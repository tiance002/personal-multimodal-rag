"""R3 explicit no-Owner-cutoff contracts; services/claims/providers SIMULATED."""
from dataclasses import asdict,replace
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import hashlib,json
import pytest
from backend.app.application import validation_usd_budget as b
from backend.app.ports import xing_zero_reservation as sf
from backend.app.ports.session_attempts import AttemptDenied
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.tests.test_v6_token_grant import _ledger,_budget
from backend.tests.test_v6_xing_generation_binding import counter
from backend.tests.test_v6_capacity_profiles import manifest

NOW=datetime(2026,10,10,9,tzinfo=timezone.utc)
FUTURE=datetime(2099,1,1,tzinfo=timezone.utc)
REGISTRY=ModelRegistry.frozen_defaults()
SCOPE=Scope.from_ids(['SIMULATED-R3-KB'],['SIMULATED-R3-DOC'])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-ONLY')
    monkeypatch.setenv('DEEPSEEK_API_KEY','SIMULATED-ONLY')
    monkeypatch.setattr('backend.app.adapters.models.cloud._send',lambda *a,**k:pytest.fail('REAL SF forbidden'))
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',lambda *a,**k:pytest.fail('REAL DS forbidden'))


def sim_r3(**changes):
    return replace(b.OWNER_V6_TOKEN_GRANT_R3,execution_kind='SIMULATED',
        baseline_ledger_sha256='SIMULATED',grant_id='SIMULATED-R3',**changes)


def lease(now=NOW,*,owner=None):
    return b.V6ExecutionLease('SIMULATED-r3-native-lease',(now-timedelta(minutes=1)).isoformat(),
        (now+timedelta(minutes=40)).isoformat(),owner or (now+timedelta(hours=4)).isoformat())


def sf_permit(*,activity=True,deadline=None,grant_id=None,role='chat_cheap'):
    spec=REGISTRY.select(role)
    call=sf.FreeModelPlannedCall('SIMULATED-r3-request','277616#0','CHEAP_ONLY',
        'answer' if role=='chat_cheap' else 'query_embedding','SIMULATED-batch',
        'a'*64,'b'*64,2048,512 if role=='chat_cheap' else 0,1,30)
    args={} if activity is False else {'owner_activity_id':b.OWNER_V6_TOKEN_GRANT_R3.grant_id if activity is True else activity,
        'execution_lease':lease(FUTURE)}
    permit=sf.SiliconFlowFreePermit(grant_id or b.OWNER_V6_TOKEN_GRANT_R3.grant_id+':SIMULATED-'+role,
        'SIMULATED-R3-RUN',SCOPE,deadline,'1'*64,'2'*64,spec.model_id,role,
        sf.SiliconFlowZeroPriceContract(spec.model_id),(call,),call.input_token_bound+call.output_token_bound,
        '0'*64,'SIMULATED',**args)
    return replace(permit,frozen_plan_sha256=permit.fingerprint)


def test_owner_cancellation_has_one_closed_identity_and_preserves_old_constants():
    grant=b.OWNER_V6_TOKEN_GRANT_R3
    assert (grant.grant_id,grant.activity_id,grant.scope,grant.version,grant.deadline_utc)==(
        'owner-v6-public-rag-r3','RAG-V6-TOKEN-GRANT-R3','p7-v6-public-r3',3,None)
    assert 'Sentinel_77a5fceb1db081918fecb2da7202a274@2026-10-10T08:46:54Z'==b.V6_OWNER_ACTIVITY_R3_SOURCE
    assert b.OWNER_V6_TOKEN_GRANT.deadline_utc=='2026-10-10T04:25:44+00:00'
    assert b.OWNER_V6_TOKEN_GRANT_R2.deadline_utc=='2026-10-10T07:44:02+00:00'
    assert sf.FREE_OWNER_DEADLINE=='2026-10-10T07:44:02Z'
    assert grant.token_limit==1_000_000 and grant.baseline_used_tokens==65_569 and grant.output_token_cap==512
    assert set(asdict(grant))==set(asdict(b.OWNER_V6_TOKEN_GRANT_R2))


