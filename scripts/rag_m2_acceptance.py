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
    'upgrade-concise':'根据知识库，用英文两句话（合计不超过35个英文单词）解释电离层对无线电波、卫星对微波远距离通信的作用。不要标题或扩展推论。',
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
    if s['processes'].get('api-332',{}).get('status') in ('STOPPED','ALREADY_EXITED'):
        del s['processes']['api-332'];c.save(OUT/'state.json',s)
    c.P31_RUNNER=Path(__file__).resolve()
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

def service():
    # Reuse the passive P17 byte-for-byte observer, without its old send cap,
    # authority factory or model substitution. Normal runtime gates still send.
    from contextlib import asynccontextmanager
    from scripts import weknora_port_p9_observed_api as wire
    from scripts.weknora_port_p17_observed_api import RawResponse
    from scripts.p31_native_acceptance import service_main
    original=wire.install_wire_observer
    def install(model,root):
        model=original(model,root)
        opened=model.transport.open
        @asynccontextmanager
        async def observed(config,path,body,context,**kwargs):
            async with opened(config,path,body,context,**kwargs) as handle:
                proxy=RawResponse(handle.response,root,context.request_id,kwargs['capability']) if '/answer/' in context.request_id else None
                if proxy:handle.response=proxy
                try:yield handle
                finally:
                    if proxy:proxy.finish()
        model.transport.open=observed
        return model
    wire.install_wire_observer=install
    service_main()

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


async def summarize():
    await c.snapshot()
    snap=c.read(OUT/'snapshot.json')
    calls=snap['p32_provider_calls']
    captures=[c.read(p) for p in (OUT/'evidence-332/captures').glob('*.json')]
    wires=[c.read(p) for p in (OUT/'evidence-332/provider-requests').glob('*.json')]
    cases=[]
    for name,record in c.state()['cases'].items():
        completion=record['complete'][0]
        run=completion['id'];data=completion['data']
        capture=next(v for v in captures if v['run_id']==run)
        persisted=c.read(OUT/('query-'+name)/'messages.json')['data']
        saved=next(m for m in persisted if m['role']=='assistant' and m['request_id']==run)
        assert saved['is_completed'] and saved['content']==record['answer']
        attempts=[]
        for a in data['attempts']:
            wire=next(w for w in wires if w['request_id']==a['attempt_id'])
            actual_messages=wire['body']['messages']
            expected=[{'role':m['role'],'content':m['content']} for m in capture['messages']]
            assert actual_messages==expected  # These text-only cases have no tools/images.
            raw=OUT/'evidence-332/raw'/(c.sha(a['attempt_id'].encode())+'.body')
            attempts.append({k:a.get(k) for k in ('attempt_id','role','provider','finish_reason','status','upgrade_reason','prompt_sha256','context_sha256')} | {
                'usage':{k:a['usage'].get(k) for k in ('prompt_tokens','completion_tokens','total_tokens','status')},
                'wire_sha256':wire['wire_sha256'],'max_tokens':wire['body'].get('max_tokens'),
                'raw_response':str(raw) if raw.exists() else 'NOT_CAPTURED_P9_PARSED_RECEIPT_ONLY'})
        selected=[x for x in calls if x['request_id']==run or x['request_id'].startswith(run+'/') or x['request_id'].startswith(run+'/query-')]
        # request_id prefixes include each embedding identity beneath this run.
        cases.append({'case':name,'session_id':record['session_id'],'run_id':run,'question':record['question'],
            'route':data['router'],'is_fallback':data['is_fallback'],'finish_reason':data['finish_reason'],
            'upgraded':data['upgraded'],'truncated':data['truncated'],
            'stage_counts':{k:len(v) for k,v in capture['rankings'].items()},
            'evidence':[{k:e.get(k) for k in ('id','knowledge_id','knowledge_base_id','start_at','end_at','source_content')} for e in capture['evidence']],
            'answer':record['answer'],'sse_equals_history':True,'actual_wire_messages_equal_capture':True,
            'post_seconds':record['post_seconds'],'provider_calls':len(selected),
            'all_chain_usage':{k:sum((x.get('usage') or {}).get(k) or 0 for x in selected) for k in ('prompt_tokens','completion_tokens','total_tokens')},
            'attempts':attempts})
    result={'source_commit':__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'cases':cases,'provider_calls':len(calls),'all_usage_reported':all((x.get('usage') or {}).get('status')=='REPORTED' for x in calls),
        'unknown_outcomes':sum(x['outcome']!='RESPONSE_RECEIVED' for x in calls),
        'total_usage':{k:sum((x.get('usage') or {}).get(k) or 0 for x in calls) for k in ('prompt_tokens','completion_tokens','total_tokens')},
        'legacy_accounting_unchanged':snap['legacy_before']==snap['legacy_after'],
        'calls':[{k:x[k] for k in ('call_id','request_id','provider','model','capability','outcome','created_at','finished_at')} | {
            'usage':{k:(x.get('usage') or {}).get(k) for k in ('prompt_tokens','completion_tokens','total_tokens','status')}} for x in calls]}
    c.save(OUT/'summary.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','calls')},ensure_ascii=True))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('prepare','offline','start','query','snapshot','stop','service','summarize'))
    p.add_argument('kind',nargs='?');p.add_argument('--cap',type=int)
    a=p.parse_args()
    if a.command=='prepare':prepare()
    elif a.command=='start':start()
    elif a.command=='offline':c.run(offline())
    elif a.command=='query':c.run(query(a.kind,a.cap))
    elif a.command=='snapshot':c.run(c.snapshot())
    elif a.command=='stop':
        c.P31_RUNNER=Path(__file__).resolve()
        c.stop()
    elif a.command=='service':service()
    elif a.command=='summarize':c.run(summarize())

if __name__=='__main__':main()
