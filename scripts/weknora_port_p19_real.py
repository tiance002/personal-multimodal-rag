"""Frozen P19 normal HTTP runner. Durable intent precedes send; no uncertain retry."""
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

from scripts.weknora_port_p8_real import connection, dsn, key

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('E:/WK/run/p19-20261016')


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8-sig'))


def save(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(path)


def start():
    state = read('state.json')
    for port in (56429, 56430):
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1', port)) != 0, 'PORT_OCCUPIED'
    env = dict(os.environ)
    env.update(WEKNORA_PORT_DATABASE_URL=dsn(), WEKNORA_PORT_DATABASE_SYSTEM_ID='7696088403504697382',
        WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013', WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',
        WEKNORA_PORT_REDIS_RUN_ID=state['redis_run_id'], WEKNORA_PORT_REDIS_PREFIX=state['redis_prefix'],
        WEKNORA_PORT_STORAGE_ROOT=state['storage_root'], WEKNORA_PORT_SECRET_KEY_FILE='E:/WK/run/p8-20261016/master.key',
        WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',
        WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY', WEKNORA_PORT_FALLBACK_STRATEGY='fixed',
        WEKNORA_PORT_ORIGINS=state['origin'], PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
        TEMP=str(OUT/'tmp'), TMP=str(OUT/'tmp'))
    (OUT/'tmp').mkdir(exist_ok=True)
    argv = [sys.executable, '-B', '-m', 'scripts.weknora_port_p19_observed_api', '--port', '56429',
            '--authority-config', str(OUT/'authority.json'), '--evidence-dir', str(OUT)]
    with (OUT/'api.stdout').open('ab') as stdout, (OUT/'api.stderr').open('ab') as stderr:
        proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=stdout, stderr=stderr,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    save('process.json', dict(pid=proc.pid, argv=argv, sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print('STARTED',proc.pid,flush=True)


async def preflight(case):
    state=read('state.json'); c=await connection()
    try:
        await c.execute('SET default_transaction_read_only=on')
        kb=await(await c.execute('SELECT tenant_id,cloud_allowed,deleted_at FROM knowledge_bases WHERE id=%s',(case['kb'],))).fetchone()
        assert kb and kb['tenant_id']==332 and kb['cloud_allowed'] and not kb['deleted_at'], 'KB_SCOPE_OR_PERMISSION_CHANGED'
        models=await(await c.execute('SELECT id,name,type FROM models WHERE tenant_id=332 ORDER BY id')).fetchall()
        assert models==read('models-before.json'),'MODEL_IDENTITY_CHANGED'
        docs=await(await c.execute('SELECT * FROM knowledges WHERE knowledge_base_id=%s ORDER BY id',(case['kb'],))).fetchall()
        before={r['id']:r for r in read('documents-before.json') if r['knowledge_base_id']==case['kb']}
        assert len(docs)==len(before),'INDEX_SCOPE_CHANGED'
        for row in docs:
            assert row['id'] in before and row['metadata']==before[row['id']]['metadata'] and row['deleted_at']==before[row['id']]['deleted_at'] and row['parse_status']==before[row['id']]['parse_status'],'DOCUMENT_VERSION_CHANGED'
        month=await(await c.execute("SELECT * FROM port_token_budget_months WHERE account_id='siliconflow-owner-main'")).fetchone()
        calls=await(await c.execute("SELECT * FROM port_token_calls WHERE account_id='siliconflow-owner-main'")).fetchall()
        assert not any(r['state']=='reserved' for r in calls), 'UNRESOLVED_RESERVED_CALL'
        used=sum(r['actual_total'] if r['state']=='settled' else r['held_tokens'] if r['state']=='unknown' else 0 for r in calls)
        snap={'headroom':month['limit_tokens']-used-14}
        assert snap['headroom']>=case['admission_bound'] and not month['blocked_reason'],'BUDGET_PREFLIGHT_DENIED'
        return dict(kb=kb,model_identity=models,headroom=snap['headroom'],document_count=len(docs))
    finally:await c.close()


async def batch(limit):
    import httpx
    state=read('state.json'); plan=read('target-plan.json'); started=time.monotonic(); count=0
    async with httpx.AsyncClient(base_url='http://127.0.0.1:56429',headers={'Origin':state['origin']},trust_env=False,timeout=600) as http:
        ready=await http.get('/readyz'); save('ready-latest.json',dict(status=ready.status_code,body=ready.json()));ready.raise_for_status()
        for case in plan['cases']:
            ident=key(case['dataset'],case['qid']); folder='cases/'+ident+'/'
            if (OUT/(folder+'intent.json')).exists():continue
            if count>=limit or time.monotonic()-started>=2100:break
            check=await preflight(case);save(folder+'preflight.json',check)
            session_body=dict(title='P19 '+case['dataset']+' '+case['qid'],knowledge_base_scope=[case['kb']],document_scope=[])
            response=await http.post('/api/v1/sessions',json=session_body);response.raise_for_status();session=response.json()['data']['id']
            record=dict(case,session=session,status='INTENT',documents=[])
            save(folder+'identity.json',record)
            body=dict(content=case['question'],request_id=case['request_id'],knowledge_base_scope=[case['kb']],document_scope=[],reasoning_effort='off')
            save(folder+'query-body.json',body);save(folder+'intent.json',dict(request_id=case['request_id'],at=time.time(),no_automatic_retry=True))
            print('SEND',case['dataset'],case['qid'],flush=True);t=time.monotonic()
            try:
                response=await http.post('/api/v1/sessions/'+session+'/messages',json=body)
                (OUT/(folder+'application.sse')).write_bytes(response.content)
                save(folder+'http.json',dict(status=response.status_code,elapsed_seconds=time.monotonic()-t))
                response.raise_for_status()
                messages=await http.get('/api/v1/sessions/'+session+'/messages');messages.raise_for_status();messages=messages.json();save(folder+'messages.json',messages)
                refs=[r for m in messages['data'] if m['role']=='assistant' for r in (m.get('knowledge_references') or [])];chunks=[]
                for ref in refs:
                    result=await http.get('/api/v1/chunks/'+ref['id']);result.raise_for_status();chunk=result.json();chunks.append(chunk)
                    assert chunk['data']['knowledge_base_id']==case['kb'],'CITATION_CROSS_KB'
                save(folder+'chunkreadback.json',chunks)
                assistants=[m for m in messages['data'] if m['role']=='assistant']
                assert len(assistants)==1 and assistants[0]['is_completed'] and not assistants[0].get('turn_error_code'),'APPLICATION_NOT_COMPLETED'
                record.update(status='COMPLETED',run_id=assistants[0]['request_id'],elapsed_seconds=time.monotonic()-t)
                state['datasets'][case['dataset']]['queries'][case['qid']]=record
                save(folder+'identity.json',record);save('state.json',state)
                count+=1;print('DONE',case['dataset'],case['qid'],'seconds',round(record['elapsed_seconds'],2),'refs',len(refs),flush=True)
            except BaseException as exc:
                record.update(status='UNKNOWN_REQUIRES_RECONCILIATION',error_type=type(exc).__name__)
                save(folder+'identity.json',record)
                raise
    print('BATCH_DONE',count,flush=True)


async def snapshot():
    c=await connection()
    try:
        await c.execute('SET default_transaction_read_only=on')
        data={}
        for name,table,where,order in [('documents','knowledges','tenant_id=332','id'),('ledger','port_token_calls','tenant_id=332','call_id'),('months','port_token_budget_months',"account_id='siliconflow-owner-main'",'month,provider'),('deepseek_calls','port_token_calls',"account_id='deepseek-owner-main'",'call_id'),('deepseek_months','port_token_budget_months',"account_id='deepseek-owner-main'",'month,provider')]:
            data[name]=await(await c.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY {order}')).fetchall()
        save('snapshot-final.json',data)
    finally:await c.close()


def stop():
    process=read('process.json');needle=str(OUT/'authority.json').replace('\\','/').lower()
    query="@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*scripts.weknora_port_p19_observed_api*' } | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress"
    raw=subprocess.check_output(['powershell','-NoProfile','-Command',query],text=True).strip();rows=json.loads(raw) if raw else []
    if isinstance(rows,dict):rows=[rows]
    owned=[r for r in rows if needle in (r['CommandLine'] or '').replace('\\','/').lower() and (r['ProcessId']==process['pid'] or r['ParentProcessId']==process['pid'])]
    save('process-stop.json',owned)
    for row in sorted(owned,key=lambda x:x['ProcessId']==process['pid']):
        subprocess.run(['powershell','-NoProfile','-Command',f"Stop-Process -Id {int(row['ProcessId'])} -ErrorAction Stop"],check=True)
    print('STOPPED_OWNED',len(owned))


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['start','batch','snapshot','stop']);p.add_argument('--limit',type=int,default=4);a=p.parse_args()
    if a.stage=='start':start()
    elif a.stage=='stop':stop()
    else:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(batch(a.limit) if a.stage=='batch' else snapshot())


if __name__=='__main__':main()