def test_r1_r2_unknown_usage_and_entire_legacy_history_are_carried_to_r3(tmp_path):
    path=_ledger(tmp_path);old=b.OwnerActivityTokenGrant.simulated(baseline_used_tokens=65569)
    gate1=b.V6OwnerActivityGate(path,grant=old,clock=lambda:datetime(2026,10,10,2,tzinfo=timezone.utc))
    gate1.initialize_grant();ticket=gate1.reserve(request_id='SIMULATED-r1-unknown',budget=_budget(gate1));gate1.finish(ticket,'unknown')
    grant2=replace(old,version=2,grant_id='SIMULATED-R2',scope='SIMULATED-r2',deadline_utc=b.OWNER_V6_TOKEN_GRANT_R2.deadline_utc)
    gate2=b.V6OwnerActivityGate(path,grant=grant2,clock=lambda:datetime(2026,10,10,5,tzinfo=timezone.utc))
    gate2.activate_revision(old)
    ticket=gate2.reserve(request_id='SIMULATED-r2-usage',budget=_budget(gate2))
    gate2.finish_with_usage(ticket,'ok',{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25})
    ticket=gate2.reserve(request_id='SIMULATED-r2-unknown',budget=_budget(gate2));gate2.finish(ticket,'unknown')
    before=gate2._read();used=gate2._used(before)
    gate3=b.V6OwnerActivityGate(path,grant=sim_r3(),clock=lambda:FUTURE,execution_lease=lease(FUTURE));gate3.activate_revision(grant2)
    after=gate3._read()
    assert gate3._used(after)==used==65569+620+25+620
    assert {k:v for k,v in after.items() if k!='v6_owner_activity'}=={k:v for k,v in before.items() if k!='v6_owner_activity'}
    assert after['v6_owner_activity']['attempts']==before['v6_owner_activity']['attempts']
    assert after['v6_owner_activity']['grant_history']==[asdict(old),asdict(grant2)]
    assert after['authorized_limit']==53 and after['calls_allowed_in_this_task'] is False
    snapshot=path.read_bytes();gate3.activate_revision(grant2);assert path.read_bytes()==snapshot
    with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):
        gate3.reserve(request_id='SIMULATED-r2-unknown',budget=_budget(gate3))
    ticket=gate3.reserve(request_id='SIMULATED-r3-not-sent',budget=_budget(gate3));gate3.finish(ticket,'not_sent')
    assert gate3._used(gate3._read())==used
    gate2.clock=lambda:NOW
    with pytest.raises(AttemptDenied,match='REQUEST_BOUND'):
        gate2.validate_request('public',30,512,model='deepseek-flash')


def test_pending_attempt_blocks_revision_and_budget_never_resets(tmp_path):
    path=_ledger(tmp_path);old=b.OwnerActivityTokenGrant.simulated(baseline_used_tokens=65569)
    g=b.V6OwnerActivityGate(path,grant=old,clock=lambda:datetime(2026,10,10,2,tzinfo=timezone.utc));g.initialize_grant()
    g.reserve(request_id='SIMULATED-pending',budget=_budget(g));before=path.read_bytes()
    new=b.V6OwnerActivityGate(path,grant=sim_r3(),clock=lambda:NOW,execution_lease=lease())
    with pytest.raises(AttemptDenied,match='PENDING_ATTEMPT'):new.activate_revision(old)
    assert path.read_bytes()==before


@pytest.mark.parametrize('version',[1,2])
def test_only_explicit_new_grant_can_omit_owner_cutoff(tmp_path,version):
    grant=replace(b.OwnerActivityTokenGrant.simulated(baseline_used_tokens=65569),version=version,deadline_utc=None)
    with pytest.raises(AttemptDenied,match='OWNER_GRANT_INVALID'):
        b.V6OwnerActivityGate(_ledger(tmp_path),grant=grant,clock=lambda:NOW)


@pytest.mark.parametrize('timeout',[0,121,float('inf'),float('nan'),True])
def test_r3_keeps_request_timeout_bounds(tmp_path,timeout):
    g=b.V6OwnerActivityGate(_ledger(tmp_path),grant=sim_r3(),clock=lambda:FUTURE,execution_lease=lease(FUTURE));g.initialize_grant()
    with pytest.raises(AttemptDenied,match='REQUEST_BOUND'):
        g.validate_request('public',timeout,512,model='deepseek-flash')


