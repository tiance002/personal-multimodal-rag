"""P14-only frozen machine-context Rerank verification; no production integration."""
from __future__ import annotations
import argparse,asyncio,hashlib,json,time,os
from dataclasses import asdict
from pathlib import Path
from scripts.weknora_port_p13_rerank import wire,require,exclusive,claim,check_source_versions
from scripts.weknora_port_p14_fence import OPEN_LIMIT,OLD_LIMIT


def binding(row):
    fields={k:row.get(k) for k in ('id','knowledge_id','knowledge_base_id','chunk_type','is_enabled','start_at','end_at','content','metadata')}
    return hashlib.sha256(wire(fields)).hexdigest()


def validate(plan):
    p=plan['proposal'];pool=p['pool'];cases=p['cases'];pools=p['pools']
    require(p['request_cap']==4 and len(cases)==4,'P14_EXACT_FOUR_CASES')
    require(len(pool)==31 and [r['index'] for r in pool]==list(range(31)),'P14_POOL_ORDER')
    require(len({r['candidate_id'] for r in pool})==31,'P14_POOL_IDENTITY')
    require(set(pools)=={'original','near','length'},'P14_VARIANTS')
    require(pools['original']==[r['text'] for r in pool],'P14_ORIGINAL_MUTATED')
    for name in ['near','length']:
        require(len(pools[name])==31,'P14_POOL_LENGTH')
        for i,(original,text) in enumerate(zip(pools['original'],pools[name])):
            require(isinstance(text,str) and original in text,'P14_ANCHOR_NOT_RETAINED')
            require(len(text)<=1200,'P14_TEXT_CAP')
            if pool[i]['generated_candidate']:require(text==original,'P14_GENERATED_EXPANSION')
    require([len(t) for t in pools['near']]==[len(t) for t in pools['length']],'P14_LENGTH_CONTROL_MISMATCH')
    expected={('row-0434','original'),('row-0434','near'),('row-0434','length'),('row-0959','near')}
    require(len({c['case_id'] for c in cases})==4 and {(c['qid'],c['pool_variant']) for c in cases}==expected,'P14_CASE_DESIGN')
    for c in cases:
        require(c['query']==plan['queries'][c['qid']],'P14_QUERY_DRIFT')
        expected_body={'model':'BAAI/bge-reranker-v2-m3','query':c['query'],'documents':pools[c['pool_variant']]}
        require(c['body']==expected_body,'P14_BODY_MISMATCH')
        raw=wire(c['body']);require(len(raw)==c['wire_bytes'] and hashlib.sha256(raw).hexdigest()==c['wire_sha256'],'P14_WIRE_CHANGED')
        require(c['candidate_id_order']==[r['candidate_id'] for r in pool],'P14_ORDER_CHANGED')
    require(sum(c['wire_bytes'] for c in cases)<=OPEN_LIMIT-OLD_LIMIT,'P14_FENCE_TOO_SMALL')
    return p


