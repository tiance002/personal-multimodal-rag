"""Bounded M2 HTTP acceptance using existing indexes, P32 observers and journals."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[1]
for path in (str(REPO),'E:/WK/deps/reader',str(REPO/'var/weknora-port/runtime-deps')):
    if path not in sys.path:sys.path.insert(0,path)
from scripts import p32_real_acceptance as c
OUT=Path('E:/WK/run/rag-m2-20261017')
c.OUT=OUT
KB='7f1d720d-9561-446f-a8cc-6a562c339d23'
QUESTIONS={
    'expensive':'若电离层有效且卫星有效且无线电波属于电磁波且微波属于电磁波，是否可以根据课件区分两者的远距离通信方式？分别列出无线电波、微波的传播路径、介质、应用，并指出资料不足的部分。',
    'upgrade':'请根据知识库，用不超过100个汉字解释无线电波和微波如何用于远距离通信，并指出两者传播路径的区别。',
}

def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'state.json').exists():raise RuntimeError('M2_ALREADY_PREPARED')
    c.save(OUT/'plan.json',{'cases':[],'tenants':[]})
    c.prepare(str(OUT/'plan.json'))

async def offline():
    from backend.weknora_port.generation.router import decide,RouterPolicy
    conn=await c.pg()
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        rows=await (await conn.execute("SELECT c.id,c.content,c.chunk_type,c.metadata,k.id AS document_id,k.file_hash,k.metadata AS document_metadata FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id AND k.tenant_id=c.tenant_id WHERE c.tenant_id=332 AND c.knowledge_base_id=%s AND c.is_enabled=true AND c.deleted_at IS NULL AND k.deleted_at IS NULL ORDER BY c.chunk_index,c.id",(KB,))).fetchall()
        await conn.execute('ROLLBACK')
    finally:await conn.close()
    routes={k:decide(q,'',RouterPolicy(mode='DYNAMIC')).public(RouterPolicy(mode='DYNAMIC')) for k,q in QUESTIONS.items()}
    c.save(OUT/'offline.json',{'knowledge_base_id':KB,'document_scope':[],
        'source_rows':rows,'routes':routes,'questions':QUESTIONS,'cloud_calls':0})
    print(json.dumps({'source_chunks':len(rows),'routes':routes},ensure_ascii=True))

def start():
    s=c.state();p=s['primary_tenant']
    c.launch('api-332',332,p['subject_id'],56425,p['storage_root'],p['redis_prefix'],p['api_evidence'])
    import httpx
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        try:
            r=httpx.get('http://127.0.0.1:56425/readyz',trust_env=False,timeout=2)
            if r.status_code==200:
                c.save(OUT/'ready.json',r.json());print('M2_API_READY');return
        except httpx.HTTPError:pass
        time.sleep(.2)
    raise RuntimeError('M2_NOT_READY')

async def query(kind,cap=None):
    from backend.weknora_port.generation.router import decide,RouterPolicy
    from scripts.p32_score_results import parse_sse
    q=QUESTIONS[kind]
    decision=decide(q,'',RouterPolicy(mode='DYNAMIC'))
    assert decision.role==('expensive' if kind=='expensive' else 'cheap')
    # Separate, durable journal entry for every attempt; never retry an UNKNOWN.
    op='query-'+kind
    folder=OUT/op;folder.mkdir(exist_ok=True)
    s=c.state()
    conn=await c.pg()
    try:
        count=await (await conn.execute('SELECT count(*) AS n FROM port_provider_calls WHERE tenant_id=332 AND created_at>=%s',(s['created_at'],))).fetchone()
    finally:await conn.close()
    if count['n']>18:raise RuntimeError('M2_AUXILIARY_CALL_LIMIT')
    base='http://127.0.0.1:56425'
    session=(await c.http_request('POST',base+'/api/v1/sessions',body={
        'title':'M2 '+kind,'knowledge_base_scope':[KB],'document_scope':[]},op=op+'-session')).json()['data']['id']
    configuration={'default_options':True,'same_hard_limit_for_both_roles':True}
    if cap:
        conn=await c.pg()
        try:
            rows=await (await conn.execute("UPDATE sessions SET summary_parameters=summary_parameters || %s::jsonb WHERE tenant_id=332 AND subject_id=%s AND id=%s AND NOT EXISTS(SELECT 1 FROM messages WHERE session_id=%s) RETURNING summary_parameters",(
                json.dumps({'max_completion_tokens':cap}),s['primary_tenant']['subject_id'],session,session))).fetchall()
            if len(rows)!=1:raise RuntimeError('M2_FRESH_SESSION_CONFIG_GUARD')
            configuration.update(default_options=False,summary_parameters=rows[0]['summary_parameters'])
        finally:await conn.close()
    c.save(folder/'configuration.json',configuration)
    rid=str(uuid.uuid4());started=time.monotonic()
    response=await c.http_request('POST',base+f'/api/v1/sessions/{session}/messages',body={
        'content':q,'request_id':rid,'knowledge_base_scope':[KB],'document_scope':[],
        'reasoning_effort':'off'},op=op)
    elapsed=time.monotonic()-started
    (folder/'answer.sse').write_bytes(response.content)
    messages=(await c.http_request('GET',base+f'/api/v1/sessions/{session}/messages',op=op+'-messages')).json()
    c.save(folder/'messages.json',messages)
    stream=parse_sse(folder/'answer.sse')
    complete=[e for e in stream['events'] if e.get('response_type',e.get('type'))=='complete']
    record={'kind':kind,'tenant_id':332,'session_id':session,'question':q,'request_id':rid,
        'knowledge_base_id':KB,'document_scope':[],'http_status':response.status_code,
        'post_seconds':elapsed,'complete':complete,'answer':stream['answer'],'errors':stream['errors']}
    c.save(folder/'result.json',record)
    s=c.state();s['cases'][kind]=record;c.save(OUT/'state.json',s)
    print(json.dumps({'kind':kind,'seconds':elapsed,'complete':complete,'answer':stream['answer'],'errors':stream['errors']},ensure_ascii=True))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('prepare','offline','start','query','snapshot','stop'))
    p.add_argument('kind',nargs='?');p.add_argument('--cap',type=int)
    a=p.parse_args()
    if a.command=='prepare':prepare()
    elif a.command=='start':start()
    elif a.command=='offline':c.run(offline())
    elif a.command=='query':c.run(query(a.kind,a.cap))
    elif a.command=='snapshot':c.run(c.snapshot())
    elif a.command=='stop':c.stop()

if __name__=='__main__':main()