def test_r3_cap_and_real_alias_guards_remain_closed(tmp_path):
    grant=sim_r3(token_limit=65600)
    g=b.V6OwnerActivityGate(_ledger(tmp_path),grant=grant,clock=lambda:FUTURE,execution_lease=lease(FUTURE));g.initialize_grant()
    with pytest.raises(AttemptDenied,match='TOKEN_CAP'):g.reserve(request_id='SIMULATED-cap',budget=_budget(g))
    with pytest.raises(AttemptDenied,match='REAL_GRANT_PATH_OR_IDENTITY_INVALID'):
        b.V6OwnerActivityGate(tmp_path/'never-created.json',grant=replace(b.OWNER_V6_TOKEN_GRANT_R3),clock=lambda:NOW)
    assert not (tmp_path/'never-created.json').exists()


@pytest.mark.parametrize('role',['chat_cheap','embedding'])
def test_explicit_r3_sf_without_owner_cutoff_after_old_deadline(role):
    p=sf_permit(role=role);p.validate(REGISTRY.select(role));assert p.owner_deadline()==p.execution_lease.deadline
    assert p.fingerprint!=replace(p,owner_activity_id=None).fingerprint


@pytest.mark.parametrize('changes',[{'owner_activity_id':None},{'owner_activity_id':True},
    {'owner_activity_id':'old'},{'grant_id':'owner-old-r2'},
    {'deadline_utc':'2026-10-10T10:31:38Z'}])
def test_r3_none_cannot_be_selected_by_bool_old_identity_or_rewritten_deadline(changes):
    p=replace(sf_permit(),**changes);p=replace(p,frozen_plan_sha256=p.fingerprint)
    with pytest.raises(ValueError,match='SF_FREE_PERMIT_INVALID'):p.validate(REGISTRY.select('chat_cheap'))


def test_old_sf_permit_cannot_be_extended_or_silently_unbounded():
    p=sf_permit(activity=False,deadline=sf.FREE_OWNER_DEADLINE,grant_id='SIMULATED-R2')
    p.validate(REGISTRY.select('chat_cheap'))
    for deadline in [None,'2026-10-10T10:31:38Z']:
        q=replace(p,deadline_utc=deadline);q=replace(q,frozen_plan_sha256=q.fingerprint)
        with pytest.raises(ValueError,match='SF_FREE_PERMIT_INVALID'):q.validate(REGISTRY.select('chat_cheap'))
    # Historical fingerprint excludes the new optional field.
    payload=dict(grant_id=p.grant_id,run_id=p.run_id,scope=dict(kb=sorted(SCOPE.knowledge_base_ids),docs=sorted(SCOPE.document_ids)),
        deadline_utc=p.deadline_utc,source_manifest_sha256=p.source_manifest_sha256,authority_manifest_sha256=p.authority_manifest_sha256,
        model=p.model,role=p.role,price_contract=asdict(p.price_contract),calls=[asdict(c) for c in p.calls],
        token_cap=p.token_cap,execution_kind=p.execution_kind,provider=p.provider)
    assert p.fingerprint==hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class Claims:
    def __init__(self,denied=False):self.denied=denied;self.settlements=[]
    def __call__(self,**kwargs):
        return SimpleNamespace(run_id='SIMULATED-run',qid='277616#0',arm='EXPENSIVE_ONLY',
            claimed_at_utc=NOW.isoformat(),**kwargs)
    def check_before_send(self,*,request_id):
        if self.denied:raise RuntimeError('SIMULATED native lease expired')
    def settle(self,**kwargs):self.settlements.append(kwargs)


def test_none_xing_grant_requires_same_explicit_permit_and_scope():
    from backend.app.adapters.models.cloud import XingChatGrant,XingAuthorizedTransport,SimulatedXingSend
    p=sf_permit();grant=XingChatGrant(p.grant_id,'SIMULATED-scope',SCOPE,None,('answer',),'SIMULATED')
    for permit in [None,replace(p,scope=Scope.from_ids(['other']))]:
        with pytest.raises(ValueError):XingAuthorizedTransport(grant,free_model_permit=permit,
            send_claim_callback=Claims(),sender=SimulatedXingSend(lambda *a:pytest.fail('no send')))
    transport=XingAuthorizedTransport(grant,free_model_permit=p,send_claim_callback=Claims(),
        sender=SimulatedXingSend(lambda *a:pytest.fail('no send')),clock=lambda:FUTURE)
    transport.check_scope(scope=SCOPE,purpose='answer',timeout_seconds=120)
    with pytest.raises(ProviderRequestNotSent):transport.check_scope(scope=SCOPE,purpose='answer',timeout_seconds=121)


