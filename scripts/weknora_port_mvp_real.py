"""Explicit public-text MVP runner. Real standard API, real TLS; no model mocks.

Only SiliconFlow is admitted. Never alters historical accounts or DeepSeek blocks.
Credentials stay process-local until sent to the local encrypted model store.
Each stage is explicit; failed/uncertain operations are never automatically resent.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('E:/WK/run/mvp-20261016')
sys.path.insert(0, str(ROOT))
BASE = 'http://127.0.0.1:56425'
SOURCE = 'https://www.rfc-editor.org/rfc/rfc2606.txt'
ROUND = 'initial'


def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def state():
    return json.loads((OUT/'state.json').read_text(encoding='utf-8'))


def dsn():
    # Existing isolated fixture connection; never print its credential.
    from urllib.parse import urlsplit
    text = (ROOT/'docs/weknora-port/AUTOMATIC_MEMORY_CHECKPOINT_20261013.md').read_text(encoding='utf-8-sig')
    return next(v for v in re.findall(r'postgres(?:ql)?://[^\s\x27\x22\x60]+', text)
                if urlsplit(v).hostname == '127.0.0.1' and urlsplit(v).port == 56423)


def credential():
    for line in Path('E:/RAG quention/.env.local').read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip() == 'SILICONFLOW_API_KEY':
            value = value.strip().strip('\"').strip("'")
            if value:
                return value
    raise RuntimeError('SILICONFLOW_CREDENTIAL_UNAVAILABLE')


async def connection():
    import psycopg
    from psycopg.rows import dict_row
    conn = await psycopg.AsyncConnection.connect(dsn(), autocommit=True, row_factory=dict_row, connect_timeout=5)
    identity = (await (await conn.execute('SELECT system_identifier FROM pg_control_system()')).fetchone())['system_identifier']
    assert str(identity) == '7696088403504697382', 'ISOLATED_PG_IDENTITY_MISMATCH'
    return conn


async def snapshot(label):
    conn = await connection()
    try:
        # Preserve DS authoritative history exactly, including misbooked historical SF.
        months = await (await conn.execute("SELECT * FROM port_token_budget_months WHERE account_id='deepseek-owner-main' ORDER BY month,provider")).fetchall()
        calls = await (await conn.execute("SELECT * FROM port_token_calls WHERE account_id='deepseek-owner-main' ORDER BY call_id")).fetchall()
        save('deepseek-'+label+'.json', {'months': months, 'calls': calls})
        if (OUT/'state.json').exists():
            s = state()
            rows = await (await conn.execute('SELECT request_id,account_id,provider,budget_provider,model,model_id,capability,state,outcome,reserved_input,reserved_output,held_tokens,actual_total,known_lower_bound,usage_facts FROM port_token_calls WHERE tenant_id=%s ORDER BY call_id', (s['tenant'],))).fetchall()
            save('ledger-'+label+'.json', rows)
            if s.get('document'):
                chunks = await (await conn.execute('SELECT id,knowledge_id,knowledge_base_id,chunk_index,chunk_type,content,is_enabled FROM chunks WHERE tenant_id=%s AND knowledge_id=%s ORDER BY chunk_index', (s['tenant'],s['document']))).fetchall()
                save('chunks-'+label+'.json',chunks)
                vectors = await (await conn.execute('SELECT count(*) AS n FROM embeddings WHERE knowledge_id=%s',(s['document'],))).fetchone()
                save('vectors-'+label+'.json',vectors)
    finally:
        await conn.close()


async def prepare():
    assert not (OUT/'state.json').exists(), 'EXISTING_RUN_USE_EXPLICIT_STAGE'
    OUT.mkdir(parents=True, exist_ok=True)
    await snapshot('before')
    with urllib.request.urlopen(SOURCE,timeout=30) as response:
        data = response.read()
    assert b'Reserved Top Level DNS Names' in data and b'Full Copyright Statement' in data
    (OUT/'rfc2606.txt').write_bytes(data)
    from redis.asyncio import Redis
    redis = Redis.from_url('redis://127.0.0.1:56424/15',decode_responses=True)
    try:
        rid = (await redis.info('server'))['run_id']
    finally:
        await redis.aclose()
    conn = await connection()
    try:
        tenant = (await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',('MVP-public-RFC2606-20261016','isolated-test'))).fetchone())['id']
    finally:
        await conn.close()
    tag = uuid.uuid4().hex
    s = dict(tenant=tenant,subject='mvp-public-'+tag,redis_run_id=rid,redis_prefix='weknora-python-mvp:'+tag,
             source_url=SOURCE,source_sha256=hashlib.sha256(data).hexdigest(),source_bytes=len(data),
             source_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),questions=[])
    save('state.json',s)
    (OUT/'master.key').write_bytes(os.urandom(32))
    meter = ROOT/'backend/weknora_port/source_facts.py'
    cfg = dict(schema='weknora-token-authority/v1',mode='approved',tenant_id=tenant,subject_id=s['subject'],
        account_id='siliconflow-owner-main',provider='siliconflow',
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(),
        approval_source='OWNER:20261016-unrestricted-siliconflow-MVP',
        month=datetime.now(timezone.utc).date().replace(day=1).isoformat(),
        # Historical SF embedding=14 remains recorded in the original DS bucket.
        # Count it conservatively here too, without rewriting or unblocking history.
        carry_in_tokens=14,carry_status='KNOWN',carry_source='LEDGER:real/embedding-0:14-preserved-original-bucket',
        allowed_models=[['siliconflow',m] for m in ('BAAI/bge-m3','BAAI/bge-reranker-v2-m3','XingChenAGI/Xing4.0-29B')],
        input_limit=200000,output_limit=8192,limit_tokens=1000000,
        meter=dict(recipe='provider-completion-cap/v2',path=str(meter),sha256=hashlib.sha256(meter.read_bytes()).hexdigest()))
    # SF is owner-unrestricted. This finite ceiling is an additional local MVP
    # execution fence, not a claim that Owner imposed a one-million SF quota.
    save('authority.json',cfg)
    print('PREPARED_PUBLIC_REAL_RUN',tenant,len(data))


def start():
    s = state()
    import socket
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1',56425)) != 0, 'API_PORT_OCCUPIED'
    env = dict(os.environ)
    env.update(WEKNORA_PORT_DATABASE_URL=dsn(),WEKNORA_PORT_DATABASE_SYSTEM_ID='7696088403504697382',
        WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013',WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',
        WEKNORA_PORT_REDIS_RUN_ID=s['redis_run_id'],WEKNORA_PORT_REDIS_PREFIX=s['redis_prefix'],
        WEKNORA_PORT_STORAGE_ROOT=str(OUT/'assets'),WEKNORA_PORT_SECRET_KEY_FILE=str(OUT/'master.key'),
        WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',
        WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY',WEKNORA_PORT_FALLBACK_STRATEGY='fixed',
        WEKNORA_PORT_ORIGINS='http://127.0.0.1:56426',
        PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',TMP=str(OUT/'tmp'),TEMP=str(OUT/'tmp'))
    (OUT/'tmp').mkdir(exist_ok=True)
    argv=[sys.executable,'-B','-m','backend.weknora_port.entrypoint','--port','56425','--authority-config',str(OUT/'authority.json')]
    with (OUT/'api.stdout').open('ab') as out,(OUT/'api.stderr').open('ab') as err:
        proc=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=out,stderr=err)
    s['api_pid']=proc.pid;save('state.json',s);save('api-command.json',argv)
    print('API_STARTED',proc.pid)


def checked(response):
    # Never include request headers/bodies (configuration carries a credential).
    if response.status_code != 200:
        raise RuntimeError('HTTP_'+str(response.status_code)+':'+response.text[:500])
    return response.json()


async def configure():
    import httpx
    s=state()
    async with httpx.AsyncClient(base_url=BASE,headers={'Origin':'http://127.0.0.1:56426'},trust_env=False,timeout=45) as client:
        ready=None
        for _ in range(100):
            try:
                ready=await client.get('/readyz')
                if ready.status_code==200:break
            except httpx.ConnectError:pass
            await asyncio.sleep(.2)
        assert ready is not None and ready.status_code==200,'READY_DENIED'
        save('ready.json',ready.json())
        rows=[]
        key=credential()
        for role,provider,model in [('embedding','siliconflow','BAAI/bge-m3'),('rerank','siliconflow','BAAI/bge-reranker-v2-m3'),('cheap','siliconflow','XingChenAGI/Xing4.0-29B'),('expensive','deepseek','deepseek-flash'),('vlm','deepseek','deepseek-flash')]:
            rows.append(dict(role=role,provider=provider,model=model,source='remote',base_url='https://api.siliconflow.cn/v1' if provider=='siliconflow' else 'https://api.deepseek.com',api_key=key if provider=='siliconflow' else ''))
        checked(await client.put('/api/v1/settings/model-configuration',json={'revision':'0','roles':rows}))
        save('model-configuration-public.json',checked(await client.get('/api/v1/settings/model-configuration')))
        kb=checked(await client.post('/api/v1/knowledge-bases',json={'name':'MVP RFC2606 public real RAG'}))['data']['id']
        checked(await client.patch(f'/api/v1/knowledge-bases/{kb}/external-processing',json={'cloud_allowed':True}))
        s['kb']=kb;save('state.json',s)
    # Only this fresh isolated KB: existing chunk configuration, no algorithm tuning.
    conn=await connection()
    try:
        await conn.execute('UPDATE knowledge_bases SET chunking_config=%s::jsonb WHERE tenant_id=%s AND id=%s',(json.dumps(dict(chunk_size=1000,chunk_overlap=100,split_markers=['\n\n','\n'])),s['tenant'],kb))
    finally:await conn.close()
    print('CONFIGURED_ENCRYPTED_SF_MODELS_AND_PUBLIC_KB',kb)


async def upload():
    import httpx
    s=state();assert not s.get('upload_attempted'),'UPLOAD_ALREADY_ATTEMPTED_NO_AUTOMATIC_RESEND'
    s['upload_attempted']=True;save('state.json',s)
    async with httpx.AsyncClient(base_url=BASE,headers={'Origin':'http://127.0.0.1:56426'},trust_env=False,timeout=240) as client:
        response=await client.post(f"/api/v1/knowledge-bases/{s['kb']}/documents",files={'file':('rfc2606.txt',(OUT/'rfc2606.txt').read_bytes(),'text/plain')})
        result=checked(response);save('upload.json',result)
        row=result['data'];s['document']=row['id'];save('state.json',s)
        assert row['status']=='completed', 'UPLOAD_NOT_COMPLETED:'+str(row.get('error_code'))
    print('REAL_DOCUMENT_COMPLETED',s['document'])


QUESTIONS=[
    'According to RFC 2606, which four top-level domain names are reserved? Cite the document.',
    'According to RFC 2606, which top-level name is recommended for testing DNS code, and which three second-level names are reserved for examples? Combine both sections and cite each supporting passage.',
    'According only to RFC 2606, what is the exact annual price in US dollars for registering example.com? If the document does not specify it, say that the document does not provide the answer.',
    'Which three second-level domain names does Section 3 of RFC 2606 reserve for examples? Cite the supporting passage.']


async def question(index):
    import httpx
    s=state();assert not any(x['index']==index and x.get('round','initial')==ROUND for x in s['questions']),'QUESTION_ALREADY_ATTEMPTED_NO_AUTOMATIC_RESEND'
    prefix='' if ROUND=='initial' else ROUND+'-'
    async with httpx.AsyncClient(base_url=BASE,headers={'Origin':'http://127.0.0.1:56426'},trust_env=False,timeout=240) as client:
        # Fresh session for each basic question; no historical scope ambiguity.
        session=checked(await client.post('/api/v1/sessions',json={'title':'MVP RFC2606 Q'+str(index+1),'knowledge_base_scope':[s['kb']],'document_scope':[s['document']]}))['data']['id']
        record=dict(index=index,round=ROUND,session=session,request_id=str(uuid.uuid4()),question=QUESTIONS[index])
        s['questions'].append(record);save('state.json',s)
        request=dict(content=record['question'],request_id=record['request_id'],knowledge_base_scope=[s['kb']],document_scope=[s['document']])
        if ROUND=='verified':
            # Existing ordinary API capability: disable optional reasoning after
            # the observed 1024-token all-thinking/empty-answer truncation.
            request['reasoning_effort']='off'
        events=await client.post(f'/api/v1/sessions/{session}/messages',json=request)
        (OUT/f'{prefix}question-{index+1}.sse').write_text(events.text,encoding='utf-8')
        assert events.status_code==200,'CHAT_HTTP_FAILED'
        messages=checked(await client.get(f'/api/v1/sessions/{session}/messages'))
        save(f'{prefix}question-{index+1}-messages.json',messages)
        answer=next(x for x in messages['data'] if x['role']=='assistant')
        assert answer['is_completed'],'ANSWER_NOT_COMPLETED'
        refs=answer.get('knowledge_references') or []
        sources=[]
        for ref in refs:
            result=checked(await client.get('/api/v1/chunks/'+ref['id']))
            sources.append(result)
            assert result['data']['knowledge_base_id']==s['kb'],'CITATION_KB_MISMATCH'
            assert result['data']['document_id']==s['document'],'CITATION_DOCUMENT_MISMATCH'
        save(f'{prefix}question-{index+1}-sources.json',sources)
        if index!=2:assert refs,'FACTUAL_ANSWER_MISSING_CITATION'
        print('REAL_QUESTION_COMPLETED',index+1,'citations',len(refs))


async def main(stage):
    if stage=='prepare':await prepare()
    elif stage=='start':start()
    elif stage=='configure':await configure()
    elif stage=='upload':await upload()
    elif stage.startswith('q'):await question(int(stage[1:])-1)
    elif stage=='snapshot':await snapshot('final')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','start','configure','upload','q1','q2','q3','q4','snapshot'])
    parser.add_argument('--round',choices=['initial','final','verified'],default='initial')
    args=parser.parse_args();stage=args.stage;ROUND=args.round
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        try:runner.run(main(stage))
        finally:
            if (OUT/'state.json').exists() and stage not in ('prepare','start'):
                runner.run(snapshot('final'))
