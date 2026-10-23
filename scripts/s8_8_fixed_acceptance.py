"""One approved S8.8 fixed input through the normal Registry/SendGate path.

The check command only renders. Execution requires a new Owner grant, a
precreated parent ledger, an existing one-call LocalExecution child and a
trusted fresh read-only source/model proof callback. No grants, ledgers,
Provider adapters, retries or production settings are created here.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.weknora_port.models import ProviderModel, provider_config
from backend.weknora_port.models.types import Message, ChatOptions, CallContext, TrustedScope, ProviderError
from backend.weknora_port.models.output_contract import resolve_output_contract
from backend.weknora_port.models.input_bounds import deepseek_inline_image_context_input
from backend.weknora_port.execution import LocalExecution, REPORTED_USAGE_ONLY, _vision_request_facts, _reported_usage_valid

SCOPE='S8_8_FIXED_INPUT'
PARENT_SCHEMA='s88-fixed-parent/v1'
MAX_CALLS=23
MAX_TOKENS=500000


def require(value,code):
    if not value:raise ValueError(code)


def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
def digest(value):return hashlib.sha256(value if isinstance(value,bytes) else canonical(value)).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf8'))
def file_sha(path):return digest(Path(path).read_bytes())


def branch(row):
    return 'prompt' if row['kind'] in ('prompt_candidate','conditional_pair') else row['kind']


def resource_path(manifest_path,reference):
    path=Path(reference)
    if path.is_absolute():return path
    root=Path(manifest_path).resolve().parent
    target=(root/path).resolve()
    require(root in target.parents,'S88_MANIFEST_RESOURCE_PATH_INVALID')
    return target


def write(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('x',encoding='utf8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)


@contextmanager
def parent_lock(path):
    lock=Path(path).with_suffix(Path(path).suffix+'.s88.lock')
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:yield
    finally:os.close(fd);lock.unlink()


@dataclass(frozen=True)
class PreparedCase:
    row: dict
    body: dict
    messages: tuple
    options: ChatOptions
    stream: bool
    capability: str
    input_risk_upper: int


def prepare_case(manifest_path,case_id,model):
    """Pure file/render check. Never constructs a transport or opens a ledger."""
    manifest=read(manifest_path)
    rows=[row for row in manifest['fixed_requests'] if row['id']==case_id]
    require(len(rows)==1,'S88_CASE_NOT_FROZEN')
    row=rows[0];body=read(resource_path(manifest_path,row['body_file']))
    require(digest(body)==row['body_sha256'],'S88_FROZEN_BODY_CHANGED')
    scope=row.get('scope')
    require(isinstance(scope,dict) and set(scope)=={'owner_id','knowledge_base_ids','document_ids'}
            and isinstance(scope['owner_id'],str) and bool(scope['owner_id'])
            and isinstance(scope['knowledge_base_ids'],list) and bool(scope['knowledge_base_ids'])
            and isinstance(scope['document_ids'],list)
            and all(isinstance(v,str) and v for v in scope['knowledge_base_ids']+scope['document_ids']),
            'S88_FROZEN_SCOPE_REQUIRED')
    require(row.get('new_request_id_required') is True,'S88_NEW_REQUEST_ID_REQUIRED')
    config=model.config
    require(isinstance(row.get('model_id'),str) and row['model_id']
            and (config.provider,config.model,config.model_id)==(row['provider'],row['model'],row['model_id']) and body.get('model')==config.model,
            'S88_CURRENT_MODEL_MISMATCH')
    require(type(body.get('max_tokens')) is int and body['max_tokens']>0
            and body['max_tokens']==row['max_output_tokens'],'S88_FROZEN_CAP_MISMATCH')
    require(type(body.get('temperature')) in (int,float),'S88_FROZEN_TEMPERATURE_REQUIRED')
    stream=body.get('stream',False)
    require(type(stream) is bool,'S88_FROZEN_STREAM_INVALID')
    capability='vision' if row['kind']=='vision' else 'chat'
    require(row.get('capability')==capability,'S88_FROZEN_CAPABILITY_MISMATCH')
    require(capability=='vision' or stream is True,'S88_FROZEN_CHAT_STREAM_REQUIRED')
    allowed={'model','messages','temperature','max_tokens','stream','stream_options','enable_thinking','thinking'}
    require(not set(body)-allowed and isinstance(body.get('messages'),list) and body['messages'],
            'S88_FROZEN_WIRE_UNSUPPORTED')
    messages=[]
    for item in body['messages']:
        require(isinstance(item,dict) and set(item)=={'role','content'}
                and item['role'] in ('system','user','assistant'),'S88_FROZEN_MESSAGES_INVALID')
        content=item['content']
        require(isinstance(content,str) or capability=='vision' and isinstance(content,list),'S88_FROZEN_MESSAGES_INVALID')
        messages.append(Message(item['role'],content) if isinstance(content,str)
                        else Message(item['role'],multi_content=tuple(content)))
    if config.provider=='siliconflow':
        require(row['kind']=='prompt_candidate' and body.get('enable_thinking') is False,
                'S88_LEGACY_CHEAP_EXPLICIT_OFF_REQUIRED')
        product_mode=''  # Explicitly declared fixed legacy experiment, never product-mode acceptance.
        thinking=False
    else:
        require(config.provider=='deepseek' and body.get('thinking') in ({'type':'enabled'},{'type':'disabled'}),
                'S88_EXPLICIT_THINKING_REQUIRED')
        thinking=body['thinking']['type']=='enabled'
        product_mode='smart' if thinking else 'quick'
        if row['kind']=='mode_control':require(row.get('mode')==product_mode,'S88_FROZEN_MODE_MISMATCH')
        if capability=='vision':
            require(product_mode=='quick' and body['max_tokens']==5000 and not stream,'S88_VISION_QUICK_5000_REQUIRED')
        else:require(row['kind'] in ('conditional_pair','mode_control'),'S88_CASE_KIND_INVALID')
    options=ChatOptions(temperature=body['temperature'],send_temperature=True,
        max_completion_tokens=body['max_tokens'],thinking=thinking,reasoning_mode=product_mode)
    rendered=model.build_request_body(tuple(messages),options,stream=stream)
    require(rendered==body and digest(rendered)==row['body_sha256'],'S88_CURRENT_RENDER_DIFFERS_FROM_FROZEN')
    contract=resolve_output_contract(config,rendered,capability=capability)
    require(contract.output_upper==row['max_output_tokens'],'S88_OUTPUT_CONTRACT_MISMATCH')
    wire=canonical(rendered)
    risk=deepseek_inline_image_context_input(config,rendered,wire,'vision') if capability=='vision' else len(wire)
    require(risk==row['input_risk_upper'] and risk+contract.output_upper==row['token_risk_upper'],
            'S88_FROZEN_INPUT_RISK_MISMATCH')
    if capability=='vision':
        import base64
        images=[p['image_url']['url'] for m in rendered['messages'] if isinstance(m['content'],list)
                for p in m['content'] if p.get('type')=='image_url']
        require(len(images)==1 and digest(base64.b64decode(images[0].split(';base64,',1)[1],validate=True))==row.get('asset_sha256')
                and isinstance(row.get('source_version'),str) and row['source_version']
                and row.get('asset_path') and row.get('asset_authority_proof_sha256'),'S88_FROZEN_ASSET_MISMATCH')
        _vision_request_facts(config,rendered,CallContext('offline-check',TrustedScope(scope['owner_id'],tuple(scope['knowledge_base_ids']),tuple(scope['document_ids']))),
                              contract.output_upper,estimate_input=False)
    return PreparedCase(row,body,tuple(messages),options,stream,capability,risk)


def check_manifest(manifest_path,models=None):
    """Default catalogue rendering is offline; real execution rechecks Registry identities."""
    manifest=read(manifest_path);rows=manifest['fixed_requests']
    require(len(rows)==MAX_CALLS and len({r['id'] for r in rows})==MAX_CALLS,'S88_FIXED_23_REQUIRED')
    require(digest(manifest['profile'])==manifest['profile_sha256'],'S88_PROFILE_CHANGED')
    checked=[]
    for row in rows:
        if models is not None:model=models[row['model_id']]
        else:
            from backend.weknora_port.model_registry import SourceCatalogue
            from backend.weknora_port.storage.configuration import ConfiguredRole
            defaults=provider_config(row['provider'],row['model'])
            role=ConfiguredRole('cheap' if row['provider']=='siliconflow' else 'expensive',row['provider'],row['model'],defaults.base_url,source='remote')
            model=ProviderModel(SourceCatalogue().resolve(role,row['model_id']),None)
        case=prepare_case(manifest_path,row['id'],model)
        checked.append({'id':row['id'],'body_sha256':row['body_sha256'],'capability':case.capability,
            'product_mode':case.options.reasoning_mode or 'LEGACY_FIXED_INPUT_NO_PRODUCT_CLAIM',
            'input_risk_upper':case.input_risk_upper,'output_cap':case.options.completion_budget()})
    return {'status':'PASS_OFFLINE_EXACT_RENDER','provider_calls':0,'count':len(checked),'rows':checked,
            'manifest_sha256':file_sha(manifest_path),'profile_sha256':manifest['profile_sha256'],
            'source_revision':manifest['profile']['source_revision'],'image_id':manifest['profile']['image_id']}


def validate_authority(manifest_path,grant,parent,case_id):
    manifest=read(manifest_path);rows=manifest['fixed_requests'];ids=[r['id'] for r in rows]
    require(len(ids)==MAX_CALLS and len(set(ids))==MAX_CALLS and case_id in ids,'S88_FIXED_23_REQUIRED')
    require(grant.get('owner_approved') is True and grant.get('scope')==SCOPE and grant.get('status')=='OPEN'
            and grant.get('accounting_mode')==REPORTED_USAGE_ONLY,'S88_FRESH_OWNER_GRANT_REQUIRED')
    require(grant.get('manifest_sha256')==file_sha(manifest_path) and grant.get('profile_sha256')==manifest['profile_sha256']==digest(manifest['profile'])
            and grant.get('source_revision')==manifest['profile']['source_revision'] and grant.get('image_id')==manifest['profile']['image_id'],
            'S88_MANIFEST_SOURCE_IMAGE_CHANGED')
    require(type(grant.get('expires_at_unix')) in (int,float) and time.time()<grant['expires_at_unix'], 'S88_GRANT_EXPIRED')
    require(grant.get('allowed_case_ids')==ids and grant.get('max_calls')==MAX_CALLS and grant.get('max_tokens')==MAX_TOKENS
            and re.fullmatch(r's88-[0-9a-f]{32}',grant.get('request_prefix','')) is not None,'S88_FIXED_AUTHORITY_LIMITS_REQUIRED')
    require(parent.get('schema')==PARENT_SCHEMA and parent.get('status')=='OPEN'
            and parent.get('manifest_sha256')==grant['manifest_sha256'] and parent.get('profile_sha256')==grant['profile_sha256']
            and parent.get('max_calls')==MAX_CALLS and parent.get('max_tokens')==MAX_TOKENS,'S88_PARENT_LEDGER_MISMATCH')
    claims=parent.get('claims')
    require(isinstance(claims,dict) and set(claims)<=set(ids) and len(claims)<MAX_CALLS and case_id not in claims
            and all(isinstance(r,dict) for r in claims.values())
            and not any(r.get('status') in ('INFLIGHT','UNKNOWN') for r in claims.values()),'S88_REUSE_OR_CONCURRENT_SEND_DENIED')
    require(all(r.get('status') in ('COMPLETE','INCOMPLETE') and _reported_usage_valid(r.get('usage'))
                for r in claims.values()),'S88_PARENT_UNSETTLED_USAGE_DENIED')
    known=sum(r['usage']['total_tokens'] for r in claims.values() if isinstance(r.get('usage'),dict) and type(r['usage'].get('total_tokens')) is int)
    require(type(parent.get('reported_total_tokens')) is int and parent['reported_total_tokens']==known and known<MAX_TOKENS,'S88_PARENT_ACTUAL_STOP')
    for proof in manifest['fresh_source_proofs']+manifest.get('private_preflight_artifacts',[]):
        require(file_sha(resource_path(manifest_path,proof['name']))==proof['sha256'],'S88_FROZEN_SOURCE_PROOF_CHANGED')
    row=next(r for r in rows if r['id']==case_id)
    stopped=parent.get('stopped_branches',{})
    require(isinstance(stopped,dict) and branch(row) not in stopped
            and not any(claim.get('status')=='INCOMPLETE' and branch(next(r for r in rows if r['id']==identifier))==branch(row)
                        for identifier,claim in claims.items()),'S88_RELATED_BRANCH_STOPPED')
    if row['kind']=='conditional_pair':
        decision=parent.get('conditional_pair_decisions',{}).get(case_id,{})
        cheap_id=row.get('cheap_request_id')
        cheap=next((r for r in rows if r['id']==cheap_id),{})
        completed=claims.get(cheap_id,{})
        require(decision.get('decision') in ('WRONG','PARTIAL') and decision.get('cheap_case_id')==cheap_id
                and decision.get('cheap_body_sha256')==cheap.get('body_sha256')==completed.get('body_sha256')
                and completed.get('status')=='COMPLETE','S88_CONDITIONAL_PAIR_DECISION_REQUIRED')
        cheap_body=read(resource_path(manifest_path,cheap['body_file']))
        paired_body=read(resource_path(manifest_path,row['body_file']))
        require(cheap['kind']=='prompt_candidate' and cheap_body.get('enable_thinking') is False
                and paired_body.get('thinking')=={'type':'disabled'}
                and all(cheap_body.get(k)==paired_body.get(k) for k in ('messages','temperature','max_tokens','stream','stream_options')),
                'S88_CONDITIONAL_PAIR_INPUT_CHANGED')
    return manifest,row


async def execute_case(manifest_path,case_id,*,app,owner_grant,parent_ledger,output,refresh_preflight):
    """A trusted background harness invokes one case; no scheduler or retry here."""
    require(callable(refresh_preflight),'S88_FRESH_PREFLIGHT_CALLBACK_REQUIRED')
    grant=read(owner_grant)
    manifest,row=validate_authority(manifest_path,grant,read(parent_ledger),case_id)
    execution=getattr(app,'execution',None)
    require(isinstance(execution,LocalExecution) and execution.accounting_mode==REPORTED_USAGE_ONLY,'S88_NORMAL_LOCAL_EXECUTION_REQUIRED')
    require((app.principal.tenant_id,app.principal.subject_id)==(grant['tenant_id'],grant['subject_id'])
            and row['scope']['owner_id']==app.principal.subject_id,'S88_TRUSTED_PRINCIPAL_MISMATCH')
    capability='vision' if row['kind']=='vision' else 'chat'
    bindings=[r for r in grant['resolved_bindings'] if (r.get('provider'),r.get('model'),r.get('capability'))==(row['provider'],row['model'],capability)]
    require(len(bindings)==1 and set(bindings[0])=={'provider','model','model_id','capability'}
            and bindings[0]['model_id']==row.get('model_id') and row.get('capability')==capability,'S88_APPROVED_MODEL_BINDING_REQUIRED')
    binding=bindings[0];scope=row['scope']
    request_id=grant['request_prefix']+'/'+digest(case_id.encode())[:16]+'/'+capability
    seconds=grant.get('request_timeout_seconds')
    require(type(seconds) in (int,float) and 0<seconds<=900,'S88_FINITE_REQUEST_TIMEOUT_REQUIRED')
    context=CallContext(request_id,TrustedScope(scope['owner_id'],tuple(scope['knowledge_base_ids']),tuple(scope['document_ids'])),
        required_origin_kbs=tuple(scope['knowledge_base_ids']),deadline=asyncio.get_running_loop().time()+seconds,deadline_kind='run')
    with execution._ledger() as child:
        require(child['status']=='OPEN' and not child['entries'] and child['max_calls']==1
                and type(child['max_tokens']) is int and 0<child['max_tokens']<=100000
                and child['capture_evidence'] is True and child['tenant_id']==grant['tenant_id'] and child['subject_id']==grant['subject_id']
                and set(child['allowed_kbs'])==set(scope['knowledge_base_ids']) and child['allowed_models']==[binding]
                and child['allowed_request_prefixes']==[request_id],'S88_FRESH_ONE_CALL_CHILD_REQUIRED')
    registry=app.registry_for_context();claimed=False;events=[];response=None;error=None
    output=Path(output);state={'case_id':case_id,'request_id':request_id,'body_sha256':row['body_sha256'],'status':'PREFLIGHT','usage':None}
    try:
        model=await registry.get(binding['model_id'],context)
        require(isinstance(model,ProviderModel) and {k:getattr(model.config,k) for k in ('provider','model','model_id')}=={k:binding[k] for k in ('provider','model','model_id')},'S88_CURRENT_REGISTRY_BINDING_CHANGED')
        require(getattr(model.transport.gate,'_local_execution',None) is execution
                and getattr(model.transport,'_local_execution_observed',False),'S88_NORMAL_GATE_CAPTURE_REQUIRED')
        require(bool(grant.get('synthetic_fixture_only',False))==bool(getattr(model.transport,'_mock_transport',False))
                and (getattr(model.transport,'_mock_transport',False) or getattr(model.transport,'live_ready',False)),'S88_TRANSPORT_MODE_MISMATCH')
        prepared=prepare_case(manifest_path,case_id,model)
        checked_at=time.time();receipt=await refresh_preflight(app,row,manifest)
        require(isinstance(receipt,dict) and receipt.get('transaction_read_only') is True
                and type(receipt.get('checked_at_unix')) in (int,float) and checked_at<=receipt['checked_at_unix']<=time.time()
                and receipt.get('scope')==scope and receipt.get('body_sha256')==row['body_sha256']
                and receipt.get('source_proof_sha256s')==[p['sha256'] for p in manifest['fresh_source_proofs']]
                and all(receipt.get(k) is True for k in ('source_identity_verified','current_versions_verified','permissions_verified','cloud_consent_verified'))
                and receipt.get('model_binding')=={**binding,'status':'active','deleted':False,'credential_present':True},'S88_FRESH_PREFLIGHT_PROOF_REQUIRED')
        prepared=prepare_case(manifest_path,case_id,model)  # Callback may refresh resources, never the approved body.
        with parent_lock(parent_ledger):
            parent=read(parent_ledger);validate_authority(manifest_path,read(owner_grant),parent,case_id)
            require(not output.exists(),'S88_OUTPUT_ALREADY_EXISTS')
            output.mkdir(parents=True,exist_ok=False)
            parent['claims'][case_id]={**state,'status':'INFLIGHT','binding':binding,'scope':scope}
            write(parent_ledger,parent);claimed=True
        state.update(status='INFLIGHT',product_mode=prepared.options.reasoning_mode or 'LEGACY_FIXED_INPUT_NO_PRODUCT_CLAIM')
        write(output/'state.json',state);write(output/'preflight.json',receipt)
        if prepared.capability=='vision':
            response=await model.vision(prepared.messages,prepared.options,context)
            write(output/'response.json',asdict(response))
            answer,thinking,finish,reported=response.content,response.reasoning_content,response.finish_reason,response.model
        else:
            async for event in model.chat_stream(prepared.messages,prepared.options,context):
                events.append(asdict(event));write(output/'events.json',events)
            answer=''.join(e['content'] for e in events if e['response_type']=='answer')
            thinking=''.join(e['content'] for e in events if e['response_type']=='thinking')
            finals=[e for e in events if e.get('is_final') and e['response_type']=='answer']
            finish=finals[-1]['finish_reason'] if finals else ''
            reported=(finals[-1].get('data') or {}).get('model_reported') if finals else None
            failures=[e for e in events if e['response_type']=='error']
            if failures:
                details=failures[-1].get('data') or {}
                state['provider_error_event']=failures[-1]
                raise ProviderError(details.get('error_code') or 'S88_PROVIDER_STREAM_ERROR',status_code=details.get('status_code'))
        state.update(answer=answer,thinking=thinking,finish_reason=finish,model_reported=reported)
        require(reported==model.config.model,'S88_RESPONSE_MODEL_IDENTITY_MISMATCH')
        state['status']='COMPLETE' if finish in ('stop','end_turn','stop_sequence','completed') and answer.strip() else 'INCOMPLETE'
        state['stop_related_branch']=state['status']!='COMPLETE'
    except BaseException as failure:
        error=failure
        state.update(status='FAILED',error_code=failure.code if isinstance(failure,ProviderError) else str(failure) if isinstance(failure,ValueError) else type(failure).__name__)
        state.setdefault('answer',''.join(e['content'] for e in events if e['response_type']=='answer'))
        state.setdefault('thinking',''.join(e['content'] for e in events if e['response_type']=='thinking'))
    finally:
        if claimed:
            with execution._ledger() as child:
                entries=json.loads(json.dumps(child['entries']));child['status']='CLOSED'
            settled=len(entries)==1 and entries[0]['status']=='SETTLED' and entries[0].get('outcome')=='RESPONSE_RECEIVED'
            usage=entries[0].get('usage') if len(entries)==1 else None
            valid=settled and _reported_usage_valid(usage)
            state.update(usage=usage,child_entries=entries,raw_evidence_directory=str(execution.evidence))
            if not valid:state['status']='UNKNOWN' if entries else 'FAILED_NO_SEND'
            if entries and (len(entries)!=1 or entries[0].get('wire_sha256')!=row['body_sha256']
                            or entries[0].get('wire_bytes')!=len(canonical(prepared.body))
                            or any(entries[0].get(k)!=v for k,v in binding.items())
                            or entries[0].get('request_id')!=request_id):
                error=ValueError('S88_CAPTURED_REQUEST_IDENTITY_MISMATCH')
                state.update(status='FAILED',error_code=str(error))
            with parent_lock(parent_ledger):
                parent=read(parent_ledger);parent['claims'][case_id].update(state)
                if state['status']=='INCOMPLETE':
                    parent.setdefault('stopped_branches',{})[branch(row)]={'case_id':case_id,'finish_reason':state.get('finish_reason')}
                parent['reported_total_tokens']=sum(r['usage']['total_tokens'] for r in parent['claims'].values() if isinstance(r.get('usage'),dict) and type(r['usage'].get('total_tokens')) is int)
                if not valid or error is not None or parent['reported_total_tokens']>=MAX_TOKENS:parent['status']='STOP'
                elif len(parent['claims'])==MAX_CALLS:parent['status']='CLOSED'
                write(parent_ledger,parent)
            write(output/'state.json',state)
        await registry.close()
    if error is not None:raise error
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['check']);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args();report=check_manifest(args.manifest)
    if args.report:
        require(not args.report.exists(),'S88_REPORT_ALREADY_EXISTS');write(args.report,report)
    print(json.dumps({'status':report['status'],'count':report['count'],'provider_calls':0}))


if __name__=='__main__':main()
