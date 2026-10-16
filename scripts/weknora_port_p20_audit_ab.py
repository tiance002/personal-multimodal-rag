"""P20 frozen-answer A/B: A unchanged historical answer; B adds one Cheap audit.

Four maximum audit sends. No answer regeneration, retrieval, repair or publication.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from scripts.weknora_port_p13_rerank import wire,require,exclusive,claim
from scripts.weknora_port_p20_evidence import AUDIT_SYSTEM,validate_audit


def validate(p):
    require(len(p['cases'])==4 and len({c['case_id'] for c in p['cases']})==4,'P20_FOUR_FROZEN_CASES')
    require(len({c['request_id'] for c in p['cases']})==4,'P20_REQUEST_IDENTITIES')
    require(p['factor']=='ADD_STRUCTURED_POST_GENERATION_AUDIT','P20_FACTOR')
    for c in p['cases']:
        require(c['body']['model']=='XingChenAGI/Xing4.0-29B','P20_MODEL')
        require(c['body']['messages'][0]==dict(role='system',content=AUDIT_SYSTEM),'P20_AUDIT_CONTRACT_CHANGED')
        data=json.loads(c['body']['messages'][1]['content'])
        require(data==dict(question=c['question'],units=[dict(unit_id=u['unit_id'],text=u['text']) for u in c['units']],
            sources=[dict(handle=h,text=v['text']) for h,v in c['sources'].items()]),'P20_DATA_CHANGED')
        require(hashlib.sha256(c['original_answer'].encode()).hexdigest()==c['original_answer_sha256'],'P20_ORIGINAL_ANSWER_CHANGED')
        require(hashlib.sha256(wire(c['body'])).hexdigest()==c['wire_sha256'],'P20_WIRE_CHANGED')
        require(set(c['body'])=={'model','messages','temperature','max_tokens','stream','stream_options','enable_thinking'}
                and c['body']['enable_thinking'] is False,'P20_BODY_FIELDS')
    require(sum(len(wire(c['body']))+c['body']['max_tokens'] for c in p['cases'])<=100000,'P20_LOCAL_FENCE_BOUND')


async def execute(path):
    from scripts.weknora_port_p8_real import dsn
    from scripts.weknora_port_p14_rerank import binding
    from scripts.weknora_port_p17_observed_api import RawResponse,journal
    from backend.weknora_port.authority import load_authority_settings
    from backend.weknora_port.development import DevelopmentSettings,assemble
    from backend.weknora_port.models import Message,ChatOptions,CallContext,TrustedScope
    from backend.weknora_port.models.request import build_chat_body
    root=path.parent;p=json.loads(path.read_text(encoding='utf-8'));validate(p)
    require(root.resolve()==Path('E:/WK/run/p20-20261016').resolve(),'P20_ROOT')
    require(hashlib.sha256((root/'authority.json').read_bytes()).hexdigest()==p['authority_sha256'],'P20_AUTHORITY_CHANGED')
    cfg=load_authority_settings(root/'authority.json');s=p['runtime_profile']
    require(cfg.limit_tokens==1741652 and cfg.allowed_models==(('siliconflow','XingChenAGI/Xing4.0-29B'),),'P20_AUTHORITY_SCOPE')
    settings=DevelopmentSettings(dsn(),'7696088403504697382','weknora_python_port_20261013',
        'redis://127.0.0.1:56424/15',s['redis_run_id'],s['redis_prefix'],Path(s['storage_root']),
        Path(s['secret_key_file']),Path('D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken'),router_mode='CHEAP_ONLY',fallback_strategy='fixed')
    async with assemble(settings,authority_settings=cfg) as app:
        registry=app.registry_for_context()
        try:
            for case in p['cases']:
                directory=root/'trials'/case['case_id']
                # Refuse replay even if a result was lost after sending.
                require(not (directory/'intent.json').exists(),'P20_PRIOR_INTENT_NO_RETRY')
                kb=await app._rows('SELECT tenant_id,cloud_allowed,deleted_at FROM knowledge_bases WHERE id=%s',(case['kb_id'],))
                require(kb and kb[0]['tenant_id']==332 and kb[0]['cloud_allowed'] and not kb[0]['deleted_at'],'P20_KB_PERMISSION_CHANGED')
                docs=await app._rows('SELECT id,metadata,parse_status,deleted_at FROM knowledges WHERE knowledge_base_id=%s',(case['kb_id'],))
                versions={r['id']:(json.loads(r['metadata']) if isinstance(r['metadata'],str) else r['metadata'])['source_version'] for r in docs if not r['deleted_at'] and r['parse_status']=='completed'}
                require(versions==case['document_versions'],'P20_VERSION_CHANGED')
                chunks=await app._rows('SELECT * FROM chunks WHERE knowledge_base_id=%s AND tenant_id=332',(case['kb_id'],))
                byid={r['id']:r for r in chunks if not r['deleted_at']}
                require(all(i in byid and binding(byid[i])==h for i,h in case['source_chunk_bindings'].items()),'P20_CHUNK_CHANGED')
                scope=TrustedScope(app.principal.subject_id,(case['kb_id'],),())
                model=await registry.chat(case['model_id'],CallContext(case['request_id']+'/preflight',scope))
                require((model.config.provider,model.config.model)==('siliconflow','XingChenAGI/Xing4.0-29B'),'P20_MODEL_IDENTITY')
                body=case['body'];messages=tuple(Message(m['role'],m['content']) for m in body['messages'])
                opts=ChatOptions(temperature=body['temperature'],max_tokens=body['max_tokens'],reasoning_effort='off')
                require(build_chat_body(model.config,messages,opts,stream=True)==body,'P20_BODY_RENDER_CHANGED')
                directory=claim(root,case);sends=0;original=model.transport.open
                @asynccontextmanager
                async def observed(config,endpoint,request_body,ctx,**kwargs):
                    nonlocal sends
                    require(sends==0 and kwargs['capability']=='chat','P20_EXTRA_SEND_DENIED')
                    require(wire(request_body)==wire(body),'P20_BODY_CHANGED')
                    sends+=1
                    async with original(config,endpoint,request_body,ctx,**kwargs) as handle:
                        raw=RawResponse(handle.response,root,ctx.request_id,'chat');handle.response=raw
                        try:yield handle
                        finally:raw.finish()
                async def hook(request):
                    require(hashlib.sha256(request.content).hexdigest()==case['wire_sha256'],'P20_ACTUAL_WIRE_CHANGED')
                    exclusive(directory/'request.body.json',json.loads(request.content))
                model.transport.open=observed;model.transport.client.event_hooks['request'].insert(0,hook)
                events=[];parts=[];finish='';t=time.monotonic()
                try:
                    ctx=CallContext(case['request_id'],scope,deadline=asyncio.get_running_loop().time()+300,deadline_kind='run')
                    async for event in model.chat_stream(messages,opts,ctx):
                        events.append(asdict(event));journal(root,ctx.request_id,dict(kind='adapter_event',**asdict(event)))
                        if event.finish_reason:finish=event.finish_reason
                        if event.response_type=='error':raise RuntimeError('P20_PROVIDER_ERROR_NO_RETRY')
                        if event.response_type=='answer':parts.append(event.content)
                    output=''.join(parts)
                    checked=validate_audit(output,case['units'],case['sources'],finish_reason=finish,original_citations=case['original_citations'])
                    exclusive(directory/'events.json',events)
                    exclusive(directory/'result.json',dict(raw_audit=output,finish_reason=finish,seconds=time.monotonic()-t,physical_sends=sends,validation=checked,original_answer_unchanged=True))
                    print(case['case_id'],'RECORDED','valid',checked['structurally_valid'],'candidate_retained',checked['experimental_candidate_retained'],flush=True)
                except BaseException as error:
                    exclusive(directory/'failure.json',dict(type=type(error).__name__,safe_code=getattr(error,'code',''),physical_sends=sends,no_retry=True));raise
                finally:
                    model.transport.open=original;model.transport.client.event_hooks['request'].remove(hook)
        finally:await registry.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--frozen',type=Path,required=True);p.add_argument('--validate-only',action='store_true');a=p.parse_args()
    if a.validate_only:validate(json.loads(a.frozen.read_text(encoding='utf-8')));print('P20_FROZEN4_VALID');return
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(execute(a.frozen))


if __name__=='__main__':main()
