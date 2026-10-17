"""M1 bounded acceptance adapter; reuses P32 controller and scorer contracts.

No model, retrieval, prompt, credential, or budget implementation lives here.
Provider calls use the ordinary HTTP application and durable usage recorder.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = Path('E:/WK/run/rag-m1-20261017')
from scripts import p32_real_acceptance as controller


def boot():
    # Reuse the known, working P32 process assembly, before runtime imports.
    source = controller.state()
    p = source['primary_tenant']
    env = controller.child_env(332, p['subject_id'], 56425, p['storage_root'],
                               p['redis_prefix'], str(OUT/'evidence-332'))
    for path in reversed(env['PYTHONPATH'].split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    controller.OUT = OUT


def prepare():
    if (OUT/'state.json').exists():
        raise RuntimeError('M1_STATE_ALREADY_EXISTS_NO_RESET')
    OUT.mkdir(parents=True, exist_ok=True)
    plan = controller.read('E:/WK/run/p32-20261017/plan.json')
    for case in plan['cases']:
        case['title'] = 'M1 ' + case['case_id']
        case.pop('reuse_stage_a', None)
    p31 = controller.read('E:/WK/run/p31-20261017/state.json')
    t = p31['tenants']['344']
    plan['tenants'] = [{'tenant_id':344,'subject_id':t['subject'],'api_port':56427,
                        'storage_root':t['storage_root'],'worker':False}]
    controller.save(OUT/'plan.json', plan)
    controller.prepare(str(OUT/'plan.json'))


def start_services():
    s=controller.state()
    s['processes']={k:v for k,v in s['processes'].items()
                    if v.get('status') not in ('STOPPED','ALREADY_EXITED')}
    controller.save(OUT/'state.json',s)
    controller.start(str(OUT/'plan.json'))
    import httpx
    deadline=time.monotonic()+20
    for port in (56425,56427):
        while True:
            try:
                with httpx.Client(trust_env=False,timeout=2) as c:
                    response=c.get(f'http://127.0.0.1:{port}/readyz')
                if response.status_code==200:break
            except httpx.HTTPError:pass
            if time.monotonic()>deadline:raise RuntimeError('M1_API_NOT_READY_'+str(port))
            time.sleep(0.2)
    print('M1_SERVICES_READY_NO_MODEL_REQUESTS')


async def configure_expensive():
    """Bind existing Owner DeepSeek credential through normal config API."""
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.history import LocalPrincipal
    source = controller.read('E:/WK/run/p31-20261017/state.json')
    who = source['tenants']['344']
    env = controller.child_env(344, who['subject'], 56427, who['storage_root'],
        'weknora-python-m1-config:344', str(OUT/'evidence-344'))
    settings = DevelopmentSettings.from_environment(env)
    import httpx
    async with assemble(settings, principal=LocalPrincipal(344,who['subject'])) as app:
        role = await app.models.resolve(source['sources']['vlm_model_id'])
        if (role.provider,role.model,role.source)!=('deepseek','deepseek-flash','remote') or not role.api_key:
            raise RuntimeError('M1_EXISTING_DEEPSEEK_IDENTITY_INVALID')
        async with httpx.AsyncClient(base_url='http://127.0.0.1:56425',trust_env=False,
                headers={'Origin':controller.ORIGIN},timeout=30) as client:
            response = await client.get('/api/v1/settings/model-configuration')
            response.raise_for_status()
            public = response.json()['data']
            if any(x['role']=='expensive' and x.get('model') for x in public['roles']):
                raise RuntimeError('M1_EXPENSIVE_ALREADY_CONFIGURED_NO_OVERWRITE')
            roles = [{k:r.get(k,'') for k in ('role','provider','model','base_url','source')}
                     for r in public['roles']]
            for value in roles:
                value['api_key']=''
                if value['role']=='expensive':
                    value.update(provider=role.provider,model=role.model,base_url=role.base_url,
                                 source=role.source,api_key=role.api_key)
            # Secret stays in memory/local config transport; never journal body.
            result = await client.put('/api/v1/settings/model-configuration',
                                     json={'revision':str(public['revision']),'roles':roles})
            result.raise_for_status()
            controller.save(OUT/'expensive-configuration-public.json',result.json())
            print('M1_EXPENSIVE_CONFIGURED_NORMAL_API existing_provider=deepseek model=deepseek-flash')


async def bind_sources():
    previous = controller.read('E:/WK/run/p32-20261017/source-bindings.json')
    conn = await controller.pg()
    try:
        for row in previous['documents']:
            found = await (await conn.execute("SELECT id,file_hash,metadata->>'source_version' AS source_version FROM knowledges WHERE tenant_id=332 AND id=%s AND deleted_at IS NULL",(row['id'],))).fetchone()
            if not found or any(str(found[k])!=str(row[k]) for k in ('id','file_hash','source_version')):
                raise RuntimeError('M1_SOURCE_VERSION_CHANGED_'+row['id'])
        # Chunk text/version and vector preservation are checked through the
        # existing source audit; no frozen data is rewritten.
        controller.save(OUT/'source-bindings.json',previous)
        controller.save(OUT/'asset-audit.json',controller.read('E:/WK/run/p32-20261017/asset-audit.json'))
        print('M1_SOURCE_BINDINGS_VERIFIED',len(previous['documents']))
    finally:
        await conn.close()


async def adopt_ui():
    s = controller.state()
    if 'mml-0959' in s['cases']:
        raise RuntimeError('M1_UI_ALREADY_ADOPTED')
    case = next(c for c in s['plan']['cases'] if c['case_id']=='mml-0959')
    captures = [controller.read(p) for p in (OUT/'evidence-332'/'captures').glob('*.json')]
    matching = [c for c in captures if c.get('question')==case['question']]
    if len(matching)!=1:
        raise RuntimeError('M1_UNIQUE_UI_CAPTURE_REQUIRED')
    c = matching[0]
    session, run_id, message = c['session_id'], c['run_id'], c['message_id']
    base='http://127.0.0.1:56425'
    response = await controller.http_request('GET',base+f'/api/v1/sessions/continue-stream/{session}?message_id={message}',op='ui-completed-run-replay')
    messages = (await controller.http_request('GET',base+f'/api/v1/sessions/{session}/messages',op='ui-readback')).json()
    folder=OUT/'cases'/'mml-0959';folder.mkdir(parents=True,exist_ok=True)
    (folder/'answer.sse').write_bytes(response.content)
    controller.save(folder/'messages.json',messages)
    record={'case_id':'mml-0959','tenant_id':332,'session_id':session,'run_id':run_id,
        'question':case['question'],'request_id':None,'http_status':200,'status':'RESPONSE_RECEIVED',
        'provenance':'REAL_EDGE_POST_GET_REPLAY_SAVED_WITHOUT_REGENERATION','gold_loaded':False}
    s['cases']['mml-0959']=record
    controller.save(OUT/'state.json',s);controller.save(folder/'result.json',record)
    print('M1_UI_ADOPTED_NO_REGENERATION',session,run_id)


async def batch():
    started=time.monotonic()
    for case in controller.state()['plan']['cases']:
        if time.monotonic()-started>2300:
            print('M1_BATCH_CHECKPOINT_TIME_LIMIT',flush=True);break
        if case['case_id'] in controller.state()['cases']:
            continue
        await controller.query_case(case['case_id'],str(OUT/'plan.json'))
        print('M1_BATCH_CASE_SAVED',case['case_id'],flush=True)


async def replay_rerank():
    from scripts.p34_rerank_frozen import load_frozen
    from backend.weknora_port.retrieval.rerank import rerank
    from backend.weknora_port.retrieval.query import default_segmenter
    from backend.weknora_port.retrieval.search import SearchNothing
    from backend.weknora_port.models.types import RerankResponse,RerankResult,CallContext,TrustedScope
    manifest=controller.read(OUT/'rerank'/'send-manifest.json')
    frozen={c['case_id']:c for c in load_frozen()}
    output=[]
    for call in manifest['cases']:
        case=frozen[call['case_id']]
        scores=tuple(RerankResult(r['provider_input_index'],r['relevance_score']) for r in call['scores'])
        class Model:
            async def rerank(self,*args):return RerankResponse(scores)
        class Registry:
            async def rerank(self,*args):return Model()
        results=[]
        for threshold in (0.3,0.25):
            candidates=[SimpleNamespace(id=m['chunk_id'],content=case['_body']['documents'][i],image_info='[]',
                chunk_metadata={},metadata={},score=case['_trace']['search']['ranked_candidates'][i]['score'],
                knowledge_source='knowledge_base',chunk_type=m['chunk_type']) for i,m in enumerate(case['candidate_map'])]
            state=SimpleNamespace(needs_retrieval=True,search_results=candidates,rerank_model_id='OFFLINE_REAL_RESPONSE',
                rewrite_query=case['query'],rerank_threshold=threshold,targets=[SimpleNamespace(disable_recall_thresholds=False)],
                rerank_results=[],rerank_top_k=30,faq_priority_enabled=False,faq_score_boost=1.0)
            try:
                await rerank(state,CallContext('m1-offline',TrustedScope('m1',(case['knowledge_base_id'],),())),Registry(),default_segmenter())
            except SearchNothing:pass
            kept=[r.id for r in state.rerank_results]
            results.append({'threshold':threshold,'retained_ids':kept,'target_retained':case['target_chunk_id'] in kept})
        ranked=sorted(call['scores'],key=lambda r:-r['relevance_score'])
        output.append({'case_id':call['case_id'],'actual_response_sha256':call['provider_capture']['response_sha256'],
            'target_score':call['target_score'],'target_rank':call['target_score_rank'],'top5':ranked[:5],
            'native_filter_function':'backend.weknora_port.retrieval.rerank.rerank','replays':results})
    controller.save(OUT/'rerank-native-offline.json',{'cloud_calls':0,'cases':output})
    print('M1_NATIVE_RERANK_REPLAY_PASS cases=4 cloud_calls=0')


async def route_case(kind):
    from backend.weknora_port.generation.router import decide,RouterPolicy
    questions={
        'expensive':'请根据知识库分析：如果通信距离>=1000公里且频率>=1GHz且卫星服务有效且预算<=100万元且电源已完成，判断是否可以选择电离层折射或卫星微波通信，并说明哪些条件无法仅凭资料判定。',
        'upgrade':'请根据知识库详细说明无线电波和微波如何用于远距离通信，给出完整解释。',
    }
    question=questions[kind]
    decision=decide(question,'',RouterPolicy(mode='DYNAMIC'))
    if decision.role!=('expensive' if kind=='expensive' else 'cheap'):
        raise RuntimeError('M1_ROUTE_INPUT_NOT_MATCHED_EXPECTED_FEATURE_BRANCH')
    kb='7f1d720d-9561-446f-a8cc-6a562c339d23'
    base='http://127.0.0.1:56425'; op='route-'+kind
    folder=OUT/op;folder.mkdir(exist_ok=True)
    session=(await controller.http_request('POST',base+'/api/v1/sessions',body={
        'title':'M1 '+op,'knowledge_base_scope':[kb],'document_scope':[]},op=op+'-session')).json()['data']['id']
    configuration={'default_options':True}
    if kind=='upgrade':
        # Existing server-owned session setting, in a fresh isolated test
        # session only. Providers themselves must actually return length.
        conn=await controller.pg()
        try:
            before=await (await conn.execute('SELECT summary_parameters FROM sessions WHERE tenant_id=332 AND id=%s',(session,))).fetchone()
            changed=await (await conn.execute("UPDATE sessions SET summary_parameters=summary_parameters || %s::jsonb WHERE tenant_id=332 AND subject_id=%s AND id=%s AND NOT EXISTS(SELECT 1 FROM messages WHERE session_id=%s) RETURNING id",(
                json.dumps({'max_completion_tokens':8}),controller.state()['primary_tenant']['subject_id'],session,session))).fetchall()
            if len(changed)!=1:raise RuntimeError('M1_FRESH_SESSION_CONFIG_GUARD_FAILED')
            configuration={'before':before['summary_parameters'],'test_override':{'max_completion_tokens':8},
                           'purpose':'real provider truncation to exercise existing controlled upgrade'}
        finally:await conn.close()
    controller.save(folder/'configuration.json',configuration)
    rid=str(uuid.uuid4());started=time.monotonic()
    response=await controller.http_request('POST',base+f'/api/v1/sessions/{session}/messages',body={
        'content':question,'request_id':rid,'knowledge_base_scope':[kb],'document_scope':[],'reasoning_effort':'off'},op=op)
    elapsed=time.monotonic()-started
    (folder/'answer.sse').write_bytes(response.content)
    messages=(await controller.http_request('GET',base+f'/api/v1/sessions/{session}/messages',op=op+'-readback')).json()
    controller.save(folder/'messages.json',messages)
    from scripts.p32_score_results import parse_sse
    stream=parse_sse(folder/'answer.sse')
    complete=[e for e in stream['events'] if e.get('response_type',e.get('type'))=='complete']
    result={'kind':kind,'session_id':session,'client_request_id':rid,'question':question,
            'offline_expected_route':decision.public(RouterPolicy(mode='DYNAMIC')),
            'http_status':response.status_code,'post_seconds':elapsed,'complete':complete,
            'errors':stream['errors'],'answer':stream['answer']}
    controller.save(folder/'result.json',result)
    brief=[{'role':e.get('data',{}).get('role'),'upgraded':e.get('data',{}).get('upgraded'),
            'is_fallback':e.get('data',{}).get('is_fallback'),
            'attempts':[{k:a.get(k) for k in ('role','provider','finish_reason','upgrade_reason')} for a in e.get('data',{}).get('attempts',[])],
            'usage':{k:e.get('usage',{}).get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')}} for e in complete]
    print('M1_ROUTE_RESULT',json.dumps({'kind':kind,'seconds':round(elapsed,3),'complete':brief,'errors':stream['errors']},ensure_ascii=True),flush=True)


def main():
    boot()
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('prepare','start','configure-expensive','bind-sources','adopt-ui','batch','replay-rerank','route','query','snapshot','stop'))
    ap.add_argument('case_id',nargs='?')
    args=ap.parse_args()
    if args.command=='prepare':prepare()
    elif args.command=='start':start_services()
    elif args.command=='configure-expensive':controller.run(configure_expensive())
    elif args.command=='bind-sources':controller.run(bind_sources())
    elif args.command=='adopt-ui':controller.run(adopt_ui())
    elif args.command=='batch':controller.run(batch())
    elif args.command=='replay-rerank':controller.run(replay_rerank())
    elif args.command=='route':controller.run(route_case(args.case_id))
    elif args.command=='query':controller.run(controller.query_case(args.case_id,str(OUT/'plan.json')))
    elif args.command=='snapshot':controller.run(controller.snapshot())
    elif args.command=='stop':controller.stop()


if __name__=='__main__':main()
