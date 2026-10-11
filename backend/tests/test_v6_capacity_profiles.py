"""SIMULATED profile/DS renderer/senders; SF uses real pinned local tokenizer.

All ledger/profile/receipt writes are temporary, never real authority or keys.
"""
from dataclasses import asdict,replace
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os
import pytest
import backend.app.adapters.models.v6_text_capacity as ds
import backend.app.adapters.models.siliconflow_xing_capacity as sf
from backend.tests.test_v6_xing_generation_binding import counter,no_live_sender,setup as xsetup,invoke as xinvoke
from backend.tests.test_v6_generation_binding import setup_binding,invoke as dinvoke
from backend.app.application.validation_usd_budget import V6OwnerActivityGate
from backend.app.application.rule_router_v1 import GenerationRole,V6GenerationBinding
from backend.app.adapters.models.deepseek import SimulatedDeepSeekTransport
from backend.app.ports.providers import ProviderRequestNotSent

DEADLINE='2026-10-10T07:44:02Z'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest(tmp_path,provider,grant):
    if provider=='deepseek':
        module=ds;model=ds.DEEPSEEK_MODEL;identity=ds.DEEPSEEK_RENDERER_IDENTITY
        resources=dict(wheel='D:/RAG-v6-text-capacity-r1/deepseek-recipe-0.1.1/deepseek_recipe-0.1.1-cp310-abi3-manylinux_2_28_x86_64.whl',
            tokenizer='D:/RAG-v6-text-capacity-r1/deepseek-recipe-8cadfede/tokenizer-v41.json')
        hashes=dict(wheel=ds.DEEPSEEK_WHEEL_SHA256,tokenizer=ds.DEEPSEEK_TOKENIZER_SHA256)
        runtime={'deepseek-recipe':'0.1.1'};adapter=Path(ds.__file__).with_name('deepseek.py')
        data=dict(endpoint='https://api.deepseek.com/chat/completions',protocol_id=ds.V6_REQUEST_PROTOCOL,
            scope_id=grant.scope,grant_sha256=hashlib.sha256(ds._canonical(asdict(grant))).hexdigest(),
            context_window=ds.DEEPSEEK_CONTEXT_WINDOW,output_cap=512,bound_basis='OWNER_SCOPED_OFFICIAL_RECIPE_CONDITIONAL')
    else:
        module=sf;model=sf.SILICONFLOW_XING_MODEL;identity=sf.RENDERER_IDENTITY
        resources={n:str((Path(os.environ['XING_TEST_ASSETS'])/n).resolve()) for n in sf.RESOURCE_SHA256}
        hashes=sf.RESOURCE_SHA256;runtime=sf.RUNTIME_VERSIONS;adapter=Path(sf.__file__).with_name('cloud.py')
        data=dict(endpoint='https://api.siliconflow.cn/v1/chat/completions',protocol_id=sf.PROTOCOL_ID,
            scope_id=grant.scope_id,grant_sha256=sf.xing_grant_sha256(grant),context_window=sf.OFFICIAL_CONTEXT_WINDOW,
            input_cap=4096,output_cap=512,content_byte_cap=8192,message_cap=16,wire_byte_cap=65536,
            bound_basis='OWNER_SCOPED_LOCAL_TWO_MODE_HOSTED_CONDITIONAL')
    evidence=dict(schema_version=1,provider=provider,model=model,execution_kind='SIMULATED',renderer_identity=identity,
        resource_sha256=hashes,runtime_versions=runtime,counter_source_sha256=digest(module.__file__),
        adapter_source_sha256=digest(adapter),positive_cases=3,negative_cases=2,provider_calls=0,
        official_renderer=provider=='siliconflow',injected_test_double=provider=='deepseek')
    if provider=='siliconflow':evidence['local_thinking_modes']=[True,False]
    evidence_path=tmp_path/'SIMULATED-evidence.json';evidence_path.write_text(json.dumps(evidence))
    data.update(schema_version=1,provider=provider,model=model,renderer_identity=identity,
        deadline_utc=grant.deadline_utc,execution_kind=grant.execution_kind,resources=resources,
        evidence_path=str(evidence_path.resolve()),evidence_sha256=digest(evidence_path))
    path=tmp_path/'SIMULATED-profile.json';path.write_text(json.dumps(data))
    load=lambda: (ds.DeepseekCapacityProfile if provider=='deepseek' else sf.XingCapacityProfile).from_manifest(
        path,expected_sha256=digest(path),grant=grant)
    return path,data,evidence_path,evidence,load