async def execute(path):
    from scripts.weknora_port_p8_real import dsn
    from backend.weknora_port.authority import load_authority_settings
    from backend.weknora_port.development import DevelopmentSettings,assemble
    from backend.weknora_port.models.types import CallContext,TrustedScope
    from backend.weknora_port.models.provider import build_rerank_body
    from scripts.weknora_port_p9_observed_api import install_wire_observer
    plan=json.loads(path.read_text(encoding='utf-8'));p=validate(plan);out=path.parent
    require(out.resolve()==Path('E:/WK/run/p14-20261016').resolve(),'P14_EVIDENCE_ROOT')
    authority=out/'authority.json';require(hashlib.sha256(authority.read_bytes()).hexdigest()==plan['authority_sha256'],'P14_AUTHORITY_CHANGED')
    cfg=load_authority_settings(authority);profile=plan['runtime_profile']
    require(cfg.limit_tokens==OPEN_LIMIT and cfg.allowed_models==(('siliconflow','BAAI/bge-reranker-v2-m3'),),'P14_AUTHORITY_SCOPE')
    settings=DevelopmentSettings(dsn(),'7696088403504697382','weknora_python_port_20261013','redis://127.0.0.1:56424/15',profile['redis_run_id'],profile['redis_prefix'],Path(profile['storage_root']),Path(profile['secret_key_file']),Path('D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken'),router_mode='CHEAP_ONLY',fallback_strategy='fixed')
    async with assemble(settings,authority_settings=cfg) as app:
        require(cfg.tenant_id==332 and cfg.subject_id==app.principal.subject_id,'P14_PRINCIPAL')
        registry=app.registry_for_context();scope=TrustedScope(app.principal.subject_id,(p['scope']['kb_id'],),())
        model=await registry.rerank(plan['model_id'],CallContext('p14-preflight',scope))
        require(model.config.provider=='siliconflow' and model.config.model=='BAAI/bge-reranker-v2-m3' and model.config.rerank_api=='cohere-rerank' and model.config.reranking.score_scale=='probability' and model.config.reranking.max_documents==0 and model.config.reranking.max_request_chars==0 and model.config.reranking.max_document_chars==0,'P14_MODEL_OR_PROTOCOL_CHANGED')
        install_wire_observer(model,out);current={}
        async def guard_hook(request):
            require(current.get('sends',0)==0,'P14_MULTIPLE_SENDS_DENIED');require(hashlib.sha256(request.content).hexdigest()==current['case']['wire_sha256'],'P14_ACTUAL_WIRE_CHANGED');current['sends']=1
        async def raw_hook(response):
            raw=await response.aread()
            with (current['dir']/'response.body').open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            exclusive(current['dir']/'response-meta.json',{'status_code':response.status_code,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'headers_saved':False})
        model.transport.client.event_hooks['request'].insert(0,guard_hook);model.transport.client.event_hooks.setdefault('response',[]).append(raw_hook)
        try:
            for c in p['cases']:
                expected=wire(build_rerank_body(model.config,c['query'],c['body']['documents']));require(hashlib.sha256(expected).hexdigest()==c['wire_sha256'],'P14_TRUSTED_BODY_MISMATCH')
                docs=await app._rows('SELECT id,parse_status,deleted_at,metadata FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s',(332,p['scope']['kb_id']))
                versions={r['id']:(json.loads(r['metadata']) if isinstance(r['metadata'],str) else r['metadata'])['source_version'] for r in docs if r['deleted_at'] is None and r['parse_status']=='completed'}
                check_source_versions(p,plan['document_versions'],versions)
                source=await app._rows('SELECT * FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s',(332,p['scope']['kb_id']));byid={r['id']:r for r in source if r['deleted_at'] is None}
                require(all(id in byid and binding(byid[id])==value for id,value in plan['source_chunk_bindings'].items()),'P14_SOURCE_CONTENT_CHANGED')
                months=await app._rows("SELECT * FROM port_token_budget_months WHERE account_id='siliconflow-owner-main' AND provider='siliconflow' AND month='2026-10-01'")
                calls=await app._rows("SELECT state,actual_total,held_tokens FROM port_token_calls WHERE account_id='siliconflow-owner-main' AND budget_provider='siliconflow' AND month='2026-10-01'")
                held=sum((r['actual_total'] or 0) if r['state']=='settled' else r['held_tokens'] if r['state'] in ('unknown','reserved') else 0 for r in calls)
                require(months[0]['limit_tokens']==OPEN_LIMIT and not months[0]['blocked_reason'] and OPEN_LIMIT-14-held>=len(expected),'P14_BUDGET_STOP')
                d=claim(out,c);current.clear();current.update(case=c,dir=d,sends=0)
                exclusive(d/'preflight.json',{'remaining':OPEN_LIMIT-14-held,'required_wire_bound':len(expected),'document_versions':versions,'source_chunk_bindings_checked':len(plan['source_chunk_bindings']),'model_id':model.config.model_id,'model':model.config.model,'protocol':model.config.rerank_api,'score_scale':model.config.reranking.score_scale})
                started=time.monotonic();ctx=CallContext(plan['request_ids'][c['case_id']],scope,deadline=asyncio.get_running_loop().time()+300,deadline_kind='run')
                try:
                    result=await model.rerank(c['query'],c['body']['documents'],ctx);exclusive(d/'adapted.json',asdict(result));raw=json.loads((d/'response.body').read_bytes())
                    require([(r['index'],float(r.get('relevance_score',r.get('score',0)))) for r in raw['results']]==[(r.index,r.score) for r in result.results],'P14_RAW_ADAPTER_MISMATCH');require(sorted(r.index for r in result.results)==list(range(31)),'P14_INDEX_BIJECTION')
                    exclusive(d/'result.json',{'status':'RECORDED','request_id':ctx.request_id,'seconds':time.monotonic()-started,'physical_send_hooks':current['sends'],'raw_adapter_equal':True,'index_bijection':True});print(c['case_id'],'RECORDED31',flush=True)
                except BaseException as exc:
                    exclusive(d/'failure.json',{'status':'FAILED_NO_RETRY','type':type(exc).__name__,'safe_code':getattr(exc,'code',''),'seconds':time.monotonic()-started,'physical_send_hooks':current['sends']});raise
        finally:await registry.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True);p.add_argument('--validate-only',action='store_true');a=p.parse_args()
    if a.validate_only:
        data=validate(json.loads(a.frozen.read_text(encoding='utf-8')));print('VALIDATED4 wire_bound',sum(c['wire_bytes'] for c in data['cases']));return
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(execute(a.frozen))

if __name__=='__main__':main()
