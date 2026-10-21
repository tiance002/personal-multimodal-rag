"""One Owner-authorized normal tenant1 API check; no ideal evidence injection.

Uses existing entrypoint, local principal, execution fence, Provider adapters and
HTTP helpers. A permanent claim and parent accounting prevent another attempt.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_runtime import environment, load_config, verify_resources
from scripts.s1_rerank_input_ab import read, write

KB = '9d3461a5-080c-4b5e-a367-c82b79d3013c'
QUERY_MODEL = 'mdl96a368bfb0c176c6e38e325c53a030ae3e40b000254c17d19a854f4495846'
QUESTION = '请写出课件中线性方程组的系数矩阵，以及增广矩阵最后一列的三个数。'


def snapshot(env):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(env['WEKNORA_PORT_DATABASE_URL'], autocommit=True, row_factory=dict_row) as c:
        c.execute('BEGIN TRANSACTION READ ONLY')
        result = {'principal': c.execute("SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'").fetchone(),
            'models': c.execute("SELECT tenant_id,subject_id,md5(roles::text) AS fingerprint FROM port_model_configuration ORDER BY tenant_id,subject_id").fetchall(),
            'unknown': c.execute("SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p WHERE outcome='UNKNOWN' ORDER BY call_id").fetchall(),
            'source': c.execute("SELECT id::text AS id,tenant_id,knowledge_base_id,file_hash,metadata->>'source_version' AS version FROM knowledges WHERE knowledge_base_id=%s AND deleted_at IS NULL ORDER BY id", (KB,)).fetchall(),
            'chunks': c.execute("SELECT c.id,md5(row_to_json(c)::text) AS fingerprint FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id WHERE k.knowledge_base_id=%s ORDER BY c.id", (KB,)).fetchall()}
        c.execute('ROLLBACK')
    return result


async def preflight(env):
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from scripts.m4_tenant332_api import _verify_runtime
    settings = DevelopmentSettings.from_environment(dict(env, WEKNORA_PORT_CLOUD_ALLOWED='false'))
    async with assemble(settings) as app:
        assert app.principal.tenant_id == 1, 'DAILY_PRINCIPAL_MISMATCH'
        public = await _verify_runtime(app, app.principal)
        assert public['roles']['cheap']['model'] == 'Qwen/Qwen3.5-4B', 'CHEAP_IDENTITY_MISMATCH'
        assert public['registry_model_ids']['cheap'] == QUERY_MODEL
        safe = {'tenant_id': app.principal.tenant_id,
            'subject_sha256': hashlib.sha256(app.principal.subject_id.encode()).hexdigest(), **public}
        return app.principal.subject_id, safe


def http_check(helper, client, output, env, request_id):
    models = client.get('/api/v1/settings/model-configuration'); models.raise_for_status()
    write(output / 'public-models.json', models.json())
    kb = client.get('/api/v1/knowledge-bases'); kb.raise_for_status()
    visible = kb.json()['data']
    assert any(r['id'] == KB for r in visible), 'KB_NOT_VISIBLE'
    write(output / 'knowledge-base.json', kb.json())
    session = client.post('/api/v1/sessions', json={'knowledge_base_scope': [KB],
        'document_scope': [], 'title': 'S1 input and source preservation check'})
    session.raise_for_status()
    sid = session.json()['data']['id']
    write(output / 'session.json', session.json())
    payload = {'content': QUESTION, 'request_id': request_id, 'knowledge_base_scope': [KB],
        'document_scope': [], 'mode': 'quick', 'reasoning_effort': 'off'}
    with (output / 'question-submitted.json').open('x', encoding='utf-8') as f:
        json.dump({'session_id': sid, 'payload': payload}, f, ensure_ascii=False, indent=2)
    events, started = [], time.monotonic()
    try:
        with (output / 'response.sse').open('w', encoding='utf-8') as trace:
            with client.stream('POST', f'/api/v1/sessions/{sid}/messages', json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    trace.write(line + '\n'); trace.flush()
                    if line.startswith('data: '):
                        event = json.loads(line[6:])
                        event['seconds_since_submit'] = time.monotonic() - started
                        events.append(event)
                        if event.get('response_type') == 'complete':
                            break
        write(output / 'events.json', events)
        write(output / 'http-timing.json', {'elapsed_seconds': time.monotonic() - started,
            'first_answer_seconds': next((e['seconds_since_submit'] for e in events
                if e.get('response_type') == 'answer' and e.get('content')), None)})
        for filename in ('history.json', 'history-refreshed.json'):
            history = client.get(f'/api/v1/sessions/{sid}/messages'); history.raise_for_status()
            write(output / filename, history.json())
    finally:
        # The helper owns only this process. Never kill a pending supplier
        # because a local observation failed. Wait for the exact durable turn.
        while True:
            final = helper.db(env, sid)
            if final['turns'] and final['turns'][0]['status'] in ('completed', 'error'):
                break
            time.sleep(2)
        write(output / 'db-final.json', final)
    return sid


def main(args):
    cfg = load_config(args.config)
    identity = asyncio.run(verify_resources(cfg), loop_factory=asyncio.SelectorEventLoop)
    env = environment(cfg, identity['redis_run_id'])
    for path in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if path and path not in sys.path: sys.path.insert(0, path)
    subject, public = asyncio.run(preflight(env), loop_factory=asyncio.SelectorEventLoop)
    before = snapshot(env)
    assert before['principal']['subject_id'] == subject and before['principal']['tenant_id'] == 1
    assert before['source'] and all(r['tenant_id'] == 1 for r in before['source'])
    parent = read(args.authorization)
    assert parent['owner_approved'] is True
    assert all(b['status'] == 'CLOSED' for b in parent['batches'])
    case_id = 'normal-tenant1-omni'
    prior = next((b for b in parent['batches'] if b['case_id'] == case_id), None)
    assert parent['status'] == 'OPEN' or args.resume_unsubmitted and parent['status'] == 'CLOSED'
    assert prior is None or args.resume_unsubmitted, 'ONE_API_QUESTION_ONLY'
    calls = sum(b['calls'] for b in parent['batches'])
    tokens = sum(b['tokens'] for b in parent['batches'])
    assert calls == 12 and tokens == 83583, 'PARENT_ACCOUNTING_UNEXPECTED'
    if not args.send:
        print(json.dumps({'status': 'PREFLIGHT_ZERO_SEND', 'identity': identity, 'models': public,
                          'source_count': len(before['source']), 'prior_calls': calls, 'prior_tokens': tokens}))
        return
    output = args.output.resolve()
    if args.resume_unsubmitted:
        # Auditable continuation of a read-only 404 before any session/question
        # or Provider admission. This never grants another question or quota.
        assert prior is not None and prior['calls'] == prior['tokens'] == 0
        assert not (output / 'question-submitted.json').exists() and not (output / 'session.json').exists()
        previous = read(output / 'result.json'); old_grant = read(output / 'execution.json')
        assert previous['status'] == 'TOOL_ERROR' and old_grant['status'] == 'CLOSED' and not old_grant['entries']
        assert not (output / 'first-failure').exists(), 'CONTINUATION_ALREADY_USED'
        archive = output / 'first-failure'; archive.mkdir()
        for path in output.iterdir():
            if path.is_file(): (archive / path.name).write_bytes(path.read_bytes())
        write(archive / 'parent.json', parent)
        request_id = (output / 'question.claimed').read_text(encoding='utf-8')
        parent['status'] = 'OPEN'
        parent['unsubmitted_tool_continuation'] = {'reason': 'Wrong read-only KB detail path returned404; no session/question/Provider',
            'same_request_id': request_id, 'extra_questions_authorized': 0, 'quota_increase': 0}
        for key in ('unused_calls_revoked', 'unused_tokens_revoked'): parent.pop(key, None)
    else:
        output.mkdir(parents=True, exist_ok=False)
        request_id = str(uuid.uuid4())
        with (output / 'question.claimed').open('x', encoding='utf-8') as f: f.write(request_id)
    allowed = [{'provider': row['provider'], 'model': row['model'],
        'model_id': public['registry_model_ids'][role],
        'capability': {'embedding': 'embedding', 'rerank': 'rerank'}.get(role, 'chat')}
        for role, row in public['roles'].items()]
    assert {m['provider'] for m in allowed} <= {'siliconflow', 'deepseek'}
    batch = {'case_id': case_id, 'status': 'DELEGATED', 'calls': 0, 'tokens': 0,
        'max_calls': min(8, parent['max_calls'] - calls), 'max_tokens': 100000, 'path': str(output)}
    assert batch['max_calls'] >= 6 and tokens + batch['max_tokens'] <= parent['max_tokens']
    if prior is None: parent['batches'].append(batch)
    else:
        prior.update(batch)
        batch = prior
    write(args.authorization, parent)
    grant = {'status': 'OPEN', 'scope': 'S1_RERANK_INPUT_20261019', 'tenant_id': 1,
        'subject_id': subject, 'allowed_kbs': [KB], 'allowed_models': allowed,
        # Normal entrypoint assigns a separate server-side UUID run_id. It is
        # not the client idempotency request_id; scope/model/tenant remain fixed.
        'allowed_request_prefixes': list('0123456789abcdef'), 'max_calls': batch['max_calls'],
        'max_tokens': batch['max_tokens'], 'capture_evidence': True, 'entries': []}
    write(output / 'execution.json', grant); write(output / 'before.json', before)
    write(output / 'preflight.json', {'identity': identity, **public})
    env.update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_EXECUTION_FILE=str(output / 'execution.json'),
        WEKNORA_PORT_RERANK_CONTEXT_ENABLED='true', WEKNORA_PORT_SOURCE_EVIDENCE_MODE='bounded_source_v1',
        WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID=QUERY_MODEL,
        PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    spec = importlib.util.spec_from_file_location('existing_http_helpers', args.helpers)
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    result = {'status': 'STARTED', 'request_id': request_id, 'worker_started': False,
        'rerank_context_enabled': True, 'source_evidence_mode': 'bounded_source_v1',
        'normal_entrypoint': True, 'manual_evidence_injection': False}
    try:
        with helper.daily_api(env, output) as (client, _, _):
            result['session_id'] = http_check(helper, client, output, env, request_id)
        events = read(output / 'events.json')
        complete = next((e for e in reversed(events) if e.get('response_type') == 'complete'), {})
        from scripts.m4_result_contract import execution_status
        final = read(output / 'db-final.json')
        assistant = next((m for m in final['messages'] if m.get('role') == 'assistant'), None)
        result['status'] = execution_status(terminal_status=complete.get('data', {}).get('status'),
            assistant=assistant, event_count=len(events),
            unknown_call_count=sum(c.get('outcome') == 'UNKNOWN' for c in final['calls']))
    except BaseException as exc:
        result.update(status='TOOL_ERROR', error_type=type(exc).__name__)
        raise
    finally:
        ledger = read(output / 'execution.json')
        batch.update(calls=len(ledger['entries']), tokens=sum(e['held_tokens'] for e in ledger['entries']))
        safe = all(e['status'] == 'SETTLED' for e in ledger['entries'])
        ledger['status'] = batch['status'] = 'CLOSED' if safe else 'STOP'
        if safe:
            ledger.update(unused_calls_revoked=ledger['max_calls'] - batch['calls'],
                unused_tokens_revoked=ledger['max_tokens'] - batch['tokens'])
        write(output / 'execution.json', ledger)
        after = snapshot(env); write(output / 'after.json', after)
        unchanged = before == after
        result.update(accounting=batch, protected_source_models_unknown_unchanged=unchanged)
        parent['status'] = 'CLOSED' if safe and unchanged else 'STOP'
        parent.update(total_calls=sum(b['calls'] for b in parent['batches']),
            total_held_tokens=sum(b['tokens'] for b in parent['batches']))
        if parent['status'] == 'CLOSED':
            parent.update(unused_calls_revoked=parent['max_calls'] - parent['total_calls'],
                unused_tokens_revoked=parent['max_tokens'] - parent['total_held_tokens'])
        write(args.authorization, parent); write(output / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('config', 'authorization', 'output', 'helpers'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--resume-unsubmitted', action='store_true')
    main(parser.parse_args())