def ds_case(tmp_path,monkeypatch):
    role,old,path,_,guard,wires=setup_binding(tmp_path)
    # Rebuild only a temporary SIMULATED grant before any activity attempt.
    raw=json.loads(path.read_text());raw.pop('v6_owner_activity');path.write_text(json.dumps(raw))
    grant=replace(old.grant,deadline_utc=DEADLINE)
    gate=V6OwnerActivityGate(path,grant=grant,clock=old.clock);gate.initialize_grant()
    profile_path,data,evidence_path,evidence,load=manifest(tmp_path,'deepseek',grant)
    renderer=ds.DeepseekV41RecipeRenderer(data['resources']['tokenizer'])
    monkeypatch.setattr(ds.DeepseekV41RecipeRenderer,'__call__',lambda self,body:'SIMULATED official framing:'+body['messages'][0]['content'])
    count=ds.DeepseekV41TextCapacityCounter(renderer,production_profile=load())
    role.gateway.attempt_gate=gate
    role=replace(role,v6_binding=V6GenerationBinding(grant,gate,count))
    return role,gate,path,guard,wires,profile_path,data,evidence_path


def sf_case(tmp_path,counter):
    role,transport,ledger,wires,factory=xsetup(counter,grant_changes={'deadline_utc':DEADLINE})
    path,data,evidence_path,evidence,load=manifest(tmp_path,'siliconflow',transport.grant)
    count=sf.SiliconFlowXingCapacityCounter.from_directory(os.environ['XING_TEST_ASSETS'],production_profile=load())
    binding=replace(role.xing_binding,capacity_counter=count)
    role=replace(role,gateway=factory.build('chat_cheap',xing_binding=binding),xing_binding=binding)
    return role,transport,ledger,wires,path,data,evidence_path


@pytest.mark.parametrize('provider',['deepseek','siliconflow'])
@pytest.mark.parametrize('fault',['resource','hash','scope','expiry','unproven','source','window','evidence_hash','bare_bool'])
def test_bad_profiles_never_construct_ready(tmp_path,monkeypatch,counter,provider,fault):
    if provider=='deepseek':
        role,gate,_,_,_,_,_,_=ds_case(tmp_path,monkeypatch);grant=gate.grant
    else:grant=sf_case(tmp_path,counter)[1].grant
    path,data,evidence_path,evidence,load=manifest(tmp_path,provider,grant)
    if fault=='resource':data['resources'][next(iter(data['resources']))]=str((tmp_path/'missing').resolve())
    if fault=='scope':data['scope_id']='other'
    if fault=='expiry':data['deadline_utc']='2000-01-01T00:00:00Z'
    if fault=='window':data['context_window']=123
    if fault=='evidence_hash':data['evidence_sha256']='0'*64
    if fault in ('unproven','source'):
        evidence['positive_cases' if fault=='unproven' else 'counter_source_sha256']=0 if fault=='unproven' else '0'*64
        evidence_path.write_text(json.dumps(evidence));data['evidence_sha256']=digest(evidence_path)
    path.write_text(json.dumps(data))
    cls=ds.DeepseekCapacityProfile if provider=='deepseek' else sf.XingCapacityProfile
    with pytest.raises((ds.CapacityCounterUnavailable,ProviderRequestNotSent)):
        cls.from_manifest(path,expected_sha256='0'*64 if fault=='hash' else digest(path),grant=True if fault=='bare_bool' else grant)


def test_profile_ds_normal_prepare_reserve_and_fake_sender(tmp_path,monkeypatch):
    role,gate,path,guard,wires,profile_path,data,evidence_path=ds_case(tmp_path,monkeypatch)
    assert dinvoke(role)=='SIMULATED answer' and len(wires)==len(guard.reservations)==1
    row=json.loads(path.read_text())['v6_owner_activity']['attempts'][0]
    assert row['status']=='ok' and row['validation_tokens']['reserved_output']==512
    assert role.gateway.last_receipt['capacity_profile_sha256']==digest(profile_path)
    assert role.gateway.last_receipt['hosted_render_equivalence']=='UNKNOWN'
    assert json.loads(wires[0])['model']=='deepseek-flash'


def test_profile_sf_normal_factory_guard_fake_sender_and_unknown_hosted(tmp_path,counter):
    role,transport,ledger,wires,path,data,evidence_path=sf_case(tmp_path,counter)
    assert xinvoke(role)=='SIMULATED answer [E1]' and len(wires)==len(ledger.reservations)==1
    receipt=role.gateway.receipts[-1]
    assert receipt['production_ready'] is True and receipt['capacity_profile_sha256']==digest(path)
    assert receipt['hosted_revision']=='UNKNOWN' and receipt['hosted_total_token_bound'] is None
    assert receipt['settlement']=='UNKNOWN' and json.loads(wires[0])['enable_thinking'] is False
    assert len(role.xing_binding.capacity_counter.measure('siliconflow',sf.SILICONFLOW_XING_MODEL,
        json.loads(wires[0]),scope=transport.grant.scope_id).profiles)==2