@pytest.mark.parametrize('provider',['deepseek','siliconflow'])
def test_new_profile_is_exactly_bound_to_r3_none_grant_not_old_profile(tmp_path,provider):
    from backend.app.adapters.models.cloud import XingChatGrant
    from backend.app.adapters.models.v6_text_capacity import DeepseekCapacityProfile,CapacityCounterUnavailable
    from backend.app.adapters.models.siliconflow_xing_capacity import XingCapacityProfile,XingCapacityUnavailable
    grant=sim_r3() if provider=='deepseek' else XingChatGrant(
        b.OWNER_V6_TOKEN_GRANT_R3.grant_id+':SIMULATED-profile','SIMULATED-scope',SCOPE,None,('answer',),'SIMULATED')
    path,data,ep,evidence,_=manifest(tmp_path,provider,grant)
    bound=lease(FUTURE);data['execution_lease']=asdict(bound);path.write_text(json.dumps(data))
    cls=DeepseekCapacityProfile if provider=='deepseek' else XingCapacityProfile
    load=lambda:cls.from_manifest(path,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),grant=grant,execution_lease=bound)
    profile=load();assert profile.grant is grant and data['deadline_utc'] is None
    wrong=replace(grant,scope='other') if provider=='deepseek' else replace(grant,scope_id='other')
    with pytest.raises((CapacityCounterUnavailable,XingCapacityUnavailable)):
        type(profile).from_manifest(path,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),grant=wrong,execution_lease=bound)
    data['deadline_utc']=sf.FREE_OWNER_DEADLINE;path.write_text(json.dumps(data))
    with pytest.raises((CapacityCounterUnavailable,XingCapacityUnavailable)):load()


@pytest.mark.parametrize('denied',[False,True])
def test_r3_ds_normal_factory_still_requires_final_native_lease(tmp_path,denied):
    from backend.tests.test_v6_generation_binding import setup_binding,invoke
    from backend.app.application.rule_router_v1 import V6GenerationBinding
    role,old,path,count,guard,wires=setup_binding(tmp_path)
    new=b.V6OwnerActivityGate(path,grant=sim_r3(),clock=lambda:FUTURE,execution_lease=lease(FUTURE));new.activate_revision(old.grant)
    role.gateway.attempt_gate=new;claims=Claims(denied);role.gateway.send_claim_callback=claims
    role=replace(role,v6_binding=V6GenerationBinding(new.grant,new,count))
    if denied:
        with pytest.raises(ProviderRequestNotSent,match='DURABLE_SEND_CHECK'):invoke(role)
        assert not wires and new._used(new._read())==65569
        assert new._read()['v6_owner_activity']['attempts'][0]['status']=='not_sent'
    else:
        assert invoke(role)=='SIMULATED answer' and len(wires)==1
    assert claims.settlements


def test_r2_final_sender_rechecks_clock_after_native_callback(tmp_path):
    from backend.tests.test_v6_generation_binding import setup_binding,invoke
    from backend.app.application.rule_router_v1 import V6GenerationBinding
    role,old,path,count,guard,wires=setup_binding(tmp_path)
    grant=replace(old.grant,version=2,grant_id='SIMULATED-R2',deadline_utc=b.OWNER_V6_TOKEN_GRANT_R2.deadline_utc)
    clock=[datetime(2026,10,10,6,tzinfo=timezone.utc)]
    gate=b.V6OwnerActivityGate(path,grant=grant,clock=lambda:clock[0]);gate.activate_revision(old.grant)
    role.gateway.attempt_gate=gate;claims=Claims()
    claims.check_before_send=lambda **kw:clock.__setitem__(0,NOW)
    role.gateway.send_claim_callback=claims;role=replace(role,v6_binding=V6GenerationBinding(grant,gate,count))
    with pytest.raises(ProviderRequestNotSent,match='REQUEST_BOUND'):invoke(role)
    assert not wires and gate._used(gate._read())==65569


