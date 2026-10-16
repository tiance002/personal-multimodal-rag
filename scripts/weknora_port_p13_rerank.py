"""Explicit P13 frozen rerank experiment through the unchanged trusted runtime.

No question generation, index changes, provider fallback or uncertain retry.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]


def wire(body):
    return json.dumps(body,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode('utf-8')


def require(ok,code):
    if not ok:raise ValueError(code)


def validate(plan):
    p=plan['proposal'];cases=p['cases'];pool=p['pool'];slot=p['target_slot']
    require(p['request_cap']==6 and len(cases)==6,'P13_EXACT_SIX_FROZEN_CASES')
    require(len({c['case_id'] for c in cases})==6,'P13_DUPLICATE_CASE')
    require(len(pool)==31 and [r['index'] for r in pool]==list(range(31)),'P13_POOL_ORDER')
    require(slot==14 and len({r['candidate_id'] for r in pool})==len(pool),'P13_CANDIDATE_IDENTITY')
    original=[r['text'] for r in pool];context=list(original);context[slot]=p['context_bundle']['text']
    require(original[slot] in context[slot] and original[slot]!=context[slot],'P13_ORIGINAL_TARGET_NOT_RETAINED')
    expected={(q,l,v) for q in ['row-0434'] for l in ['EN','ZH'] for v in ['original','context']}|{('row-0959',l,'original') for l in ['EN','ZH']}
    require({(c['qid'],c['language'],c['target_text_variant']) for c in cases}==expected,'P13_FACTOR_DESIGN')
    for c in cases:
        body=c['body'];require(set(body)=={'model','query','documents'},'P13_UNEXPECTED_BODY_FIELDS')
        require(body['model']=='BAAI/bge-reranker-v2-m3' and body['query']==c['query'],'P13_MODEL_QUERY_MISMATCH')
        require(body['documents']==(original if c['target_text_variant']=='original' else context),'P13_POOL_MUTATION')
        raw=wire(body);require(len(raw)==c['wire_bytes'] and hashlib.sha256(raw).hexdigest()==c['wire_sha256'],'P13_WIRE_MISMATCH')
        require(c['candidate_id_order']==[r['candidate_id'] for r in pool],'P13_ORDER_MUTATION')
    for edge in p['single_factor_edges']:
        a,b=[next(c for c in cases if c['case_id']==name) for name in edge['cases']]
        require(a['qid']==b['qid'],'P13_CROSS_QID_FACTOR_EDGE')
        require(sum(a[k]!=b[k] for k in ['query','target_text_variant'])==1,'P13_NOT_SINGLE_FACTOR')
    require(sum(c['wire_bytes'] for c in cases)<=120000,'P13_FENCE_TOO_SMALL')
    require(len({c['query'] for c in cases if c['qid']=='row-0434'})==2,'P13_QUERY_DRIFT')
    return p


def exclusive(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,default=str);f.flush();os.fsync(f.fileno())


def claim(root,case):
    d=root/'trials'/case['case_id'];exclusive(d/'intent.json',case);return d


async def execute(plan_path):
    from scripts.weknora_port_p8_real import dsn
    from backend.weknora_port.authority import load_authority_settings
    from backend.weknora_port.development import DevelopmentSettings,assemble
    from backend.weknora_port.models.types import CallContext,TrustedScope
    from backend.weknora_port.models.provider import build_rerank_body
    from scripts.weknora_port_p9_observed_api import install_wire_observer
    plan=json.loads(plan_path.read_text(encoding='utf-8'));p=validate(plan);out=plan_path.parent
    require(out.resolve()==Path('E:/WK/run/p13-20261016').resolve(),'P13_EVIDENCE_ROOT')
    authority=out/'authority.json';require(hashlib.sha256(authority.read_bytes()).hexdigest()==plan['authority_sha256'],'P13_AUTHORITY_CHANGED')
    cfg=load_authority_settings(authority);profile=plan['runtime_profile']
    require(cfg.limit_tokens==1357596 and cfg.allowed_models==(('siliconflow','BAAI/bge-reranker-v2-m3'),),'P13_AUTHORITY_SCOPE')
    settings=DevelopmentSettings(dsn(),'7696088403504697382','weknora_python_port_20261013',
        'redis://127.0.0.1:56424/15',profile['redis_run_id'],profile['redis_prefix'],Path(profile['storage_root']),
        Path(profile['secret_key_file']),Path('D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken'),router_mode='CHEAP_ONLY',fallback_strategy='fixed')
    async with assemble(settings,authority_settings=cfg) as app:
        require(cfg.tenant_id==332 and cfg.subject_id==app.principal.subject_id,'P13_PRINCIPAL_MISMATCH')
        registry=app.registry_for_context();scope=TrustedScope(app.principal.subject_id,(p['scope']['kb_id'],),())
        context=CallContext('p13-preflight',scope)
        model=await registry.rerank(plan['model_id'],context)
        require(model.config.provider=='siliconflow' and model.config.model=='BAAI/bge-reranker-v2-m3' and model.config.rerank_api=='cohere-rerank','P13_MODEL_IDENTITY')
        require(model.config.reranking.max_documents==0 and model.config.reranking.score_scale=='probability','P13_UNEXPECTED_BATCH_OR_SCALE')
        install_wire_observer(model,out)
        current={}
        async def guard_hook(request):
            require(current.get('sends',0)==0,'P13_MULTIPLE_SENDS_DENIED')
            require(hashlib.sha256(request.content).hexdigest()==current['case']['wire_sha256'],'P13_ACTUAL_WIRE_CHANGED')
            current['sends']=1
        async def raw_hook(response):
            raw=await response.aread();path=current['dir']/'response.body'
            with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            exclusive(current['dir']/'response-meta.json',{'status_code':response.status_code,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'headers_saved':False})
        model.transport.client.event_hooks['request'].insert(0,guard_hook)
        model.transport.client.event_hooks.setdefault('response',[]).append(raw_hook)
        try:
            for c in p['cases']:
                expected=wire(build_rerank_body(model.config,c['query'],c['body']['documents']))
                require(hashlib.sha256(expected).hexdigest()==c['wire_sha256'],'P13_TRUSTED_BODY_MISMATCH')
                docs=await app._rows('SELECT id,parse_status,deleted_at,metadata FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s',(332,p['scope']['kb_id']))
                versions={r['id']:(json.loads(r['metadata']) if isinstance(r['metadata'],str) else r['metadata'])['source_version'] for r in docs if r['deleted_at'] is None and r['parse_status']=='completed'}
                require(all(versions.get(r['provenance']['document_id'])==r['provenance']['source_version'] for r in p['pool']),'P13_STALE_SOURCE')
                months=await app._rows("SELECT * FROM port_token_budget_months WHERE account_id='siliconflow-owner-main' AND provider='siliconflow' AND month='2026-10-01'")
                calls=await app._rows("SELECT state,actual_total,held_tokens FROM port_token_calls WHERE account_id='siliconflow-owner-main' AND budget_provider='siliconflow' AND month='2026-10-01'")
                held=sum((r['actual_total'] or 0) if r['state']=='settled' else r['held_tokens'] if r['state'] in ('unknown','reserved') else 0 for r in calls)
                require(months[0]['limit_tokens']==1357596 and not months[0]['blocked_reason'] and 1357596-14-held>=len(expected),'P13_BUDGET_STOP')
                d=claim(out,c);current.clear();current.update(case=c,dir=d,sends=0)
                exclusive(d/'preflight.json',{'remaining':1357596-14-held,'required_wire_bound':len(expected),'source_versions':versions,'configured_model_id':model.config.model_id,'model':model.config.model,'protocol':model.config.rerank_api,'score_scale':model.config.reranking.score_scale})
                started=time.monotonic();ctx=CallContext(plan['request_ids'][c['case_id']],scope,deadline=asyncio.get_running_loop().time()+300,deadline_kind='run')
                try:
                    result=await model.rerank(c['query'],c['body']['documents'],ctx)
                    exclusive(d/'adapted.json',asdict(result))
                    raw=json.loads((d/'response.body').read_bytes());raw_rows=raw['results']
                    expected_pairs=[(r['index'],float(r.get('relevance_score',r.get('score',0)))) for r in raw_rows]
                    require(expected_pairs==[(r.index,r.score) for r in result.results],'P13_RAW_ADAPTER_MISMATCH')
                    require(sorted(r.index for r in result.results)==list(range(31)),'P13_RESPONSE_INDEX_MISMATCH')
                    exclusive(d/'result.json',{'status':'RECORDED','request_id':ctx.request_id,'seconds':time.monotonic()-started,'physical_send_hooks':current['sends'],'raw_adapter_equal':True,'index_bijection':True})
                    print(c['case_id'],'RECORDED',len(result.results),flush=True)
                except BaseException as exc:
                    exclusive(d/'failure.json',{'status':'FAILED_NO_RETRY','type':type(exc).__name__,'safe_code':getattr(exc,'code',''),'seconds':time.monotonic()-started,'physical_send_hooks':current['sends']})
                    raise
        finally:await registry.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--frozen',type=Path,required=True);parser.add_argument('--validate-only',action='store_true');a=parser.parse_args()
    if a.validate_only:
        p=validate(json.loads(a.frozen.read_text(encoding='utf-8')));print('VALIDATED',len(p['cases']),'wire_bound',sum(c['wire_bytes'] for c in p['cases']));return
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(execute(a.frozen))

if __name__=='__main__':main()