@pytest.mark.parametrize('provider',['deepseek','siliconflow'])
@pytest.mark.parametrize('fault',['profile','evidence','resource'])
def test_final_send_rechecks_drift_after_successful_measure_and_reserve(tmp_path,monkeypatch,counter,provider,fault):
    if provider=='deepseek':
        role,gate,ledger,guard,wires,path,data,evidence_path=ds_case(tmp_path,monkeypatch)
        original=gate.validate_before_send
        def before_send(attempt):
            mutate();return original(attempt)
        monkeypatch.setattr(gate,'validate_before_send',before_send)
        call=lambda:dinvoke(role)
    else:
        role,transport,ledger,wires,path,data,evidence_path=sf_case(tmp_path,counter)
        original=transport.validate_request
        def request_check(request,timeout):
            original(request,timeout);mutate()
        monkeypatch.setattr(transport,'validate_request',request_check)
        call=lambda:xinvoke(role)
    def mutate():
        if fault=='profile':path.write_text('SIMULATED altered profile')
        elif fault=='evidence':evidence_path.write_text('SIMULATED altered receipt')
        else:
            # Never edit official resource; alter only the profile's path.
            data['resources'][next(iter(data['resources']))]=str((tmp_path/'missing').resolve())
            path.write_text(json.dumps(data))
    with pytest.raises(ProviderRequestNotSent):call()
    assert not wires
    if provider=='deepseek':
        row=json.loads(ledger.read_text())['v6_owner_activity']['attempts'][0]
        assert row['status']=='not_sent' and row['validation_tokens']['state']=='released'
        assert len(guard.reservations)==1 and guard.settlements[-1][2] is False
    else:assert len(ledger.reservations)==1 and next(iter(ledger.reservations.values()))['state']=='released'


@pytest.mark.parametrize('provider',['deepseek','siliconflow'])
def test_manually_replaced_estimate_and_sim_profile_cannot_authorize_real(tmp_path,monkeypatch,counter,provider):
    if provider=='deepseek':
        role,gate,_,_,_,_,_,_=ds_case(tmp_path,monkeypatch);count=role.v6_binding.capacity_counter
        body=dict(model='deepseek-flash',messages=[dict(role='user',content='public')],thinking={'type':'disabled'},max_tokens=512,temperature=0,stream=False)
        estimate=count.measure('deepseek','deepseek-flash',body,scope=gate.grant.scope)
        with pytest.raises(ds.CapacityCounterUnavailable):count.validate_estimate(replace(estimate,reservation_bound=1),grant=gate.grant,scope=gate.grant.scope,wire_bytes=json.dumps(body).encode())
        with pytest.raises(ds.CapacityCounterUnavailable):count.production_profile.validate_request(grant=replace(gate.grant,execution_kind='REAL'),scope=gate.grant.scope)
    else:
        role,transport,_,_,_,_,_=sf_case(tmp_path,counter);count=role.xing_binding.capacity_counter
        body=dict(model=sf.SILICONFLOW_XING_MODEL,messages=[dict(role='user',content='public')],max_tokens=512,stream=False,enable_thinking=False)
        estimate=count.measure('siliconflow',sf.SILICONFLOW_XING_MODEL,body,scope=transport.grant.scope_id)
        with pytest.raises(ProviderRequestNotSent):replace(estimate,reservation_bound=1).validate_profile(transport.grant)
        with pytest.raises(ProviderRequestNotSent):estimate.validate_profile(replace(transport.grant,execution_kind='REAL'))


def test_profile_cannot_replace_existing_real_sf_free_permit(tmp_path,monkeypatch,counter):
    from backend.app.adapters.models.cloud import XingAuthorizedTransport
    role,transport,_,wires,_,_,_=sf_case(tmp_path,counter)
    real=XingAuthorizedTransport(replace(transport.grant,execution_kind='REAL'))
    # SIMULATED isolated profile-success injection exercises the independent
    # free authorization guard, without claiming REAL profile verification.
    class Estimate:
        def validate_profile(self,grant):return None
    with pytest.raises(ProviderRequestNotSent,match='SF_FREE_PERMIT_REQUIRED'):
        real.validate_capacity_profile(Estimate())
    assert not wires
