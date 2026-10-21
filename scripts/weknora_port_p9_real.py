"""Explicit P9 real stages. No calls on import; no uncertain-send retries."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import weknora_port_p8_1_real as prior
from scripts import weknora_port_p8_real as p8

BASE = Path('E:/WK/run/p9-20261016')
OLD = Path('E:/WK/run/p8-20261016')
P81 = Path('E:/WK/run/p8-1-20261016')

def save_checkpoint(name,value):
    """Retry only an atomic rename briefly held by a Windows reader."""
    target=p8.OUT/name
    assert target.resolve().is_relative_to(BASE.resolve()), 'P9_CHECKPOINT_ROOT_REQUIRED'
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    for attempt in range(6):
        try:
            temporary.replace(target)
            return
        except PermissionError as error:
            if getattr(error,'winerror',None) not in (5,32) or attempt==5:
                raise
            time.sleep(.02*(2**attempt))

def bind(directory):
    global OUT
    OUT = directory.resolve()
    assert OUT.is_relative_to(BASE.resolve()), 'P9_OUTPUT_ROOT_REQUIRED'
    prior.OUT = OUT
    prior.MODULE = 'scripts.weknora_port_p9_observed_api'
    prior.bind()
    p8.save=save_checkpoint

async def preflight(label):
    authority, original = prior.trusted_authority()
    conn = await p8.connection()
    try:
        await conn.execute('SET default_transaction_read_only=on')
        identity = await (await conn.execute('SELECT system_identifier::text FROM pg_control_system()')).fetchone()
        assert identity['system_identifier'] == '7696088403504697382', 'PG_IDENTITY_MISMATCH'
        db = await (await conn.execute('SELECT current_database() AS db')).fetchone()
        assert db['db'] == 'weknora_python_port_20261013', 'DB_IDENTITY_MISMATCH'
        month = await (await conn.execute("SELECT * FROM port_token_budget_months WHERE account_id=%s AND provider=%s AND month=%s",
            (authority['account_id'], authority['provider'], authority['month']))).fetchone()
        assert month and not month['blocked_reason'], 'ACCOUNT_BLOCKED'
        for name in ('limit_tokens','carry_in_tokens','carry_status','carry_source','approval_source'):
            assert month[name] == authority[name], 'AUTHORITY_LEDGER_MISMATCH:' + name
        totals = await (await conn.execute("SELECT COALESCE(SUM(CASE WHEN state IN ('reserved','unknown') THEN held_tokens WHEN state='settled' THEN actual_total ELSE 0 END),0) AS held FROM port_token_calls WHERE account_id=%s AND budget_provider=%s AND month=%s",
            (authority['account_id'], authority['provider'], authority['month']))).fetchone()
        remaining = int(month['limit_tokens']) - int(month['carry_in_tokens']) - int(totals['held'])
        assert remaining > 0, 'LOCAL_FENCE_EXHAUSTED'
        records = await (await conn.execute('SELECT * FROM port_token_calls WHERE account_id=%s ORDER BY call_id', (authority['account_id'],))).fetchall()
        models = await (await conn.execute('SELECT id,name,type FROM models WHERE tenant_id=%s ORDER BY id',(original['tenant'],))).fetchall()
        expected = {'BAAI/bge-m3','BAAI/bge-reranker-v2-m3','XingChenAGI/Xing4.0-29B'}
        assert {r['name'] for r in models} == expected, 'MODEL_IDENTITY_MISMATCH'
        from redis.asyncio import Redis
        redis = Redis.from_url('redis://127.0.0.1:56424/15',decode_responses=True)
        try: rid = (await redis.info('server'))['run_id']
        finally: await redis.aclose()
        assert rid == '87b4c2a9f76f9f94b34b5f21fe31a967926f343a', 'REDIS_IDENTITY_MISMATCH'
        result = {'at':p8.stamp(),'read_only':True,'pg':identity,'database':db,'redis_run_id':rid,
            'account_id':authority['account_id'],'provider':authority['provider'],'month':authority['month'],
            'expires_at':authority['expires_at'],'monthly':month,'held':int(totals['held']),
            'remaining_occupancy_tokens':remaining,'models':models,'rows':len(records)}
        p8.save('preflight/'+label+'.json', result)
        p8.save('account-ledger/'+label+'.json', records)
        print('PREFLIGHT',label,'remaining',remaining,'models',len(models))
        return result
    finally: await conn.close()

async def prepare():
    assert not (OUT/'state.json').exists() and not (OUT/'attempts.json').exists(), 'EXISTING_RUN_PRESERVE'
    OUT.mkdir(parents=True,exist_ok=True)
    await preflight('prepare')
    authority, old = prior.trusted_authority()
    selected = p8.validate_selection(p8.read('selection.json'))
    sources = []
    for directory in (OLD,P81):
        sources.append((directory,json.loads((directory/'state.json').read_text(encoding='utf-8-sig'))))
    datasets = {}
    for dataset in selected['datasets']:
        assert dataset['scope_mode']=='knowledge_base', 'KB_RETRIEVAL_REQUIRED'
        inherited = old['datasets'][dataset['name']]
        entry = {k:deepcopy(v) for k,v in inherited.items() if k not in ('documents','queries')}
        entry.update(documents={},queries={})
        for item in dataset['files']:
            assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'].lower(), 'SOURCE_HASH_MISMATCH'
            for directory,state in reversed(sources):
                record = state['datasets'].get(dataset['name'],{}).get('documents',{}).get(item['source_id'])
                if record:
                    assert record['source']['sha256']==item['sha256'], 'PRIOR_SOURCE_MISMATCH'
                    entry['documents'][item['source_id']]=deepcopy(record)
                    receipt=directory/('sources/'+p8.key(dataset['name'],item['source_id'])+'.json')
                    assert receipt.is_file(), 'SOURCE_RECEIPT_MISSING'
                    target=OUT/receipt.relative_to(directory);target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(receipt,target)
                    break
        datasets[dataset['name']]=entry
    p8.save('state.json',{'tenant':old['tenant'],'subject':old['subject'],
        'redis_run_id':'87b4c2a9f76f9f94b34b5f21fe31a967926f343a','redis_prefix':'weknora-python-p9:'+uuid.uuid4().hex,
        'datasets':datasets,'selection_sha256':hashlib.sha256((OUT/'selection.json').read_bytes()).hexdigest(),
        'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'storage_root':str(OLD/'assets'),'secret_key_file':str(OLD/'master.key')})
    shutil.copyfile(OLD/'authority.json',OUT/'authority.json')
    await p8.snapshot('before')

async def query(dataset,qid):
    await preflight('query-'+p8.key(dataset,qid,p8.stamp()))
    await p8.query(dataset,qid)

async def batch(dataset,phase):
    selected=p8.selection()
    for row in selected['datasets']:
        if dataset and row['name']!=dataset: continue
        eligible=[case for case in row['cases'] if case.get('execution_status')!='NO_MODEL_CALL_CAPABILITY_LIMIT']
        chosen=eligible[:2] if phase=='first' else eligible[2:]
        for case in chosen:
            attempted='query-'+p8.key(row['name'],case['qid']) in p8.read('attempts.json')
            if attempted:
                print('PRESERVED_PRIOR_ATTEMPT',row['name'],case['qid'],flush=True);continue
            await query(row['name'],case['qid'])
            print('COLLECTED',row['name'],case['qid'],flush=True)
            await prior.snapshot('checkpoint-'+p8.key(row['name'],case['qid']))

def start_worker():
    state=p8.read('state.json')
    assert 'worker_pid' not in state, 'WORKER_ALREADY_RECORDED'
    env=dict(os.environ)
    env.update(WEKNORA_PORT_DATABASE_URL=p8.dsn(),WEKNORA_PORT_DATABASE_SYSTEM_ID='7696088403504697382',
        WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013',WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',
        WEKNORA_PORT_REDIS_RUN_ID=state['redis_run_id'],WEKNORA_PORT_REDIS_PREFIX=state['redis_prefix'],
        WEKNORA_PORT_STORAGE_ROOT=state['storage_root'],WEKNORA_PORT_SECRET_KEY_FILE=state['secret_key_file'],
        WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY',
        WEKNORA_PORT_FALLBACK_STRATEGY='fixed',RAG_TESSDATA_DIR='E:/RAG quention/var/tessdata',
        PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',TMP=str(OUT/'tmp'),TEMP=str(OUT/'tmp'))
    argv=[sys.executable,'-B','-m','backend.weknora_port.worker','--authority-config',str(OUT/'authority.json')]
    with (OUT/'worker.stdout').open('ab') as stdout,(OUT/'worker.stderr').open('ab') as stderr:
        process=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=stdout,stderr=stderr)
    state['worker_pid']=process.pid;p8.save('state.json',state);p8.save('worker-command.json',argv)

async def main(args):
    bind(args.run_dir)
    if args.stage=='prepare': await prepare()
    elif args.stage=='preflight': await preflight(args.label)
    elif args.stage=='start': prior.start()
    elif args.stage=='configure': await prior.configure()
    elif args.stage=='upload':
        await preflight('upload-'+p8.key(args.dataset,p8.stamp()))
        original=p8.selection
        def limited_selection():
            selected=deepcopy(original())
            if args.limit is not None:
                state=p8.read('state.json')
                for row in selected['datasets']:
                    existing=state['datasets'][row['name']]['documents']
                    known=[f for f in row['files'] if f['source_id'] in existing]
                    missing=[f for f in row['files'] if f['source_id'] not in existing]
                    row['files']=known+missing[:args.limit]
            return selected
        p8.selection=limited_selection
        try: await prior.upload(args.dataset)
        finally: p8.selection=original
    elif args.stage=='query': await query(args.dataset,args.qid)
    elif args.stage=='batch': await batch(args.dataset,args.phase)
    elif args.stage=='snapshot': await prior.snapshot(args.label)
    elif args.stage=='start-worker': start_worker()
    elif args.stage=='stop':
        state=p8.read('state.json')
        if state.get('worker_pid'): p8.stop_process(state,'worker')
        prior.stop()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare','preflight','start','configure','upload','query','batch','snapshot','start-worker','stop'])
    parser.add_argument('--run-dir',required=True,type=Path);parser.add_argument('--dataset');parser.add_argument('--qid')
    parser.add_argument('--phase',choices=['first','rest'],default='first')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--label',default='after');args=parser.parse_args()
    assert __import__('re').fullmatch('[A-Za-z0-9_-]+',args.label), 'LABEL_REQUIRED'
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as loop:loop.run(main(args))
