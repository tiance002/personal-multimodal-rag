"""One-shot S6 acceptance through the normal API, graph CLI and existing fence.

No approval is created here. ``prepare`` performs no model sends. ``run`` needs
an external current Owner grant and an empty, already-created isolated KB.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCOPE = 'S6_20261019'
KB = '63266207-95cf-42a6-8e5b-6b3ae18f0819'
QUESTION = 'Franklin bells 配合使用的装置储存的是什么电？请依据知识库回答。'
PORT = 56425
PHASE_LIMITS = ((12, 100_000), (8, 100_000))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write('\n')


def save(path, value):
    temporary = Path(path).with_name(Path(path).name + '.' + uuid.uuid4().hex + '.tmp')
    write_new(temporary, value)
    os.replace(temporary, path)


def require(condition, code):
    if not condition:
        raise ValueError(code)


def validate_authorization(grant, manifest_sha256):
    require(grant.get('scope') == SCOPE and grant.get('status') == 'OPEN'
            and grant.get('owner_approved') is True
            and type(grant.get('max_calls')) is int and grant['max_calls'] == 20
            and type(grant.get('max_tokens')) is int and grant['max_tokens'] == 200_000
            and grant.get('manifest_sha256') == manifest_sha256,
            'S6_CURRENT_EXTERNAL_OWNER_AUTHORIZATION_REQUIRED')


def authorization_state(path, manifest_sha256, *, sending):
    """Preparing does not require approval and never creates approval data."""
    try:
        grant = read(path)
        validate_authorization(grant, manifest_sha256)
        return 'AUTHORIZED'
    except (OSError, ValueError, TypeError):
        if sending:
            raise ValueError('S6_CURRENT_EXTERNAL_OWNER_AUTHORIZATION_REQUIRED') from None
        return 'BLOCKED_AUTHORIZATION_NO_SEND'


def frozen_sources(manifest):
    data = read(manifest)
    sources = data.get('sources')
    require(isinstance(sources, list) and len(sources) == 2, 'S6_TWO_FROZEN_SOURCES_REQUIRED')
    for source in sources:
        raw = Path(source['path']).read_bytes()
        require(digest(raw) == source['sha256'] and len(raw) == source['bytes']
                and raw.decode('utf-8') == source['text'], 'S6_FROZEN_SOURCE_CHANGED')
    require(len({s['sha256'] for s in sources}) == 2, 'S6_DUPLICATE_SOURCE')
    return sources


def ledger_summary(paths):
    ledgers = [read(path) for path in paths if Path(path).exists()]
    entries = [row for ledger in ledgers for row in ledger['entries']]
    return {'calls': len(entries),
            'reported_tokens': sum(row['usage']['total_tokens'] for row in entries if (row.get('usage') or {}).get('status') == 'REPORTED'),
            'held_tokens': sum(row['held_tokens'] for row in entries),
            'unknown': sum(row['status'] == 'UNKNOWN' for row in entries),
            'unsettled': sum(row['status'] != 'SETTLED' for row in entries),
            'inflight': sum(row['status'] == 'INFLIGHT' for row in entries),
            'entries': entries}


def require_settled(paths):
    summary = ledger_summary(paths)
    require(summary['unsettled'] == 0 and summary['calls'] <= 20
            and summary['held_tokens'] <= 200_000, 'S6_UNKNOWN_OR_SETTLEMENT_STOP')
    return summary


def port_is_free():
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError:
            return False
    return True


def business_headers(cfg):
    """Use the existing wk UI origin for normal API write admission."""
    return {'Origin': f"http://127.0.0.1:{cfg['ui_port']}"}


def question_body():
    # Ordinary durable admission limits client request IDs to 36 characters.
    # This identity is distinct from the server-generated Provider run IDs.
    return {'content': QUESTION, 'request_id': str(uuid.uuid4()), 'mode': 'quick',
            'knowledge_base_scope': [KB], 'document_scope': [], 'reasoning_effort': 'off'}


def same_history(left, right):
    # Correlation IDs belong to each GET, not to the persisted message data.
    return (isinstance(left, dict) and isinstance(right, dict)
            and isinstance(left.get('data'), list) and isinstance(right.get('data'), list)
            and left['data'] == right['data'])


def make_ledger(principal, models, phase):
    max_calls, max_tokens = PHASE_LIMITS[phase - 1]
    return {'status': 'OPEN', 'scope': SCOPE, 'tenant_id': principal.tenant_id,
            'subject_id': principal.subject_id, 'allowed_kbs': [KB],
            'allowed_models': models, 'allowed_request_prefixes': ['*'],
            'max_calls': max_calls, 'max_tokens': max_tokens, 'capture_evidence': True, 'entries': []}


async def inspect(args):
    from scripts.local_runtime import load_config, environment, verify_resources
    sources = frozen_sources(args.manifest)
    manifest_sha = digest(args.manifest.read_bytes())
    authorization = authorization_state(args.authorization, manifest_sha, sending=args.command == 'run')
    cfg = load_config(args.config)
    require(cfg['api_port'] == PORT, 'S6_NORMAL_API_PORT_REQUIRED')
    identity = await verify_resources(cfg)
    require(identity['tenant_id'] == 1, 'S6_DAILY_TENANT_ONE_REQUIRED')
    env = environment(cfg, identity['redis_run_id'])
    for path in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    env.pop('WEKNORA_PORT_EVIDENCE_DIR', None)
    env.pop('WEKNORA_PORT_EXECUTION_FILE', None)
    env['WEKNORA_PORT_CLOUD_ALLOWED'] = 'false'
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.models.types import CallContext, TrustedScope
    async with assemble(DevelopmentSettings.from_environment(env)) as app:
        principal = app.principal
        require(principal.tenant_id == 1, 'S6_PRINCIPAL_CHANGED')
        kb_rows = await app._rows('SELECT id,tenant_id,embedding_model_id,summary_model_id,rerank_model_id,chunking_config,image_processing_config '
                                  'FROM knowledge_bases WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL', (KB, 1))
        require(len(kb_rows) == 1, 'S6_KB_IDENTITY_MISMATCH')
        documents = await app._rows('SELECT id FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL', (1, KB))
        require(not documents, 'S6_ACCEPTANCE_KB_MUST_START_EMPTY')
        async with app.leases.lease() as guard:
            _, roles, _ = await PostgresModelRoleStore(guard, principal, app.catalogue, cipher=app.cipher).read_available()
        selected = {role.role: role for role in roles if role.role in ('embedding', 'rerank', 'cheap', 'expensive')}
        require(set(selected) == {'embedding', 'rerank', 'cheap', 'expensive'}, 'S6_EXISTING_ROLES_MISSING')
        models = []
        role_ids = {}
        registry = app.registry_for_context()
        try:
            for role_name, role in selected.items():
                mid = app.models._identity(role)[0]
                capability = role_name if role_name in ('embedding', 'rerank') else 'chat'
                require(role.provider in ('siliconflow', 'deepseek'), 'S6_UNAPPROVED_PROVIDER')
                model = await getattr(registry, capability)(mid, CallContext('s6-zero-send-preflight', TrustedScope(principal.subject_id, (KB,), ())))
                require(model.config.model_id == mid and model.config.provider == role.provider and model.config.model == role.model,
                        'S6_REGISTRY_IDENTITY_MISMATCH')
                record = {'provider': role.provider, 'model': role.model, 'model_id': mid, 'capability': capability}
                if record not in models:
                    models.append(record)
                role_ids[role_name] = mid
            require(selected['cheap'].model == 'Qwen/Qwen3.5-4B'
                    and selected['embedding'].model.lower() == 'baai/bge-m3'
                    and selected['rerank'].model == 'BAAI/bge-reranker-v2-m3', 'S6_FROZEN_MODEL_CHANGED')
            for name in ('embedding', 'rerank'):
                require(kb_rows[0][name + '_model_id'] == role_ids[name], 'S6_KB_MODEL_BINDING_MISMATCH')
            require(kb_rows[0]['summary_model_id'] == role_ids['cheap'], 'S6_SUMMARY_ROLE_MISMATCH')
            query_mid = DevelopmentSettings.from_environment(env).query_understand_model_id
            if query_mid:
                require(query_mid in role_ids.values(), 'S6_QUERY_MODEL_NOT_IN_APPROVED_EXISTING_ROLES')
        finally:
            await registry.close()
        calls_before = await app._rows('SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p ORDER BY call_id', ())
        config_before = await app._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s', (1, principal.subject_id))
        graph_tables = await app._rows("SELECT to_regclass('port_graph_evidence') AS evidence,to_regclass('port_graph_nodes') AS nodes,to_regclass('port_graph_edges') AS edges", ())
        require(len(graph_tables) == 1 and all(graph_tables[0].values()), 'S6_EXPLICIT_GRAPH_MIGRATION_REQUIRED')
    prepared = {'status': 'ZERO_SEND_PREPARED', 'authorization': authorization,
                'recommended_envelope': {'max_calls': 20, 'max_tokens': 200_000},
                'tenant_id': 1, 'subject_sha256': digest(principal.subject_id.encode()),
                'knowledge_base_id': KB, 'role_ids': role_ids, 'models': models, 'resource_identity': identity,
                'source_manifest_sha256': manifest_sha, 'config_sha256': digest(args.config.read_bytes()),
                'sources': [{k: s[k] for k in ('path', 'sha256', 'bytes', 'estimated_child_count')} for s in sources],
                'phase_limits': PHASE_LIMITS, 'cloud_calls': 0, 'historical_calls': calls_before,
                'configuration': config_before, 'question': QUESTION}
    return prepared, cfg, env, principal, sources


def phase_environment(cfg, env, ledger, config_path):
    copied = json.loads(json.dumps(cfg))
    copied['state_dir'] = str(config_path.parent / 'runtime')
    copied['environment'].pop('WEKNORA_PORT_EVIDENCE_DIR', None)
    copied['environment'].update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_GRAPH_ENABLED='true',
                                 WEKNORA_PORT_EXECUTION_FILE=str(ledger))
    write_new(config_path, copied)
    result = dict(env)
    result.update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_GRAPH_ENABLED='true', WEKNORA_PORT_EXECUTION_FILE=str(ledger))
    return result


async def start_api(args, env, phase):
    import httpx
    require(port_is_free(), 'S6_STOP_EXISTING_DAILY_API_FIRST')
    stdout = (args.output / f'phase{phase}-api.stdout.log').open('xb')
    stderr = (args.output / f'phase{phase}-api.stderr.log').open('xb')
    try:
        process = subprocess.Popen([sys.executable, '-m', 'backend.weknora_port.entrypoint', '--host', '127.0.0.1', '--port', str(PORT)],
                                   cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
    finally:
        stdout.close()
        stderr.close()
    write_new(args.output / f'phase{phase}-process.json', {'pid': process.pid, 'port': PORT, 'entrypoint': 'backend.weknora_port.entrypoint'})
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            for _ in range(60):
                require(process.poll() is None, 'S6_API_PROCESS_EXITED')
                try:
                    ready = await client.get(f'http://127.0.0.1:{PORT}/readyz')
                    if ready.status_code == 200:
                        require(ready.json().get('implementation') == 'weknora-python-port' and ready.json().get('cloud_allowed') is True,
                                'S6_API_IMPLEMENTATION_MISMATCH')
                        return process
                except httpx.RequestError:
                    pass
                await asyncio.sleep(.5)
        raise ValueError('S6_API_START_TIMEOUT')
    except BaseException:
        await stop_api(process, Path(env['WEKNORA_PORT_EXECUTION_FILE']))
        raise


def close_ledger(path):
    from backend.weknora_port.execution import LocalExecution
    execution = LocalExecution(path)
    with execution._ledger() as data:
        require(not any(row['status'] == 'INFLIGHT' for row in data['entries']), 'S6_INFLIGHT_PROCESS_MUST_NOT_BE_KILLED')
        data['status'] = 'CLOSED' if all(row['status'] == 'SETTLED' for row in data['entries']) else 'STOP'


def stop_admission(path):
    from backend.weknora_port.execution import LocalExecution
    execution = LocalExecution(path)
    with execution._ledger() as data:
        data['status'] = 'STOP'


async def stop_api(process, ledger):
    require(ledger_summary([ledger])['inflight'] == 0, 'S6_INFLIGHT_PROCESS_MUST_NOT_BE_KILLED')
    if process is not None and process.poll() is None:
        process.terminate()
        await asyncio.to_thread(process.wait, 15)


async def snapshot(env, document_ids):
    from backend.weknora_port.development import DevelopmentSettings, assemble
    local = dict(env)
    local.pop('WEKNORA_PORT_EXECUTION_FILE', None)
    local['WEKNORA_PORT_CLOUD_ALLOWED'] = 'false'
    async with assemble(DevelopmentSettings.from_environment(local)) as app:
        require(app.principal.tenant_id == 1, 'S6_READBACK_PRINCIPAL_MISMATCH')
        documents = await app._rows('SELECT id,tenant_id,knowledge_base_id,file_hash,parse_status,metadata FROM knowledges '
                                   'WHERE tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL ORDER BY id', (1, KB))
        require({d['id'] for d in documents} == set(document_ids), 'S6_DOCUMENT_READBACK_MISMATCH')
        chunks = await app._rows('SELECT id,knowledge_id,chunk_type,parent_chunk_id,content,start_at,end_at,metadata,context_header FROM chunks '
                                'WHERE tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL ORDER BY knowledge_id,chunk_index,id', (1, KB))
        vectors = await app._rows('SELECT chunk_id,knowledge_id,dimension,embedding IS NOT NULL AS has_vector FROM embeddings WHERE knowledge_base_id=%s ORDER BY id', (KB,))
        counts = {}
        for table in ('port_graph_evidence', 'port_graph_nodes', 'port_graph_edges'):
            sql = ('SELECT count(*) AS count FROM port_graph_evidence WHERE tenant_id=%s AND knowledge_base_id=%s' if table == 'port_graph_evidence'
                   else f'SELECT count(*) AS count FROM {table} g JOIN port_graph_evidence e ON e.id=g.evidence_id WHERE e.tenant_id=%s AND e.knowledge_base_id=%s')
            counts[table] = (await app._rows(sql, (1, KB)))[0]['count']
        calls = await app._rows('SELECT call_id,request_id,capability,provider,model,model_id,outcome,usage,wire_sha256 FROM port_provider_calls '
                               'WHERE tenant_id=%s AND subject_id=%s ORDER BY call_id', (1, app.principal.subject_id))
        all_fingerprints = await app._rows('SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p ORDER BY call_id', ())
        config = await app._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s', (1, app.principal.subject_id))
        return {'documents': documents, 'chunks': chunks, 'vectors': vectors, 'graph_counts': counts,
                'calls': calls, 'all_call_fingerprints': all_fingerprints, 'configuration': config}


async def run(args, prepared, cfg, env, principal, sources):
    import httpx
    validate_authorization(read(args.authorization), prepared['source_manifest_sha256'])
    require(port_is_free(), 'S6_STOP_EXISTING_DAILY_API_FIRST')
    write_new(args.output / 'run.claim', {'scope': SCOPE, 'one_shot': True, 'request_id': 's6-' + uuid.uuid4().hex})
    ledgers = []
    process = None
    result = {'status': 'STARTED', 'provider_calls': 0, 'question': QUESTION, 'documents': [], 'phases': []}
    base = f'http://127.0.0.1:{PORT}'
    try:
        for phase in (1, 2):
            if ledgers:
                require_settled(ledgers)
            validate_authorization(read(args.authorization), prepared['source_manifest_sha256'])
            phase_dir = args.output / f'phase{phase}'
            phase_dir.mkdir(exist_ok=False)
            ledger = phase_dir / 'execution.json'
            write_new(ledger, make_ledger(principal, prepared['models'], phase))
            ledgers.append(ledger)
            phase_env = phase_environment(cfg, env, ledger, phase_dir / 'local.json')
            process = await start_api(args, phase_env, phase)
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5), trust_env=False,
                                         headers=business_headers(cfg)) as client:
                response = await client.get(base + '/api/v1/knowledge-bases')
                response.raise_for_status()
                require(any(row['id'] == KB for row in response.json()['data']), 'S6_HTTP_KB_NOT_VISIBLE')
                if phase == 1:
                    consent = await client.patch(base + f'/api/v1/knowledge-bases/{KB}/external-processing', json={'cloud_allowed': True})
                    consent.raise_for_status()
                    write_new(args.output / 'consent-open.json', consent.json())
                    for index, source in enumerate(sources):
                        require_settled(ledgers)
                        raw = Path(source['path']).read_bytes()
                        require(digest(raw) == source['sha256'], 'S6_SOURCE_CHANGED_BEFORE_UPLOAD')
                        write_new(args.output / f'upload-{index}.claim', {'source_sha256': source['sha256'], 'once': True})
                        uploaded = await client.post(base + f'/api/v1/knowledge-bases/{KB}/documents',
                                                     files={'file': (Path(source['path']).name, raw, 'text/plain')})
                        write_new(args.output / f'upload-{index}.response.json', {'status_code': uploaded.status_code, 'body': uploaded.json()})
                        uploaded.raise_for_status()
                        document = uploaded.json()['data']
                        result['documents'].append(document['id'])
                        require_settled(ledgers)
                        documents = await client.get(base + f'/api/v1/knowledge-bases/{KB}/documents')
                        documents.raise_for_status()
                        rows = [row for row in documents.json()['data'] if row['id'] == document['id']]
                        require(len(rows) == 1 and rows[0]['status'] == 'completed', 'S6_UPLOAD_NOT_COMPLETED_NO_RETRY')
                    for index, document_id in enumerate(result['documents']):
                        require_settled(ledgers)
                        operation = 's6-index-' + uuid.uuid4().hex
                        output = args.output / f'graph-{index}.json'
                        command = [sys.executable, '-m', 'backend.weknora_port.graph_cli', '--config', str(phase_dir / 'local.json'),
                                   '--output', str(output), 'index', '--kb', KB, '--document', document_id,
                                   '--model-id', prepared['role_ids']['cheap'], '--request-id', operation]
                        completed = await asyncio.to_thread(subprocess.run, command, cwd=ROOT, env=phase_env, capture_output=True, text=True)
                        write_new(args.output / f'graph-{index}.command.json', {'command': command, 'exit_code': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr})
                        require_settled(ledgers)
                        require(completed.returncode == 0, 'S6_GRAPH_INDEX_FAILED_NO_RETRY')
                    write_new(args.output / 'after-index.json', await snapshot(env, result['documents']))
                else:
                    session = await client.post(base + '/api/v1/sessions', json={'title': 'S6 Graph two-hop source acceptance', 'knowledge_base_scope': [KB], 'document_scope': []})
                    session.raise_for_status()
                    sid = session.json()['data']['id']
                    body = question_body()
                    request_id = body['request_id']
                    write_new(args.output / 'question.claim.json', {'session_id': sid, 'body': body})
                    started = time.monotonic()
                    with (args.output / 'question.sse').open('xb') as handle:
                        async with client.stream('POST', base + f'/api/v1/sessions/{sid}/messages', json=body) as stream:
                            stream.raise_for_status()
                            async for block in stream.aiter_bytes():
                                handle.write(block)
                                handle.flush()
                    require_settled(ledgers)
                    histories = []
                    before_history = ledger_summary(ledgers)['calls']
                    for index in range(2):
                        history = await client.get(base + f'/api/v1/sessions/{sid}/messages')
                        history.raise_for_status()
                        histories.append(history.json())
                        write_new(args.output / f'history-{index}.json', histories[-1])
                    require(same_history(histories[0], histories[1]), 'S6_HISTORY_READBACK_CHANGED')
                    require(ledger_summary(ledgers)['calls'] == before_history, 'S6_HISTORY_CAUSED_PROVIDER_CALL')
                    result.update(session_id=sid, request_id=request_id, question_seconds=time.monotonic() - started,
                                  history_readback_equal=True)
                    final_snapshot = await snapshot(env, result['documents'])
                    write_new(args.output / 'after-question.json', final_snapshot)
                    old = {row['call_id']: row['fingerprint'] for row in prepared['historical_calls']}
                    current = {row['call_id']: row['fingerprint'] for row in final_snapshot['all_call_fingerprints']}
                    require(all(current.get(key) == value for key, value in old.items()), 'S6_HISTORICAL_LEDGER_CHANGED')
                    require(final_snapshot['configuration'] == prepared['configuration'], 'S6_DEFAULT_ROLES_CHANGED')
                    entries = ledger_summary(ledgers)['entries']
                    sent = {(row['request_id'], row['capability']) for row in entries}
                    receipts = [row for row in final_snapshot['calls'] if (row['request_id'], row['capability']) in sent]
                    require(len(receipts) == len(entries) and all(row['outcome'] == 'RESPONSE_RECEIVED'
                            and (row.get('usage') or {}).get('status') == 'REPORTED' for row in receipts),
                            'S6_DURABLE_RECEIPT_MISMATCH')
                    result.update(historical_calls_unchanged=True, default_roles_unchanged=True,
                                  fresh_provider_receipts=receipts)
                    consent = await client.patch(base + f'/api/v1/knowledge-bases/{KB}/external-processing', json={'cloud_allowed': False})
                    consent.raise_for_status()
                    write_new(args.output / 'consent-closed.json', consent.json())
            require_settled(ledgers)
            close_ledger(ledger)
            await stop_api(process, ledger)
            process = None
            result['phases'].append({'phase': phase, 'status': 'CLOSED', 'ledger': str(ledger)})
            save(args.output / 'results.json', result)
        result['status'] = 'EXECUTED_REQUIRES_EVIDENCE_REVIEW'
    except BaseException as error:
        result.update(status='INCOMPLETE_STOP', first_failure={'exception_type': type(error).__name__,
                                                             'code': str(error) if isinstance(error, ValueError) else 'S6_OPERATION_FAILED_SEE_SAVED_EVIDENCE'})
        raise
    finally:
        # Revoke only this isolated KB. Never stop a process with unresolved sends.
        summary = ledger_summary(ledgers)
        for ledger in ledgers:
            if ledger_summary([ledger])['inflight']:
                stop_admission(ledger)
        if result['documents'] and not (args.output / 'consent-closed.json').exists():
            try:
                async with httpx.AsyncClient(timeout=5, trust_env=False, headers=business_headers(cfg)) as client:
                    closed = await client.patch(base + f'/api/v1/knowledge-bases/{KB}/external-processing', json={'cloud_allowed': False})
                    closed.raise_for_status()
                    save(args.output / 'consent-closed.json', closed.json())
            except Exception:
                result['consent_close_status'] = 'UNCONFIRMED_OWNER_ACTION_REQUIRED'
        if process is not None:
            if summary['inflight'] == 0:
                await stop_api(process, ledgers[-1])
            else:
                result['process_kept_alive_due_inflight'] = process.pid
        for ledger in ledgers:
            if ledger_summary([ledger])['inflight'] == 0:
                close_ledger(ledger)
        result['usage'] = summary
        grant = read(args.authorization)
        grant.update(status='STOP' if summary['unsettled'] else 'CLOSED', actual_calls=summary['calls'],
                     actual_reported_tokens=summary['reported_tokens'], held_tokens=summary['held_tokens'],
                     unknown=summary['unknown'], unused_space_revoked=True)
        save(args.authorization, grant)
        save(args.output / 'results.json', result)


async def execute(args):
    args.output.mkdir(parents=True, exist_ok=True)
    require(args.output.is_absolute() and args.output.drive.upper() in ('D:', 'E:'), 'S6_EXTERNAL_OUTPUT_D_E_REQUIRED')
    require(not args.output.resolve().is_relative_to(ROOT.resolve()), 'S6_EXECUTION_FILES_MUST_NOT_BE_IN_REPOSITORY')
    prepared, cfg, env, principal, sources = await inspect(args)
    preflight = args.output / 'preflight.json'
    if preflight.exists():
        previous = read(preflight)
        fields = ('source_manifest_sha256', 'config_sha256', 'subject_sha256', 'role_ids', 'models', 'historical_calls', 'configuration')
        require(all(previous.get(field) == prepared[field] for field in fields), 'S6_PREPARED_IDENTITY_CHANGED')
    else:
        write_new(preflight, prepared)
    if args.command == 'prepare':
        print(json.dumps({'status': prepared['status'], 'authorization': prepared['authorization'],
                          'recommended_envelope': prepared['recommended_envelope'], 'provider_calls': 0, 'models': prepared['models']}))
        return
    await run(args, prepared, cfg, env, principal, sources)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    for name in ('config', 'authorization', 'manifest', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(execute(arguments(argv)))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED', 'exception_type': type(error).__name__,
                          'code': str(error) if isinstance(error, ValueError) else 'S6_OPERATION_FAILED_SEE_SAVED_EVIDENCE'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
