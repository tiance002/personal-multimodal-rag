"""P8.1 explicit real-run stages; reuses tenant/model/storage and preserves P8.

Import/help do not read credentials, connect, create artifacts, or start services.
Selection is frozen by the coordinator before any new model request.
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import weknora_port_p8_real as p8

OUT = Path('E:/WK/run/p8-1-20261016')
PRIOR = Path('E:/WK/run/p8-20261016')
MODULE = 'scripts.weknora_port_p8_1_observed_api'


def bind():
    p8.OUT, p8.BASE, p8.ORIGIN = OUT, 'http://127.0.0.1:56429', 'http://127.0.0.1:56430'


def trusted_authority():
    authority = json.loads((PRIOR / 'authority.json').read_text(encoding='utf-8-sig'))
    original = json.loads((PRIOR / 'state.json').read_text(encoding='utf-8-sig'))
    assert original['tenant'] == 332, 'PRIOR_TENANT_MISMATCH'
    assert authority['tenant_id'] == 332 and authority['subject_id'] == original['subject'], 'PRIOR_AUTHORITY_IDENTITY_MISMATCH'
    assert authority['account_id'] == 'siliconflow-owner-main' and authority['provider'] == 'siliconflow', 'PRIOR_PROVIDER_MISMATCH'
    assert authority['month'] == '2026-10-01' and authority['carry_in_tokens'] == 14, 'PRIOR_BUDGET_BINDING_MISMATCH'
    assert authority['approval_source'] == 'OWNER:20261016-unrestricted-siliconflow-MVP', 'PRIOR_APPROVAL_MISMATCH'
    assert datetime.fromisoformat(authority['expires_at']) > datetime.now(timezone.utc), 'AUTHORITY_EXPIRED_ROOT_RECONCILIATION_REQUIRED'
    assert set(map(tuple, authority['allowed_models'])) == {('siliconflow', model) for model in
        ('BAAI/bge-m3', 'BAAI/bge-reranker-v2-m3', 'XingChenAGI/Xing4.0-29B')}, 'PRIOR_ALLOWED_MODELS_MISMATCH'
    return authority, original


async def prepare():
    authority, old = trusted_authority()
    assert not (OUT / 'state.json').exists() and not (OUT / 'attempts.json').exists(), 'EXISTING_RUN_RECONCILIATION_REQUIRED'
    selection_bytes = (OUT / 'selection.json').read_bytes()
    selected = p8.validate_selection(json.loads(selection_bytes.decode('utf-8-sig')))
    assert {row['name'] for row in selected['datasets']} <= {'omnidocbench', 'mmlongbench-doc'}, 'P8_1_SCOPE_ONLY'
    datasets = {}
    for dataset in selected['datasets']:
        assert dataset.get('scope_mode') == 'knowledge_base', 'FROZEN_KNOWLEDGE_BASE_SCOPE_REQUIRED'
        prior_entry = old['datasets'][dataset['name']]
        entry = {key: deepcopy(value) for key, value in prior_entry.items() if key not in ('queries', 'documents')}
        entry.update(queries={}, documents={})
        for item in dataset['files']:
            assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256'], 'SOURCE_SHA_MISMATCH'
            previous = prior_entry.get('documents', {}).get(item['source_id'])
            if previous:
                assert previous['source']['sha256'] == item['sha256'], 'PRIOR_SOURCE_IDENTITY_SHA_MISMATCH'
                entry['documents'][item['source_id']] = deepcopy(previous)
                receipt = PRIOR / ('sources/' + p8.key(dataset['name'], item['source_id']) + '.json')
                assert receipt.exists(), 'PRIOR_SOURCE_READBACK_REQUIRED'
                destination = OUT / receipt.relative_to(PRIOR)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(receipt, destination)
        datasets[dataset['name']] = entry
    assert (PRIOR / 'master.key').is_file() and (PRIOR / 'assets').is_dir(), 'PRIOR_SECRET_OR_STORAGE_MISSING'
    p8.begin('prepare-redis-identity')
    from redis.asyncio import Redis
    redis = Redis.from_url('redis://127.0.0.1:56424/15', decode_responses=True)
    try:
        rid = (await redis.info('server'))['run_id']
        p8.finish('prepare-redis-identity', 'COMPLETED')
    finally:
        await redis.aclose()
    state = {'tenant': old['tenant'], 'subject': old['subject'], 'redis_run_id': rid,
        'redis_prefix': 'weknora-python-p8-1:' + uuid.uuid4().hex, 'datasets': datasets,
        'selection_sha256': hashlib.sha256(selection_bytes).hexdigest(),
        'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'prior_run': str(PRIOR), 'storage_root': str(PRIOR / 'assets'), 'secret_key_file': str(PRIOR / 'master.key')}
    p8.save('state.json', state)
    shutil.copyfile(PRIOR / 'authority.json', OUT / 'authority.json')
    p8.save('authority-reuse.json', {'source': str(PRIOR / 'authority.json'), 'expires_at': authority['expires_at'],
        'sha256': hashlib.sha256((OUT / 'authority.json').read_bytes()).hexdigest(), 'renewed': False})
    await p8.snapshot('before')
    print('PREPARED_REUSED_TENANT', state['tenant'])


def processes(state):
    pid = int(state['api_pid'])
    command = ("@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*" + MODULE +
               "*'} | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress")
    raw = subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True).strip()
    rows = json.loads(raw) if raw else []
    if isinstance(rows, dict): rows = [rows]
    expected = str(OUT / 'authority.json').replace('\\', '/').lower()
    candidates = [row for row in rows if expected in (row.get('CommandLine') or '').replace('\\', '/').lower()]
    known = {pid} | {int(row['ProcessId']) for row in state.get('api_processes', [])}
    found = {}
    while True:
        added = [row for row in candidates if int(row['ProcessId']) not in found
            and (int(row['ProcessId']) in known or int(row['ParentProcessId']) in known)]
        if not added: break
        for row in added:
            found[int(row['ProcessId'])] = row; known.add(int(row['ProcessId']))
    return list(found.values())


def start():
    trusted_authority(); p8.selection()
    state = p8.read('state.json')
    for port in (56429, 56430):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0, 'PORT_OCCUPIED:' + str(port)
    env = dict(os.environ)
    env.update(WEKNORA_PORT_DATABASE_URL=p8.dsn(), WEKNORA_PORT_DATABASE_SYSTEM_ID='7696088403504697382',
        WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013', WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',
        WEKNORA_PORT_REDIS_RUN_ID=state['redis_run_id'], WEKNORA_PORT_REDIS_PREFIX=state['redis_prefix'],
        WEKNORA_PORT_STORAGE_ROOT=state['storage_root'], WEKNORA_PORT_SECRET_KEY_FILE=state['secret_key_file'],
        WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',
        WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY', WEKNORA_PORT_FALLBACK_STRATEGY='fixed', WEKNORA_PORT_ORIGINS=p8.ORIGIN,
        RAG_TESSDATA_DIR='E:/RAG quention/var/tessdata', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
        TMP=str(OUT / 'tmp'), TEMP=str(OUT / 'tmp'))
    (OUT / 'tmp').mkdir(exist_ok=True)
    argv = [sys.executable, '-B', '-m', MODULE, '--port', '56429', '--authority-config', str(OUT / 'authority.json'), '--evidence-dir', str(OUT)]
    p8.begin('start', {'argv': argv, 'ports_checked_free': [56429, 56430]})
    with (OUT / 'api.stdout').open('ab') as stdout, (OUT / 'api.stderr').open('ab') as stderr:
        proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
    state['api_pid'] = proc.pid; p8.save('state.json', state); p8.save('api-command.json', argv)
    for _ in range(10):
        observed = processes(state)
        if any(row['ParentProcessId'] == proc.pid for row in observed) or proc.poll() is not None: break
        time.sleep(.1)
    state['api_processes'] = observed; p8.save('state.json', state)
    p8.finish('start', 'STARTED_PROCESS', pid=proc.pid)
    print('API_STARTED_NO_WORKER', proc.pid)


async def configure():
    # Reuse encrypted roles/KB consent; never PUT model configuration again.
    async with p8.client() as http:
        for label, path in [('ready', '/readyz'), ('model-configuration-public', '/api/v1/settings/model-configuration')]:
            p8.save(label + '.json', await p8.request(http, label + '-' + p8.key(p8.stamp()), 'GET', path))


async def upload(dataset):
    # Existing selected sources are already bound and have preserved receipts.
    selected = p8.selection()
    state = p8.read('state.json')
    missing = deepcopy(selected)
    for row in missing['datasets']:
        existing = state['datasets'][row['name']].get('documents', {})
        row['files'] = [item for item in row['files'] if item['source_id'] not in existing]
    original = p8.selection
    p8.selection = lambda: missing
    try:
        await p8.upload(dataset)
    finally:
        p8.selection = original


async def snapshot(label):
    await p8.snapshot(label)
    before = p8.read('ledger-before.json')
    after = p8.read('ledger-' + label + '.json')
    ids = {row['call_id'] for row in before}
    p8.save('ledger-delta-' + label + '.json', [row for row in after if row['call_id'] not in ids])
    p8.save('ledger-existing-changes-' + label + '.json', [row for row in after if row['call_id'] in ids
        and row != next(old for old in before if old['call_id'] == row['call_id'])])


def stop():
    state = p8.read('state.json')
    owned = processes(state)
    op = 'stop-' + p8.key(p8.stamp())
    p8.begin(op, {'pids': [row['ProcessId'] for row in owned]})
    for row in sorted(owned, key=lambda row: int(row['ProcessId']) == int(state['api_pid'])):
        target = int(row['ProcessId'])
        if target not in {int(item['ProcessId']) for item in processes(state)}: continue
        subprocess.run(['powershell', '-NoProfile', '-Command', f'if (Get-Process -Id {target} -ErrorAction SilentlyContinue) {{ Stop-Process -Id {target} -ErrorAction Stop }}'], check=True)
    remaining = processes(state)
    p8.finish(op, 'COMPLETED' if not remaining else 'UNKNOWN', remaining=remaining)
    assert not remaining, 'OWNED_PROCESS_REMAINS'
    state['api_stopped_at'] = p8.stamp(); p8.save('state.json', state)


async def main(args):
    bind()
    if args.stage == 'prepare': await prepare()
    elif args.stage == 'start': start()
    elif args.stage == 'configure': await configure()
    elif args.stage == 'upload': await upload(args.dataset)
    elif args.stage == 'query': await p8.query(args.dataset, args.qid)
    elif args.stage == 'snapshot': await snapshot(args.label)
    elif args.stage == 'stop': stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'start', 'configure', 'upload', 'query', 'snapshot', 'stop'])
    parser.add_argument('--dataset'); parser.add_argument('--qid'); parser.add_argument('--label', default='after')
    args = parser.parse_args()
    assert re.fullmatch('[A-Za-z0-9_-]+', args.label), 'INVALID_SNAPSHOT_LABEL'
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(main(args))
