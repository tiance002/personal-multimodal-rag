"""Explicit, resumable real HTTP P8 sample runner; never retries uncertain sends.

Selection is supplied by the owner/coordinator. No sampling, tuning, mocks, or
DeepSeek configuration/calls occur here. --help and importing perform no I/O.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('E:/WK/run/p8-20261016')
BASE = 'http://127.0.0.1:56427'
ORIGIN = 'http://127.0.0.1:56428'
sys.path.insert(0, str(ROOT))
from scripts.weknora_port_mvp_real import connection, credential, dsn


def save(name, value):
    target = OUT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(target)


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8-sig'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def key(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def validate_selection(selected):
    assert isinstance(selected.get('datasets'), list) and selected['datasets'], 'DATASETS_REQUIRED'
    names = set()
    for dataset in selected['datasets']:
        assert dataset['name'] not in names, 'DUPLICATE_DATASET_NAME'
        names.add(dataset['name'])
        sources, questions = set(), set()
        for item in dataset['files']:
            assert item['source_id'] not in sources, 'DUPLICATE_SOURCE_ID'
            sources.add(item['source_id'])
            assert re.fullmatch('[0-9a-fA-F]{64}', item['sha256']), 'SHA256_REQUIRED'
            assert Path(item['path']).is_absolute(), 'ABSOLUTE_SOURCE_PATH_REQUIRED'
            assert item['file_name'] and item['media_type'], 'SOURCE_METADATA_REQUIRED'
        for case in dataset['cases']:
            assert case['qid'] not in questions and case['question'], 'QUESTION_IDENTITY_REQUIRED'
            questions.add(case['qid'])
    return selected


def selection():
    selected = validate_selection(read('selection.json'))
    s = read('state.json')
    assert hashlib.sha256((OUT / 'selection.json').read_bytes()).hexdigest() == s['selection_sha256'], 'FROZEN_SELECTION_CHANGED'
    return selected


def begin(operation, metadata=None):
    """Persist intent before *every* HTTP/PG request; refuse all prior attempts.

    STARTED/UNKNOWN operations require manual reconciliation, never replay. Reads
    use unique operation IDs so an explicit later snapshot can safely read again.
    """
    attempts = read('attempts.json') if (OUT / 'attempts.json').exists() else {}
    assert operation not in attempts, 'ALREADY_ATTEMPTED_NO_AUTOMATIC_RESEND:' + operation
    attempts[operation] = {'state': 'STARTED', 'at': stamp(), 'metadata': metadata or {}}
    save('attempts.json', attempts)


def finish(operation, status, **facts):
    attempts = read('attempts.json')
    attempts[operation].update(state=status, finished_at=stamp(), **facts)
    save('attempts.json', attempts)


async def request(client, operation, method, path, *, binary=False, **kwargs):
    begin(operation, {'method': method, 'path': path})
    try:
        response = await client.request(method, path, **kwargs)
    except BaseException as exc:
        finish(operation, 'UNKNOWN', error_type=type(exc).__name__)
        raise
    # Never persist request bodies or headers; model config contains a secret.
    if response.status_code != 200:
        finish(operation, 'HTTP_FAILED', status_code=response.status_code)
        raise RuntimeError('HTTP_' + str(response.status_code) + ':' + operation)
    finish(operation, 'RECEIVED', status_code=response.status_code,
           response_sha256=hashlib.sha256(response.content).hexdigest())
    return response if binary else response.json()


def client():
    import httpx
    return httpx.AsyncClient(base_url=BASE, headers={'Origin': ORIGIN}, trust_env=False, timeout=600)


async def snapshot(label):
    assert not (OUT / ('deepseek-' + label + '.json')).exists(), 'SNAPSHOT_LABEL_EXISTS_USE_NEW_LABEL'
    op = 'snapshot-' + key(label, stamp(), str(uuid.uuid4()))
    begin(op, {'label': label, 'access': 'read-only'})
    conn = await connection()
    try:
        ds = {}
        for name, table, order in [('months', 'port_token_budget_months', 'month,provider'),
                                   ('calls', 'port_token_calls', 'call_id')]:
            ds[name] = await (await conn.execute(f"SELECT * FROM {table} WHERE account_id='deepseek-owner-main' ORDER BY {order}")).fetchall()
        save('deepseek-' + label + '.json', ds)
        if (OUT / 'state.json').exists():
            s = read('state.json')
            ledger = await (await conn.execute('SELECT * FROM port_token_calls WHERE tenant_id=%s ORDER BY call_id', (s['tenant'],))).fetchall()
            save('ledger-' + label + '.json', ledger)
            months = await (await conn.execute("SELECT * FROM port_token_budget_months WHERE account_id='siliconflow-owner-main' ORDER BY month,provider")).fetchall()
            save('siliconflow-months-' + label + '.json', months)
            docs = await (await conn.execute('SELECT * FROM knowledges WHERE tenant_id=%s ORDER BY id', (s['tenant'],))).fetchall()
            save('documents-' + label + '.json', docs)
            chunks = await (await conn.execute('SELECT * FROM chunks WHERE tenant_id=%s ORDER BY knowledge_id,chunk_index,id', (s['tenant'],))).fetchall()
            save('chunks-' + label + '.json', chunks)
            tasks = await (await conn.execute('SELECT * FROM port_image_tasks WHERE tenant_id=%s ORDER BY id', (s['tenant'],))).fetchall()
            save('image-tasks-' + label + '.json', tasks)
        if (OUT / 'deepseek-before.json').exists() and label != 'before':
            unchanged = read('deepseek-before.json') == json.loads(json.dumps(ds, default=str))
            save('deepseek-invariance-' + label + '.json', {'unchanged': unchanged, 'at': stamp()})
            assert unchanged, 'DEEPSEEK_HISTORY_CHANGED'
        finish(op, 'COMPLETED')
    except BaseException as exc:
        finish(op, 'UNKNOWN', error_type=type(exc).__name__)
        raise
    finally:
        await conn.close()


async def prepare():
    assert not (OUT / 'state.json').exists(), 'EXISTING_RUN'
    assert not (OUT / 'attempts.json').exists(), 'PREPARE_ALREADY_ATTEMPTED_REQUIRES_RECONCILIATION'
    OUT.mkdir(parents=True, exist_ok=True)
    # Do not create resources until all selected input hashes are verified.
    frozen = (OUT / 'selection.json').read_bytes()
    selected = validate_selection(json.loads(frozen.decode('utf-8-sig')))
    for dataset in selected['datasets']:
        for item in dataset['files']:
            assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256'].lower(), 'SOURCE_HASH_MISMATCH'
    await snapshot('before')
    from redis.asyncio import Redis
    op = 'prepare-redis-identity'; begin(op)
    redis = Redis.from_url('redis://127.0.0.1:56424/15', decode_responses=True)
    try:
        rid = (await redis.info('server'))['run_id']
        finish(op, 'COMPLETED')
    finally:
        await redis.aclose()
    begin('prepare-tenant')
    conn = await connection()
    try:
        tenant = (await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                   ('P8-six-dataset-sample-20261016', 'isolated-test'))).fetchone())['id']
        # Record result immediately: a crash thereafter must never create another tenant.
        finish('prepare-tenant', 'COMPLETED', tenant=tenant)
    finally:
        await conn.close()
    tag = uuid.uuid4().hex
    s = {'tenant': tenant, 'subject': 'p8-real-' + tag, 'redis_run_id': rid,
         'redis_prefix': 'weknora-python-p8:' + tag, 'selection_sha256': hashlib.sha256(frozen).hexdigest(),
         'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
         'datasets': {}}
    save('state.json', s)
    selection()
    (OUT / 'master.key').write_bytes(os.urandom(32))
    meter = ROOT / 'backend/weknora_port/source_facts.py'
    save('authority.json', {'schema': 'weknora-token-authority/v1', 'mode': 'approved',
         'tenant_id': tenant, 'subject_id': s['subject'], 'account_id': 'siliconflow-owner-main',
         'provider': 'siliconflow', 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
         'approval_source': 'OWNER:20261016-unrestricted-siliconflow-MVP', 'month': '2026-10-01',
         'carry_in_tokens': 14, 'carry_status': 'KNOWN', 'carry_source': 'LEDGER:real/embedding-0:14-preserved-original-bucket',
         'allowed_models': [['siliconflow', m] for m in ('BAAI/bge-m3', 'BAAI/bge-reranker-v2-m3', 'XingChenAGI/Xing4.0-29B')],
         'input_limit': 200000, 'output_limit': 8192, 'limit_tokens': 1000000,
         'meter': {'recipe': 'provider-completion-cap/v2', 'path': str(meter), 'sha256': hashlib.sha256(meter.read_bytes()).hexdigest()}})
    save('execution-fence.json', {'limit_tokens': 1000000, 'meaning': 'Additional local execution fence; not an Owner SiliconFlow quota.'})
    print('PREPARED', tenant)


def start():
    s = read('state.json'); selection()
    for port in (56427, 56428):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0, 'PORT_OCCUPIED:' + str(port)
    env = dict(os.environ)
    env.update(WEKNORA_PORT_DATABASE_URL=dsn(), WEKNORA_PORT_DATABASE_SYSTEM_ID='7696088403504697382',
        WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013', WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',
        WEKNORA_PORT_REDIS_RUN_ID=s['redis_run_id'], WEKNORA_PORT_REDIS_PREFIX=s['redis_prefix'],
        WEKNORA_PORT_STORAGE_ROOT=str(OUT / 'assets'), WEKNORA_PORT_SECRET_KEY_FILE=str(OUT / 'master.key'),
        WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',
        WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY', WEKNORA_PORT_FALLBACK_STRATEGY='fixed', WEKNORA_PORT_ORIGINS=ORIGIN,
        RAG_TESSDATA_DIR='E:/RAG quention/var/tessdata',
        PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', TMP=str(OUT / 'tmp'), TEMP=str(OUT / 'tmp'))
    (OUT / 'tmp').mkdir(exist_ok=True)
    argv = [sys.executable, '-B', '-m', 'scripts.weknora_port_p8_observed_api', '--port', '56427',
            '--authority-config', str(OUT / 'authority.json'), '--evidence-dir', str(OUT)]
    begin('start', {'argv': argv, 'ports_checked_free': [56427, 56428]})
    with (OUT / 'api.stdout').open('ab') as stdout, (OUT / 'api.stderr').open('ab') as stderr:
        proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
    s['api_pid'] = proc.pid; save('state.json', s); save('api-command.json', argv)
    # The Windows venv launcher can own a second interpreter process. Capture
    # only matching descendants, retaining ancestry without exposing other apps.
    for _ in range(10):
        processes = owned_api_processes(s)
        if any(row['ParentProcessId'] == proc.pid for row in processes): break
        if proc.poll() is not None: break
        time.sleep(.1)
    s['api_processes'] = processes; save('state.json', s)
    finish('start', 'STARTED_PROCESS', pid=proc.pid)
    worker_argv = [sys.executable, '-B', '-m', 'backend.weknora_port.worker',
                   '--authority-config', str(OUT / 'authority.json')]
    begin('start-worker', {'argv': worker_argv})
    with (OUT / 'worker.stdout').open('ab') as stdout, (OUT / 'worker.stderr').open('ab') as stderr:
        worker = subprocess.Popen(worker_argv, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
    s['worker_pid'] = worker.pid; save('state.json', s); save('worker-command.json', worker_argv)
    for _ in range(10):
        processes = owned_api_processes(s, kind='worker')
        if any(row['ParentProcessId'] == worker.pid for row in processes): break
        if worker.poll() is not None: break
        time.sleep(.1)
    s['worker_processes'] = processes; save('state.json', s)
    finish('start-worker', 'STARTED_PROCESS', pid=worker.pid)
    print('API_STARTED', proc.pid)
    print('WORKER_STARTED', worker.pid)


async def configure():
    s = read('state.json'); selected = selection()
    async with client() as http:
        save('ready.json', await request(http, 'ready-' + key(stamp()), 'GET', '/readyz'))
        attempts = read('attempts.json')
        if 'configure-models' not in attempts:
            roles = []
            for role, model in [('embedding', 'BAAI/bge-m3'), ('rerank', 'BAAI/bge-reranker-v2-m3'),
                                ('cheap', 'XingChenAGI/Xing4.0-29B'), ('expensive', ''), ('vlm', '')]:
                roles.append({'role': role, 'provider': 'siliconflow' if model else '', 'model': model,
                    'source': 'remote' if model else '', 'base_url': 'https://api.siliconflow.cn/v1' if model else '',
                    'api_key': credential() if model else ''})
            result = await request(http, 'configure-models', 'PUT', '/api/v1/settings/model-configuration', json={'revision': '0', 'roles': roles})
            save('model-configuration-public.json', result)
        else:
            assert attempts['configure-models']['state'] == 'RECEIVED' and (OUT / 'model-configuration-public.json').exists(), 'CONFIGURATION_REQUIRES_RECONCILIATION'
        for dataset in selected['datasets']:
            name = dataset['name']; entry = s['datasets'].setdefault(name, {})
            if not entry.get('kb'):
                result = await request(http, 'kb-' + key(name), 'POST', '/api/v1/knowledge-bases', json={'name': 'P8 ' + name})
                entry['kb'] = result['data']['id']; save('state.json', s)
            if not entry.get('consent'):
                await request(http, 'consent-' + key(name), 'PATCH', f"/api/v1/knowledge-bases/{entry['kb']}/external-processing", json={'cloud_allowed': True})
                entry['consent'] = True; save('state.json', s)
            if name.lower() == 'omnidocbench' and not entry.get('image_processing_configured'):
                config = await request(http, 'image-config-' + key(name), 'PATCH',
                    f"/api/v1/knowledge-bases/{entry['kb']}/image-processing",
                    json={'enable_multimodal': True, 'ocr_adapter': 'tesseract', 'caption_mode': 'follow_ocr'})
                save('image-config-' + key(name) + '.json', config)
                entry['image_processing_configured'] = True; save('state.json', s)
    print('CONFIGURED', len(s['datasets']))


async def upload(dataset_filter):
    s = read('state.json')
    async with client() as http:
        for dataset in selection()['datasets']:
            name = dataset['name']
            if dataset_filter and dataset_filter != name: continue
            entry = s['datasets'][name]; kb = entry['kb']
            for item in dataset['files']:
                op = 'upload-' + key(name, item['source_id'])
                documents = entry.setdefault('documents', {})
                if item['source_id'] not in documents and op in read('attempts.json'): continue
                try:
                    data = Path(item['path']).read_bytes()
                    assert hashlib.sha256(data).hexdigest() == item['sha256'].lower(), 'SOURCE_HASH_MISMATCH'
                    if item['source_id'] not in documents:
                        response = await request(http, op, 'POST', f'/api/v1/knowledge-bases/{kb}/documents',
                            files={'file': (item['file_name'], data, item['media_type'])})
                        save('uploads/' + key(name, item['source_id']) + '.json', response)
                        row = response['data']; documents[item['source_id']] = {'id': row['id'], 'status': row['status'], 'source': item}
                        save('state.json', s)
                    row = documents[item['source_id']]
                    source = await request(http, 'source-' + key(name, item['source_id'], stamp()), 'GET',
                        f"/api/v1/knowledge-bases/{kb}/documents/{row['id']}/source", binary=True)
                    digest = hashlib.sha256(source.content).hexdigest()
                    save('sources/' + key(name, item['source_id']) + '.json', {'document_id': row['id'],
                        'source_id': item['source_id'], 'sha256': digest, 'bytes': len(source.content), 'expected_sha256': item['sha256'],
                        'matches': digest == item['sha256'].lower()})
                    assert digest == item['sha256'].lower(), 'SOURCE_READBACK_HASH_MISMATCH'
                except Exception as exc:
                    save('failures/' + op + '.json', {'operation': op, 'error_type': type(exc).__name__, 'error': str(exc), 'at': stamp()})
            try:
                deadline = time.monotonic() + 300
                previous = None
                while True:
                    statuses = await request(http, 'document-status-' + key(name, stamp()), 'GET', f'/api/v1/knowledge-bases/{kb}/documents')
                    if statuses != previous:
                        save('document-status/' + key(name, stamp()) + '.json', statuses)
                        previous = statuses
                    current = {row['id']: row for row in statuses['data']}
                    for doc in entry.get('documents', {}).values():
                        if doc['id'] in current: doc['status'] = current[doc['id']]['status']
                    save('state.json', s)
                    pending = [doc for doc in entry.get('documents', {}).values() if doc['status'] in ('pending', 'processing')]
                    if not pending: break
                    if time.monotonic() >= deadline:
                        save('failures/poll-' + key(name, stamp()) + '.json', {'error': 'DOCUMENT_STATUS_POLL_TIMEOUT', 'pending': pending})
                        break
                    await asyncio.sleep(1)
            except Exception as exc:
                save('failures/status-' + key(name, stamp()) + '.json', {'error_type': type(exc).__name__, 'error': str(exc)})
    print('UPLOAD_STAGE_COLLECTED; inspect failures and document status')


async def query(dataset_filter, qid_filter):
    s = read('state.json')
    async with client() as http:
        for dataset in selection()['datasets']:
            name = dataset['name']
            if dataset_filter and dataset_filter != name: continue
            entry = s['datasets'][name]; kb = entry['kb']
            docs = [row['id'] for row in entry.get('documents', {}).values() if row['status'] == 'completed']
            if not docs:
                save('failures/query-' + key(name) + '.json', {'error': 'NO_COMPLETED_DOCUMENTS', 'dataset': name}); continue
            for case in dataset['cases']:
                if qid_filter and case['qid'] != qid_filter: continue
                ident = key(name, case['qid']); prefix = 'queries/' + ident
                (OUT / 'queries').mkdir(exist_ok=True)
                records = entry.setdefault('queries', {})
                record = records.setdefault(case['qid'], {'request_id': str(uuid.uuid4()), 'question': case['question']})
                save('state.json', s)
                try:
                    if not record.get('session'):
                        session = await request(http, 'session-' + ident, 'POST', '/api/v1/sessions',
                            json={'title': 'P8 ' + name + ' ' + case['qid'], 'knowledge_base_scope': [kb], 'document_scope': docs})
                        record['session'] = session['data']['id']; save('state.json', s)
                    op = 'query-' + ident
                    if op not in read('attempts.json'):
                        response = await request(http, op, 'POST', f"/api/v1/sessions/{record['session']}/messages", binary=True,
                            json={'content': case['question'], 'request_id': record['request_id'], 'knowledge_base_scope': [kb],
                                  'document_scope': docs, 'reasoning_effort': 'off'})
                        (OUT / (prefix + '.sse')).write_bytes(response.content)
                    messages = await request(http, 'messages-' + key(ident, stamp()), 'GET', f"/api/v1/sessions/{record['session']}/messages")
                    save(prefix + '-messages.json', messages)
                    refs = [ref for msg in messages['data'] if msg['role'] == 'assistant' for ref in (msg.get('knowledge_references') or [])]
                    save(prefix + '-references.json', refs)
                    sources = []
                    for ref in refs:
                        chunk = await request(http, 'chunk-' + key(ident, ref['id'], stamp()), 'GET', '/api/v1/chunks/' + ref['id'])
                        sources.append(chunk)
                        assert chunk['data']['knowledge_base_id'] == kb and chunk['data']['document_id'] in docs, 'CITATION_SCOPE_MISMATCH'
                    save(prefix + '-chunkreadback.json', sources)
                    save(prefix + '-identity.json', {'dataset': name, 'qid': case['qid'], 'kb': kb, 'documents': docs, **record})
                except Exception as exc:
                    save('failures/query-' + ident + '.json', {'error_type': type(exc).__name__, 'error': str(exc), 'at': stamp()})
    print('QUERY_STAGE_COLLECTED; inspect SSE, messages, references, observer rankings and failures')


def owned_api_processes(s, kind='api'):
    """Return only this run's command-verified parent/descendant process tree."""
    pid = int(s[kind + '_pid'])
    module = 'scripts.weknora_port_p8_observed_api' if kind == 'api' else 'backend.weknora_port.worker'
    script = ("@(Get-CimInstance Win32_Process | Where-Object { "
              "$_.CommandLine -like '*" + module + "*' } | "
              "Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress")
    raw = subprocess.check_output(['powershell', '-NoProfile', '-Command', script], text=True).strip()
    rows = json.loads(raw) if raw else []
    if isinstance(rows, dict): rows = [rows]
    authority = str(OUT / 'authority.json').replace('\\', '/').lower()
    matches = [row for row in rows if authority in (row['CommandLine'] or '').replace('\\', '/').lower()]
    # The original parent may have exited while its child still retains the
    # original ParentProcessId. Recorded IDs are accepted only with current
    # matching module + authority command lines, protecting against PID reuse.
    reachable = {pid} | {int(row['ProcessId']) for row in s.get(kind + '_processes', [])}
    found = {}
    changed = True
    while changed:
        changed = False
        for row in matches:
            current = int(row['ProcessId'])
            if current in reachable or int(row['ParentProcessId']) in reachable:
                if current not in found:
                    found[current] = row; reachable.add(current); changed = True
    return list(found.values())


