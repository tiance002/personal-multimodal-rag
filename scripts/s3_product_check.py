"""One-shot S3 upload/readback helper for the normal local API, never a mock API.

The owner approved ordinary cloud mode. This tool is not a hard budget gate.
Permanent local claims prevent accidental repeated upload/question submission.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUN = Path(r'E:\WK\run\s3-product-20261019')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _json_mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def validate_upload_snapshot(result, document_id, *, worker_active):
    """Allow one scoped in-flight UNKNOWN while its owned upload is processing.

    ``UNKNOWN`` is also the send ledger's initial in-flight value. It is only
    treated as in-flight when the unique active document and image task match
    this upload/version, the worker is still owned, and the task lease is live.
    A finished UNKNOWN, terminal/unknown task, stale lease, or scope mismatch
    remains a hard stop.
    """
    if not result.get('historical_calls_unchanged'):
        raise RuntimeError('HISTORICAL_CALL_CHANGED_STOP')

    new_calls = result.get('calls', [])
    unknown = [call for call in new_calls if str(call.get('outcome', '')).upper() == 'UNKNOWN']
    abnormal = [call for call in new_calls
                if str(call.get('outcome', '')).upper() not in {'RESPONSE_RECEIVED', 'NOT_SENT', 'UNKNOWN'}]
    if abnormal:
        raise RuntimeError('PROVIDER_COMPLETION_ABNORMAL_STOP')
    not_sent = [call for call in new_calls
                if str(call.get('outcome', '')).upper() == 'NOT_SENT']
    if not_sent:
        raise RuntimeError('PROVIDER_COMPLETION_ABNORMAL_STOP')

    docs = result.get('documents', [])
    tasks = result.get('tasks', [])
    target_docs = [doc for doc in docs if doc.get('id') == document_id]
    if len(target_docs) != 1:
        raise RuntimeError('UPLOAD_DOCUMENT_SCOPE_MISMATCH_STOP')
    document = target_docs[0]
    metadata = _json_mapping(document.get('metadata'))
    version = metadata.get('source_version')
    if not isinstance(version, str) or not version:
        raise RuntimeError('UPLOAD_DOCUMENT_VERSION_MISSING_STOP')
    target_tasks = [task for task in tasks
                    if task.get('knowledge_id') == document_id
                    and task.get('source_version') == version]
    if len(target_tasks) != 1:
        if unknown:
            raise RuntimeError('UPLOAD_TASK_SCOPE_MISMATCH_STOP')
        return 'settled'
    task = target_tasks[0]
    task_state = str(task.get('state', '')).lower()
    if task_state == 'unknown':
        raise RuntimeError('IMAGE_TASK_UNKNOWN_STOP')
    if task_state in {'failed', 'cancelled', 'orphaned'}:
        raise RuntimeError('IMAGE_TASK_TERMINAL_FAILURE_STOP')

    if not unknown:
        return 'settled'
    if len(unknown) != 1:
        raise RuntimeError('MULTIPLE_UNKNOWN_CALLS_STOP')

    active_docs = [doc for doc in docs if str(doc.get('parse_status', '')).lower() in {'pending', 'processing'}]
    active_tasks = [item for item in tasks if str(item.get('state', '')).lower() == 'processing']
    observed_at = result.get('observed_at')
    lease_until = task.get('lease_until')
    if (not worker_active or len(active_docs) != 1 or active_docs[0].get('id') != document_id
            or str(document.get('parse_status', '')).lower() != 'processing'
            or len(active_tasks) != 1 or active_tasks[0].get('id') != task.get('id')
            or task_state != 'processing' or not task.get('claim_token')
            or observed_at is None or lease_until is None or lease_until <= observed_at):
        raise RuntimeError('UNRESOLVED_PROVIDER_UNKNOWN_STOP')

    call = unknown[0]
    if (call.get('finished_at') is not None
            or call.get('tenant_id') != task.get('tenant_id')
            or call.get('subject_id') != task.get('subject_id')
            or not isinstance(call.get('request_id'), str)
            or not call['request_id'].startswith(version + '/')):
        raise RuntimeError('UNRESOLVED_PROVIDER_UNKNOWN_STOP')
    return 'in_flight'


def validate_before_upload(result):
    """Block a new send if this run has any call not fully settled."""
    if not result.get('historical_calls_unchanged'):
        raise RuntimeError('HISTORICAL_CALL_CHANGED_STOP')
    outcomes = [str(call.get('outcome', '')).upper() for call in result.get('calls', [])]
    if any(outcome != 'RESPONSE_RECEIVED' for outcome in outcomes):
        raise RuntimeError('UNSETTLED_PROVIDER_CALL_STOP')


def _worker_is_active():
    processes = read(RUN / 'runtime' / 'processes.json').get('processes', {})
    saved = processes.get('worker')
    if not saved:
        return False
    from scripts.local_runtime import process_identity, owns_process
    return owns_process(saved, process_identity(saved['pid']))


def snapshot():
    from scripts.local_runtime import load_config, environment, verify_resources
    cfg = load_config(RUN / 'local.json')
    identity = asyncio.run(verify_resources(cfg), loop_factory=asyncio.SelectorEventLoop)
    env = environment(cfg, identity['redis_run_id'])
    for path in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if path: sys.path.insert(0, path)
    import psycopg
    from psycopg.rows import dict_row
    kb = read(RUN / 'kb-created.json')['data']['id']
    with psycopg.connect(env['WEKNORA_PORT_DATABASE_URL'], autocommit=True, row_factory=dict_row) as c:
        c.execute('BEGIN TRANSACTION READ ONLY')
        old = {r['call_id']: r['fingerprint'] for r in read(RUN / 'preflight.json')['calls_before']}
        calls = c.execute('SELECT p.*,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p ORDER BY created_at,call_id').fetchall()
        history_unchanged = all(next((r['fingerprint'] for r in calls if r['call_id'] == k), None) == v for k,v in old.items())
        new = [r for r in calls if r['call_id'] not in old]
        observed_at = c.execute('SELECT clock_timestamp() AS observed_at').fetchone()['observed_at']
        result = {'identity': identity, 'observed_at': observed_at,
            'calls': new, 'historical_calls_unchanged': history_unchanged,
            'documents': c.execute('SELECT * FROM knowledges WHERE knowledge_base_id=%s ORDER BY created_at,id', (kb,)).fetchall(),
            'chunks': c.execute('SELECT c.* FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id WHERE k.knowledge_base_id=%s ORDER BY c.created_at,c.id', (kb,)).fetchall(),
            'tasks': c.execute('SELECT * FROM port_image_tasks WHERE knowledge_base_id=%s ORDER BY created_at,id', (kb,)).fetchall(),
            'turns': c.execute('SELECT * FROM port_turns WHERE knowledge_base_scope @> %s::jsonb ORDER BY created_at', (json.dumps([kb]),)).fetchall()}
        c.execute('ROLLBACK')
    write(RUN / 'db-current.json', result)
    if not history_unchanged: raise RuntimeError('HISTORICAL_CALL_CHANGED_STOP')
    return result


def main(args):
    import httpx
    if args.action == 'snapshot':
        result = snapshot()
        print(json.dumps({'documents': len(result['documents']), 'chunks': len(result['chunks']), 'tasks': len(result['tasks']), 'new_calls': len(result['calls'])}))
        return
    authorization = read(RUN / 'owner-authorization.json')
    assert authorization['owner_approved'] and authorization['status'] == 'OPEN'
    kb = read(RUN / 'kb-created.json')['data']['id']
    with httpx.Client(base_url='http://127.0.0.1:56425', headers={'Origin': 'http://127.0.0.1:56426'}, timeout=600) as client:
        assert client.get('/api/v1/runtime').json()['data']['implementation'] == 'weknora-python-port'
        source = next(r for r in read(RUN / 'samples/source_manifest.json')['files'] if r['id'] == args.case)
        path = RUN / 'samples' / source['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source['sha256']
        assert client.get('/readyz').json()['cloud_allowed'] is True
        validate_before_upload(snapshot())
        with (RUN / (args.case + '.upload.claim')).open('x', encoding='utf-8') as claim:
            claim.write(source['sha256'])
        started = time.monotonic()
        with path.open('rb') as file:
            response = client.post(f'/api/v1/knowledge-bases/{kb}/documents', files={'file': (path.name, file)})
        write(RUN / (args.case + '.upload.json'), {'http_status': response.status_code, 'body': response.json(), 'seconds': time.monotonic()-started})
        response.raise_for_status()
        document = response.json()['data']
        states = []
        while True:
            response = client.get(f'/api/v1/knowledge-bases/{kb}/documents')
            response.raise_for_status()
            row = next(r for r in response.json()['data'] if r['id'] == document['id'])
            if not states or states[-1]['status'] != row['status']:
                states.append({'status': row['status'], 'error_code': row.get('error_code'), 'seconds': time.monotonic()-started})
                write(RUN / (args.case + '.states.json'), states)
            current = snapshot()
            worker_active = _worker_is_active()
            progress = validate_upload_snapshot(current, document['id'], worker_active=worker_active)
            if row['status'] not in ('pending', 'processing'): break
            if not worker_active:
                raise RuntimeError('WORKER_NOT_ACTIVE_STOP')
            if time.monotonic()-started > 900: raise RuntimeError('DOCUMENT_PENDING_TIMEOUT_NO_REUPLOAD')
            time.sleep(2)
        print(json.dumps({'document_id': document['id'], 'status': row['status'], 'states': states, 'calls_so_far': len(current['calls'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('upload', 'snapshot'))
    parser.add_argument('--case')
    main(parser.parse_args())
