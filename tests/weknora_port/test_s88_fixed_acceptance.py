"""Real source renderer/transport/LocalExecution, synthetic grants+HTTP only."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from scripts import s8_8_fixed_acceptance as adapter
from backend.weknora_port.models import ProviderModel,provider_config
from backend.weknora_port.models.types import ChatOptions,Message,ProviderError
from backend.weknora_port.models.transport import AsyncHTTPTransport,endpoint_urls
from backend.weknora_port.execution import LocalExecution


def fixture(tmp_path):
    root=tmp_path/'package';root.mkdir()
    profile={'source_revision':'a'*40,'image_id':'sha256:'+('b'*64)}
    models={provider:provider_config(provider,model,model_id=identifier) for provider,model,identifier in
        [('siliconflow','Qwen/Qwen3.5-4B','cheap-id'),('deepseek','deepseek-flash','expensive-id')]}
    output=io.BytesIO();Image.new('RGB',(1,1)).save(output,format='PNG')
    data=output.getvalue();image='data:image/png;base64,'+base64.b64encode(data).decode()
    rows=[]
    def add(identifier,kind,provider,*,mode='quick',cheap=None):
        config=models[provider];thinking=mode=='smart'
        messages=(Message('user','synthetic fixed source'),) if kind!='vision' else (Message('user','synthetic caption',images=(image,)),)
        options=ChatOptions(temperature=.7 if kind!='vision' else .1,max_completion_tokens=5000 if kind=='vision' else 2048,
            thinking=thinking,reasoning_mode='' if provider=='siliconflow' else mode)
        body=ProviderModel(config,None).build_request_body(messages,options,stream=kind!='vision')
        path=root/(identifier+'.json');adapter.write(path,body)
        risk=adapter.deepseek_inline_image_context_input(config,body,adapter.canonical(body),'vision') if kind=='vision' else len(adapter.canonical(body))
        row={'id':identifier,'kind':kind,'provider':provider,'model':config.model,'model_id':config.model_id,
            'capability':'vision' if kind=='vision' else 'chat','body_file':path.name,'body_sha256':adapter.digest(body),
            'max_output_tokens':options.completion_budget(),'input_risk_upper':risk,'token_risk_upper':risk+options.completion_budget(),
            'new_request_id_required':True,'scope':{'owner_id':'owner','knowledge_base_ids':['kb'],'document_ids':[]}}
        if kind=='mode_control':row['mode']=mode
        if kind=='conditional_pair':row['cheap_request_id']=cheap
        if kind=='vision':row.update(asset_sha256=adapter.digest(data),asset_path='asset.png',source_version='v1',asset_authority_proof_sha256='proof')
        rows.append(row)
    for n in range(6):
        add('prompt-'+str(n),'prompt_candidate','siliconflow')
        add('paired-'+str(n),'conditional_pair','deepseek',cheap='prompt-'+str(n))
    for mode in ('quick','smart'):add('mode-'+mode,'mode_control','deepseek',mode=mode)
    for n in range(9):add('image-'+str(n),'vision','deepseek')
    proof=root/'proof.json';adapter.write(proof,{'synthetic_fixture_only':True})
    manifest={'profile':profile,'profile_sha256':adapter.digest(profile),'fixed_requests':rows,
              'fresh_source_proofs':[{'name':proof.name,'sha256':adapter.file_sha(proof)}]}
    manifest_path=root/'manifest.json';adapter.write(manifest_path,manifest)
    grant={'owner_approved':True,'status':'OPEN','scope':adapter.SCOPE,'accounting_mode':'reported_usage_only_v1',
           'manifest_sha256':adapter.file_sha(manifest_path),'profile_sha256':manifest['profile_sha256'],**profile,
           'expires_at_unix':time.time()+3600,'allowed_case_ids':[r['id'] for r in rows],
           'max_calls':23,'max_tokens':500000,'request_prefix':'s88-'+('c'*32),'tenant_id':1,'subject_id':'owner',
           'request_timeout_seconds':30,'synthetic_fixture_only':True,
           'resolved_bindings':[{'provider':p,'model':c.model,'model_id':c.model_id,'capability':cap}
                for p,c in models.items() for cap in (('chat',) if p=='siliconflow' else ('chat','vision'))]}
    parent={'schema':adapter.PARENT_SCHEMA,'status':'OPEN','manifest_sha256':grant['manifest_sha256'],
            'profile_sha256':grant['profile_sha256'],'max_calls':23,'max_tokens':500000,
            'claims':{},'reported_total_tokens':0,'conditional_pair_decisions':{}}
    grant_path=root/'grant.json';parent_path=root/'parent.json'
    adapter.write(grant_path,grant);adapter.write(parent_path,parent)
    return manifest_path,models,grant_path,parent_path


async def fresh(app,row,manifest):
    return {'transaction_read_only':True,'checked_at_unix':time.time(),'scope':row['scope'],
        'body_sha256':row['body_sha256'],'source_proof_sha256s':[p['sha256'] for p in manifest['fresh_source_proofs']],
        'source_identity_verified':True,'current_versions_verified':True,'permissions_verified':True,'cloud_consent_verified':True,
        'model_binding':{'provider':row['provider'],'model':row['model'],'model_id':row['model_id'],
            'capability':row['capability'],'status':'active','deleted':False,'credential_present':True}}


def counts(total=30):
    return {'status':'REPORTED','prompt_tokens':10,'completion_tokens':total-10,'total_tokens':total}


def application(tmp_path,manifest_path,models,grant_path,case_id,*,usage=True,finish='stop',child_calls=1,reported_model=None):
    row=next(r for r in adapter.read(manifest_path)['fixed_requests'] if r['id']==case_id)
    config=models[row['provider']];grant=adapter.read(grant_path)
    binding=next(r for r in grant['resolved_bindings'] if r['model_id']==config.model_id and r['capability']==row['capability'])
    child_path=tmp_path/'child.json'
    adapter.write(child_path,{'status':'OPEN','tenant_id':1,'subject_id':'owner','allowed_kbs':['kb'],
        'allowed_models':[binding],'allowed_request_prefixes':[grant['request_prefix']+'/'+adapter.digest(case_id.encode())[:16]+'/'+row['capability']],
        'max_calls':child_calls,'max_tokens':100000,'capture_evidence':True,'accounting_mode':'reported_usage_only_v1','entries':[]})
    execution=LocalExecution(child_path)
    class Gate:
        application=SimpleNamespace(principal=SimpleNamespace(tenant_id=1,subject_id='owner'))
        calls=[]
        async def authorize(self,call):self.calls.append(call)
        async def finish(self,call,**kwargs):return True
    gate=execution.install_gate(Gate());requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        count={'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}
        if row['capability']=='vision':
            body={'model':reported_model or config.model,'choices':[{'message':{'content':'synthetic caption'},'finish_reason':finish}]}
            if usage:body['usage']=count
            return httpx.Response(200,json=body)
        chunks=[{'model':reported_model or config.model,'choices':[{'delta':{'content':'synthetic answer'},'finish_reason':finish}]}]
        if usage:chunks.append({'choices':[],'usage':count})
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text=''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n')
    transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(config),gate=gate,credential=lambda _:'SYNTHETIC',http_transport=httpx.MockTransport(handler))
    model=execution.observe_model(ProviderModel(config,transport))
    class Registry:
        async def get(self,model_id,context):assert model_id==config.model_id;return model
        async def close(self):await transport.aclose()
    app=SimpleNamespace(principal=gate.application.principal,execution=execution,registry_for_context=lambda:Registry())
    return app,requests,child_path


def test_all23_current_source_renderers_prepare_without_transport(tmp_path):
    manifest,_,_,_=fixture(tmp_path)
    result=adapter.check_manifest(manifest)
    assert result['count']==23 and result['provider_calls']==0
    assert sum(r['product_mode']=='LEGACY_FIXED_INPUT_NO_PRODUCT_CLAIM' for r in result['rows'])==6
    assert sum(r['capability']=='vision' for r in result['rows'])==9


@pytest.mark.parametrize('case_id',['prompt-0','mode-quick','mode-smart','image-0'])
def test_single_case_existing_gate_and_reported_usage_child_settle(tmp_path,case_id):
    manifest,models,grant,parent=fixture(tmp_path)
    app,requests,child=application(tmp_path,manifest,models,grant,case_id)
    state=asyncio.run(adapter.execute_case(manifest,case_id,app=app,owner_grant=grant,parent_ledger=parent,
        output=tmp_path/'output',refresh_preflight=fresh))
    assert state['status']=='COMPLETE' and state['usage']['total_tokens']==30 and len(requests)==1
    assert adapter.read(parent)['reported_total_tokens']==30
    entry=adapter.read(child)['entries'][0]
    assert entry['status']=='SETTLED' and 'held_tokens' not in entry and 'reserved_tokens' not in entry
    assert adapter.read(parent)['claims'][case_id]['body_sha256']==adapter.digest(requests[0])


@pytest.mark.parametrize('usage,finish,expected',[(False,'stop','UNKNOWN'),(True,'length','INCOMPLETE')])
def test_unknown_stops_parent_and_length_preserves_usage_partial_without_retry(tmp_path,usage,finish,expected):
    manifest,models,grant,parent=fixture(tmp_path)
    app,requests,_=application(tmp_path,manifest,models,grant,'image-0',usage=usage,finish=finish)
    state=asyncio.run(adapter.execute_case(manifest,'image-0',app=app,owner_grant=grant,parent_ledger=parent,
        output=tmp_path/'output',refresh_preflight=fresh))
    assert state['status']==expected and state['answer']=='synthetic caption' and len(requests)==1
    if not usage:assert adapter.read(parent)['status']=='STOP' and state['usage'] is None
    else:
        assert state['usage']['total_tokens']==30 and state['stop_related_branch']
        with pytest.raises(ValueError,match='RELATED_BRANCH_STOPPED'):
            adapter.validate_authority(manifest,adapter.read(grant),adapter.read(parent),'image-1')


@pytest.mark.parametrize('change',['body','cap','model','scope'])
def test_frozen_drift_refuses_before_send(tmp_path,change):
    manifest,models,grant,parent=fixture(tmp_path)
    value=adapter.read(manifest);row=next(r for r in value['fixed_requests'] if r['id']=='mode-quick')
    if change=='body':
        path=adapter.resource_path(manifest,row['body_file']);body=adapter.read(path);body['messages'][0]['content']='drift';adapter.write(path,body)
    elif change=='cap':row['max_output_tokens']=2049
    elif change=='model':row['model_id']='different-model'
    else:row['scope']['owner_id']='different-owner'
    if change!='body':
        adapter.write(manifest,value);authority=adapter.read(grant);authority['manifest_sha256']=adapter.file_sha(manifest);adapter.write(grant,authority)
        ledger=adapter.read(parent);ledger['manifest_sha256']=authority['manifest_sha256'];adapter.write(parent,ledger)
    app,requests,_=application(tmp_path,manifest,models,grant,'mode-quick')
    with pytest.raises((ValueError,AssertionError)):
        asyncio.run(adapter.execute_case(manifest,'mode-quick',app=app,owner_grant=grant,parent_ledger=parent,
            output=tmp_path/'output',refresh_preflight=fresh))
    assert requests==[] and adapter.read(parent)['claims']=={}


def test_conditional_pair_requires_completed_matching_cheap_and_explicit_decision(tmp_path):
    manifest,models,grant,parent=fixture(tmp_path);authority=adapter.read(grant);ledger=adapter.read(parent)
    with pytest.raises(ValueError,match='CONDITIONAL_PAIR_DECISION'):
        adapter.validate_authority(manifest,authority,ledger,'paired-0')
    cheap=next(r for r in adapter.read(manifest)['fixed_requests'] if r['id']=='prompt-0')
    ledger['claims']['prompt-0']={'status':'COMPLETE','body_sha256':cheap['body_sha256'],'usage':counts()}
    ledger['reported_total_tokens']=30
    ledger['conditional_pair_decisions']['paired-0']={'decision':'PARTIAL','cheap_case_id':'prompt-0','cheap_body_sha256':cheap['body_sha256']}
    adapter.validate_authority(manifest,authority,ledger,'paired-0')
    ledger['conditional_pair_decisions']['paired-0']['decision']='CORRECT'
    with pytest.raises(ValueError,match='CONDITIONAL_PAIR_DECISION'):
        adapter.validate_authority(manifest,authority,ledger,'paired-0')


@pytest.mark.parametrize('change',['temperature','max_tokens','stream','thinking'])
def test_conditional_pair_rejects_changed_generation_conditions(tmp_path,change):
    manifest,_,grant,parent=fixture(tmp_path);authority=adapter.read(grant);ledger=adapter.read(parent)
    value=adapter.read(manifest);cheap=next(r for r in value['fixed_requests'] if r['id']=='prompt-0')
    paired=next(r for r in value['fixed_requests'] if r['id']=='paired-0')
    path=adapter.resource_path(manifest,paired['body_file']);body=adapter.read(path)
    body[change]={'temperature':.5,'max_tokens':2049,'stream':False,'thinking':{'type':'enabled'}}[change]
    adapter.write(path,body);paired['body_sha256']=adapter.digest(body);adapter.write(manifest,value)
    authority['manifest_sha256']=adapter.file_sha(manifest);ledger['manifest_sha256']=authority['manifest_sha256']
    ledger['claims']['prompt-0']={'status':'COMPLETE','body_sha256':cheap['body_sha256'],'usage':counts()}
    ledger['reported_total_tokens']=30
    ledger['conditional_pair_decisions']['paired-0']={'decision':'PARTIAL','cheap_case_id':'prompt-0','cheap_body_sha256':cheap['body_sha256']}
    with pytest.raises(ValueError,match='CONDITIONAL_PAIR_INPUT_CHANGED'):
        adapter.validate_authority(manifest,authority,ledger,'paired-0')


@pytest.mark.parametrize('change',['parent_limit','child_limit','claimed','unknown','callback'])
def test_parent_child_authority_and_fresh_proof_guards_are_zero_send(tmp_path,change):
    manifest,models,grant,parent=fixture(tmp_path)
    ledger=adapter.read(parent)
    if change=='parent_limit':ledger['max_tokens']=500001
    if change=='claimed':ledger['claims']['mode-quick']={'status':'COMPLETE','usage':{'total_tokens':0}}
    if change=='unknown':ledger['claims']['prompt-0']={'status':'UNKNOWN','usage':None}
    adapter.write(parent,ledger)
    app,requests,_=application(tmp_path,manifest,models,grant,'mode-quick',child_calls=2 if change=='child_limit' else 1)
    async def bad(*args):return True
    with pytest.raises(ValueError):
        asyncio.run(adapter.execute_case(manifest,'mode-quick',app=app,owner_grant=grant,parent_ledger=parent,
            output=tmp_path/'output',refresh_preflight=bad if change=='callback' else fresh))
    assert requests==[]


def test_parent_retains_actual_overshoot_and_stops_future_calls(tmp_path):
    manifest,models,grant,parent=fixture(tmp_path)
    ledger=adapter.read(parent);ledger['claims']['prompt-1']={'status':'COMPLETE','usage':counts(499999)}
    ledger['reported_total_tokens']=499999;adapter.write(parent,ledger)
    app,requests,_=application(tmp_path,manifest,models,grant,'image-0')
    state=asyncio.run(adapter.execute_case(manifest,'image-0',app=app,owner_grant=grant,parent_ledger=parent,
        output=tmp_path/'output',refresh_preflight=fresh))
    assert state['status']=='COMPLETE' and len(requests)==1
    ledger=adapter.read(parent)
    assert ledger['status']=='STOP' and ledger['reported_total_tokens']==500029
    with pytest.raises(ValueError):adapter.validate_authority(manifest,adapter.read(grant),ledger,'image-1')


@pytest.mark.parametrize('case_id,next_case',[('prompt-0','paired-0'),('mode-quick','mode-smart')])
def test_incomplete_persists_and_enforces_related_branch_stop(tmp_path,case_id,next_case):
    manifest,models,grant,parent=fixture(tmp_path)
    app,requests,_=application(tmp_path,manifest,models,grant,case_id,finish='length')
    state=asyncio.run(adapter.execute_case(manifest,case_id,app=app,owner_grant=grant,parent_ledger=parent,
        output=tmp_path/'output',refresh_preflight=fresh))
    assert state['status']=='INCOMPLETE' and state['usage']['total_tokens']==30 and len(requests)==1
    ledger=adapter.read(parent)
    stopped='prompt' if case_id.startswith('prompt') else 'mode_control'
    assert ledger['stopped_branches'][stopped]['case_id']==case_id
    with pytest.raises(ValueError,match='RELATED_BRANCH_STOPPED'):
        adapter.validate_authority(manifest,adapter.read(grant),ledger,next_case)
    ledger.pop('stopped_branches')
    with pytest.raises(ValueError,match='RELATED_BRANCH_STOPPED'):
        adapter.validate_authority(manifest,adapter.read(grant),ledger,next_case)


def test_reported_model_mismatch_preserves_partial_usage_and_globally_stops(tmp_path):
    manifest,models,grant,parent=fixture(tmp_path)
    app,requests,_=application(tmp_path,manifest,models,grant,'image-0',reported_model='wrong-model')
    with pytest.raises(ProviderError,match='PROVIDER_MODEL_IDENTITY_MISMATCH'):
        asyncio.run(adapter.execute_case(manifest,'image-0',app=app,owner_grant=grant,parent_ledger=parent,
            output=tmp_path/'output',refresh_preflight=fresh))
    state=adapter.read(tmp_path/'output'/'state.json');ledger=adapter.read(parent)
    assert len(requests)==1 and state['usage']['total_tokens']==30 and ledger['status']=='STOP'
    assert state['error_code']=='PROVIDER_MODEL_IDENTITY_MISMATCH'
    raw=list(Path(state['raw_evidence_directory']).glob('*.response.body'))
    assert len(raw)==1 and adapter.read(raw[0])['choices'][0]['message']['content']=='synthetic caption'
    assert adapter.read(raw[0])['model']=='wrong-model'


def test_chat_nonstream_body_and_invalid_parent_usage_are_zero_send(tmp_path):
    manifest,models,grant,parent=fixture(tmp_path)
    value=adapter.read(manifest);row=next(r for r in value['fixed_requests'] if r['id']=='mode-quick')
    path=adapter.resource_path(manifest,row['body_file']);body=adapter.read(path);body['stream']=False;body.pop('stream_options',None)
    adapter.write(path,body);row['body_sha256']=adapter.digest(body)
    adapter.write(manifest,value)
    with pytest.raises(ValueError,match='CHAT_STREAM_REQUIRED'):
        adapter.prepare_case(manifest,'mode-quick',ProviderModel(models['deepseek'],None))
    authority=adapter.read(grant);authority['manifest_sha256']=adapter.file_sha(manifest)
    ledger=adapter.read(parent);ledger['manifest_sha256']=authority['manifest_sha256']
    ledger['claims']['prompt-0']={'status':'COMPLETE','usage':counts(-1)};ledger['reported_total_tokens']=-1
    with pytest.raises(ValueError,match='PARENT_UNSETTLED_USAGE_DENIED'):
        adapter.validate_authority(manifest,authority,ledger,'image-0')