def stop():
    for kind in ('worker', 'api'):
        s = read('state.json')
        if s.get(kind + '_pid'): stop_process(s, kind)


def stop_process(s, kind):
    pid = s.get(kind + '_pid')
    assert pid, 'NO_OWNED_API_PID'
    processes = owned_api_processes(s, kind)
    if not processes:
        print('ALREADY_STOPPED_OR_NO_COMMAND_VERIFIED_OWNED_PROCESS'); return
    s[kind + '_processes_at_stop'] = processes; save('state.json', s)
    operation = 'stop-' + kind + '-' + key(stamp())
    begin(operation, {'pids': [row['ProcessId'] for row in processes]})
    # Re-read and verify identity immediately before each stop; children first.
    for row in sorted(processes, key=lambda row: int(row['ProcessId']) == int(pid)):
        current = {int(item['ProcessId']): item for item in owned_api_processes(s, kind)}
        target = int(row['ProcessId'])
        if target not in current: continue
        subprocess.run(['powershell', '-NoProfile', '-Command',
            f'if (Get-Process -Id {target} -ErrorAction SilentlyContinue) {{ Stop-Process -Id {target} -ErrorAction Stop }}'], check=True)
    remaining = owned_api_processes(s, kind)
    finish(operation, 'COMPLETED' if not remaining else 'UNKNOWN', remaining=remaining)
    assert not remaining, 'OWNED_API_PROCESS_REMAINS'
    s[kind + '_stopped_at'] = stamp(); save('state.json', s)
    print('STOPPED', kind, pid)


async def main(args):
    if args.stage == 'prepare': await prepare()
    elif args.stage == 'start': start()
    elif args.stage == 'configure': await configure()
    elif args.stage == 'upload': await upload(args.dataset)
    elif args.stage == 'query': await query(args.dataset, args.qid)
    elif args.stage == 'snapshot': await snapshot(args.label)
    elif args.stage == 'stop': stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'start', 'configure', 'upload', 'query', 'snapshot', 'stop'])
    parser.add_argument('--dataset', help='Exact frozen dataset name (upload/query only)')
    parser.add_argument('--qid', help='Exact frozen question ID (query only)')
    parser.add_argument('--label', default='after', help='Snapshot artifact label')
    args = parser.parse_args()
    assert re.fullmatch('[A-Za-z0-9_-]+', args.label), 'INVALID_SNAPSHOT_LABEL'
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(args))