@pytest.mark.parametrize('denied',[False,True])
def test_r3_xing_final_sender_keeps_native_lease_and_timeout(counter,denied):
    from urllib.request import Request
    from backend.app.adapters.models.cloud import XingChatGrant,XingAuthorizedTransport,SimulatedXingSend
    p=sf_permit();grant=XingChatGrant(p.grant_id,'SIMULATED-scope',SCOPE,None,('answer',),'SIMULATED')
    wires=[];claims=Claims(denied)
    transport=XingAuthorizedTransport(grant,free_model_permit=p,send_claim_callback=claims,
        sender=SimulatedXingSend(lambda req,t:wires.append(req.data) or {}),clock=lambda:FUTURE)
    body=dict(model=p.model,messages=[{'role':'user','content':'SIMULATED public'}],max_tokens=512,stream=False,enable_thinking=False)
    estimate=counter.measure('siliconflow',p.model,body,scope=grant.scope_id)
    request=Request('https://api.siliconflow.cn/v1/chat/completions',data=json.dumps(body).encode(),method='POST')
    with transport.authorize(estimate,scope=SCOPE,purpose='answer',request_id=p.calls[0].request_id,timeout_seconds=30):
        token=transport._provider_claim.set(SimpleNamespace())
        try:
            if denied:
                with pytest.raises(ProviderRequestNotSent,match='DURABLE_SEND_CHECK'):transport(request,30)
                assert not wires
            else:
                transport(request,30);assert wires==[request.data]
        finally:transport._provider_claim.reset(token)


def test_explicit_four_hour_owner_end_is_separate_from_finite_native_lease(tmp_path):
    bound=b.V6ExecutionLease('SIMULATED-current-owner','2026-10-10T09:10:54Z',
        '2026-10-10T09:50:54Z','2026-10-10T13:10:54Z')
    clock=[datetime(2026,10,10,9,20,tzinfo=timezone.utc)]
    gate=b.V6OwnerActivityGate(_ledger(tmp_path),grant=sim_r3(),clock=lambda:clock[0],execution_lease=bound)
    gate.initialize_grant();gate.validate_request('public',30,512,model='deepseek-flash')
    assert gate.grant.deadline_utc is None and gate.deadline==bound.deadline
    clock[0]=bound.deadline
    with pytest.raises(AttemptDenied,match='REQUEST_BOUND'):gate.validate_request('public',30,512,model='deepseek-flash')
    with pytest.raises(ValueError,match='EXECUTION_LEASE_INVALID'):
        b.V6ExecutionLease('SIMULATED-beyond-owner','2026-10-10T12:50:54Z','2026-10-10T13:30:54Z','2026-10-10T13:10:54Z')
    with pytest.raises(AttemptDenied,match='EXECUTION_LEASE_REQUIRED'):
        b.V6OwnerActivityGate(tmp_path/'no-init.json',grant=sim_r3(),clock=lambda:NOW)
    assert not (tmp_path/'no-init.json').exists()


@pytest.mark.parametrize('change',[{'lease_id':True},{'lease_id':'../escape'},
    {'not_before_utc':'2026-10-10T09:00:00'},{'deadline_utc':'2026-10-10T09:00:00Z'},
    {'deadline_utc':'2026-10-10T11:00:00Z'},{'owner_deadline_utc':True}])
def test_lease_rejects_bool_path_naive_unbounded_or_overlong_configuration(change):
    args=dict(lease_id='SIMULATED-config',not_before_utc='2026-10-10T09:00:00Z',
        deadline_utc='2026-10-10T09:40:00Z',owner_deadline_utc='2026-10-10T13:10:54Z')
    args.update(change)
    with pytest.raises(ValueError,match='EXECUTION_LEASE_INVALID'):b.V6ExecutionLease(**args)


def test_owner_total_cancellation_is_explicit_and_does_not_remove_native_end():
    bound=b.V6ExecutionLease('SIMULATED-historical-cancel','2030-01-01T09:00:00Z','2030-01-01T09:40:00Z',None)
    assert bound.owner_deadline_utc is None and bound.deadline is not None
