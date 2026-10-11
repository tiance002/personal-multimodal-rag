"""Explicit finite execution leases; SIMULATED inputs, no live DB/model/ledger."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import pytest
from sqlalchemy.engine import Engine
from backend.app.bootstrap import Container
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.domain.scope import Scope
from backend.tests.test_v6_xing_generation_binding import counter,no_live_sender
from eval_center.tests.test_public_r2_ecs_assembly import offline_composition,RUN,SOURCE,freeze
from eval_center.public_paired_reporting import PairedTimeProfile,PairedLeaseTimeProfile,time_profile
from eval_center.public_benchmark_cli import _parser,_paired_time_profile,_load_runtime_composition
from eval_center.public_runner import build_normal_paired_runtime,PairedRuntimeAssemblyBlocked
from eval_center import paired_state as state


class SimulatedClock(datetime):
    current=datetime(2026,10,10,3,tzinfo=timezone.utc)
    @classmethod
    def now(cls,tz=None):return cls.current


@pytest.fixture(autouse=True)
def zero_real_io(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-ONLY')
    monkeypatch.setenv('DEEPSEEK_API_KEY','SIMULATED-ONLY')
    monkeypatch.setattr(Engine,'connect',lambda *a,**k:pytest.fail('REAL DB forbidden'))
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',lambda *a,**k:pytest.fail('REAL model forbidden'))
    SimulatedClock.current=datetime(2026,10,10,3,tzinfo=timezone.utc)
    for target in ('eval_center.public_runner.datetime','eval_center.public_benchmark_cli.datetime'):
        monkeypatch.setattr(target,SimulatedClock)
    monkeypatch.setattr(state,'datetime',SimulatedClock)


def lease(*,end='2026-10-10T07:44:02Z',start='2026-10-10T02:00:00Z',name='owner-lease-small',id='SIMULATED-independent-lease'):
    return time_profile(name,lease_id=id,lease_not_before_utc=start,lease_deadline_utc=end)


@pytest.mark.parametrize('date',['2026-10-10','2026-12-12'])
@pytest.mark.parametrize('name,rounds,count,pages',[('owner-lease-small',40,24,0),('owner-lease-40q-24page',60,40,24)])
def test_explicit_finite_lease_without_owner_calendar_constant(date,name,rounds,count,pages):
    p=lease(start=date+'T08:00:00Z',end=date+'T11:00:00Z',name=name)
    p.validate(question_count=count,omni_page_count=pages)
    now=datetime.fromisoformat(date+'T08:30:00+00:00')
    hard,dispatch,end=p.deadlines(now)
    assert p.owner_deadline_utc is None and type(p) is PairedLeaseTimeProfile
    assert dispatch-now==timedelta(minutes=30) and end-now==timedelta(minutes=rounds)
    assert hard==datetime.fromisoformat(date+'T11:00:00+00:00')
    assert p.availability_error(now) is None
    assert p.availability_error(datetime.fromisoformat(date+'T07:59:59+00:00'))=='execution_lease_not_started'
    assert p.availability_error(hard)=='absolute_deadline_expired'
    assert p.deadlines(hard-timedelta(minutes=1))[1:]==(hard,hard)


@pytest.mark.parametrize('changes',[{'lease_id':None},{'lease_id':''},{'lease_id':'bad space'},
    {'lease_not_before_utc':None},{'lease_deadline_utc':None},{'lease_deadline_utc':'infinity'},
    {'lease_deadline_utc':'2026-10-10T02:00:00'}, {'lease_deadline_utc':'2026-10-10T09:00:00+08:00'},
    {'lease_deadline_utc':'2026-10-10T01:00:00Z'}, {'lease_deadline_utc':'2026-10-10T02:00:00Z'}])
def test_missing_or_unbounded_or_wrong_zone_lease_rejected(changes):
    config=dict(lease_id='SIMULATED-lease',lease_not_before_utc='2026-10-10T02:00:00Z',lease_deadline_utc='2026-10-10T05:00:00Z')
    config.update(changes)
    with pytest.raises(ValueError,match='paired_owner_time_profile_invalid'):
        time_profile('owner-lease-small',**config)


@pytest.mark.parametrize('changes',[{'round_minutes':120},{'dispatch_minutes':60},{'owner_deadline_utc':'2026-10-10T01:00:00Z'}])
def test_round_caps_and_invalid_owner_bound_rejected(changes):
    with pytest.raises(ValueError):replace(lease(),**changes).validate(question_count=24)
    with pytest.raises(ValueError):lease(name='owner-lease-40q-24page').validate(question_count=24,omni_page_count=24)


@pytest.mark.parametrize('name,end,rounds',[('legacy-dev24','2026-10-10T04:25:44Z',40),
    ('owner-small','2026-10-10T07:44:02Z',40),('owner-40q-24page','2026-10-10T07:44:02Z',60)])
def test_old_profile_identity_and_expired_rejection_unchanged(name,end,rounds):
    p=time_profile(name)
    assert type(p) is PairedTimeProfile
    assert vars(p)==dict(name=name,absolute_deadline_utc=end,round_minutes=rounds,dispatch_minutes=30)
    assert p.availability_error(datetime.fromisoformat(end.replace('Z','+00:00')))=='absolute_deadline_expired'
    with pytest.raises(ValueError):time_profile(name,lease_deadline_utc='2026-10-11T03:00:00Z')


def cli_args(path,*,start='2026-10-10T02:00:00Z',end='2026-10-10T07:44:02Z'):
    return _parser().parse_args(['--phase','paired-run','--paired-time-profile','owner-lease-small',
        '--paired-lease-id','SIMULATED-independent-lease','--paired-lease-not-before-utc',start,
        '--paired-lease-deadline-utc',end,'--runtime-entry',str(path),'--runtime-entry-sha256','a'*64])


def test_cli_exposes_exact_config_and_requires_explicit_lease(tmp_path):
    p=_paired_time_profile(cli_args(tmp_path/'SIMULATED-entry.py'))
    assert p==lease()
    args=_parser().parse_args(['--phase','paired-run','--paired-time-profile','owner-lease-small'])
    with pytest.raises(ValueError):_paired_time_profile(args)


@pytest.mark.parametrize('clock,error',[('2026-10-10T01:59:59+00:00','execution_lease_not_started'),
    ('2026-10-10T07:44:02+00:00','absolute_deadline_expired')])
def test_cli_rejects_inactive_lease_before_entry_execution(tmp_path,clock,error):
    SimulatedClock.current=datetime.fromisoformat(clock)
    entry=tmp_path/'SIMULATED-entry.py'
    entry.write_text("raise AssertionError('composition must not execute')\n",encoding='utf-8')
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:
        _load_runtime_composition(cli_args(entry),tmp_path/'SIMULATED-center.sqlite3')
    assert exc.value.missing==[error]
    assert not (tmp_path/'SIMULATED-center.sqlite3').exists()


@pytest.fixture(scope='module')
def native_offline(counter,tmp_path_factory):
    return offline_composition(tmp_path_factory.mktemp('SIMULATED-independent-lease-normal'),counter)


def assemble(composition,cohort,profile=None):
    return build_normal_paired_runtime(composition,source_manifest_path=SOURCE,index_manifest_path=None,
        cohort_path=cohort,run_id=RUN,time_profile=profile or lease())


def test_explicit_lease_reaches_formal_container_factory_quickchain(native_offline):
    composition,receipts,cohort=native_offline
    runtime=assemble(composition,cohort)
    try:
        for name,c in runtime.containers.items():
            item=composition.datasets[name]
            assert type(c) is Container and type(c.quick_chain) is LangChainQuickChain
            role=c.quick_chain.generation_roles['chat_cheap']
            assert role.xing_binding is item.xing_binding
            assert role.gateway.transport.free_model_permit is item.xing_binding.request_window
            assert item.xing_binding.authorized_transport.grant is item.xing_binding.grant
        assert all(not r['sf_wires'] and not r['ds_wires'] for r in receipts.values())
    finally:
        for c in runtime.containers.values():c.engine.dispose()


@pytest.mark.parametrize('dimension',['deadline','run','source','scope','permit_copy'])
def test_new_lease_preserves_native_window_identity_checks(native_offline,dimension):
    composition,receipts,cohort=native_offline
    name=next(iter(composition.datasets));item=composition.datasets[name];xing=item.xing_binding
    fields={'deadline':{'deadline_utc':'2026-10-10T07:43:00Z'},'run':{'run_id':'SIMULATED-other'},
        'source':{'source_manifest_sha256':'f'*64},'scope':{'scope':Scope.from_ids(['SIMULATED-other'])}}
    window=freeze(replace(xing.request_window,**fields.get(dimension,{})))
    item=replace(item,xing_binding=replace(xing,request_window=window))
    bad=replace(composition,datasets={**composition.datasets,name:item})
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:assemble(bad,cohort)
    assert 'frozen_xing_request_window:'+name in exc.value.missing
    assert all(not r['sf_wires'] and not r['ds_wires'] for r in receipts.values())


@pytest.mark.parametrize('clock,error',[('2026-10-10T01:59:59+00:00','execution_lease_not_started'),
    ('2026-10-10T07:44:02+00:00','absolute_deadline_expired')])
def test_normal_assembly_inactive_lease_zero_bootstrap(native_offline,clock,error,monkeypatch):
    composition,_,cohort=native_offline
    SimulatedClock.current=datetime.fromisoformat(clock)
    monkeypatch.setattr('eval_center.public_runner.build_container',lambda *a,**k:pytest.fail('bootstrap must not run'))
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:assemble(composition,cohort)
    assert exc.value.missing==[error]


def test_fresh_lease_cannot_renew_old_run_or_unknown_claim(tmp_path):
    path=tmp_path/'SIMULATED-only-center.sqlite3'
    kwargs=dict(run_id='SIMULATED-old-run',cohort_sha256='a'*64,source_manifest_sha256='b'*64,
        code_sha='c'*64,deadline_utc='2026-10-10T04:25:44Z',expected_qids=['SIMULATED-q'])
    state.register_run(path,**kwargs)
    state.begin_attempt(path,run_id=kwargs['run_id'],qid='SIMULATED-q',arm='CHEAP_ONLY',input_identity_sha256='d'*64)
    state.claim_provider_send(path,run_id=kwargs['run_id'],qid='SIMULATED-q',arm='CHEAP_ONLY',
        request_id='SIMULATED-unknown',input_identity_sha256='d'*64,request_identity_sha256='e'*64,
        provider='siliconflow',model='SIMULATED-model',role='chat_cheap')
    state.finish_provider_send(path,request_id='SIMULATED-unknown',status='unknown',error_code='REQUEST_TIMEOUT')
    before=state.run_record(path,kwargs['run_id']);unknown=state.provider_send_claim_record(path,request_id='SIMULATED-unknown')
    SimulatedClock.current=datetime(2026,10,10,5,tzinfo=timezone.utc)
    with pytest.raises(state.PairedContractError,match='run_identity_conflict'):
        state.register_run(path,**{**kwargs,'deadline_utc':'2026-10-10T07:44:02Z'})
    assert state.run_record(path,kwargs['run_id'])==before
    with pytest.raises(state.PairedContractError,match='run_deadline_expired'):
        state.begin_attempt(path,run_id=kwargs['run_id'],qid='SIMULATED-q',arm='DYNAMIC',input_identity_sha256='d'*64)
    # Existing safe terminal transition pauses an expired running Run; no
    # identity/deadline/claim field changes and no new Attempt is inserted.
    assert state.run_record(path,kwargs['run_id'])=={**before,'status':'paused'}
    assert state.provider_send_claim_record(path,request_id='SIMULATED-unknown')==unknown
    assert set(state.attempts_for_qid(path,run_id=kwargs['run_id'],qid='SIMULATED-q'))=={'CHEAP_ONLY'}
    (tmp_path/'lease-no-renewal-observations.json').write_text(json.dumps(dict(
        execution_kind='SIMULATED',old_run_identity_unchanged=True,expired_run_status='paused',unknown_claim_unchanged=True,
        real_provider_calls=0,real_database_calls=0)),encoding='utf-8')


@pytest.fixture(scope='module')
def r3_native_offline(native_offline,tmp_path_factory):
    """New independent SIMULATED native R3 grants; never revise old fixtures."""
    from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT_R3,V6OwnerActivityGate,V6ExecutionLease
    from backend.app.adapters.models.cloud import XingAuthorizedTransport,SimulatedXingSend,SiliconFlowAuthorizedTransport
    from backend.app.application.rule_router_v1 import V6XingGenerationBinding
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.application.budget import InMemoryBudgetGate
    from backend.app.domain.model_registry import ModelRegistry
    composition,receipts,cohort=native_offline
    root=tmp_path_factory.mktemp('SIMULATED-native-R3-assembly')
    assert root.drive.upper()=='E:'
    legacy=next(iter(composition.datasets.values())).v6_binding.activity_gate.path
    old=json.loads(Path(legacy).read_text(encoding='utf-8'))
    ledger=root/'SIMULATED-r3-ledger.json'
    ledger.write_text(json.dumps({k:v for k,v in old.items() if k!='v6_owner_activity'}),encoding='utf-8')
    grant=replace(OWNER_V6_TOKEN_GRANT_R3,execution_kind='SIMULATED')
    native_lease=V6ExecutionLease('SIMULATED-R3-finite-run-lease','2026-10-10T09:50:00Z',
        '2026-10-10T10:40:00Z',None)
    gate=V6OwnerActivityGate(ledger,grant=grant,clock=lambda:SimulatedClock.now(timezone.utc),execution_lease=native_lease)
    gate.initialize_grant()
    registry=ModelRegistry.frozen_defaults();items={}
    for name,item in composition.datasets.items():
        original=item.xing_binding
        permit=freeze(replace(original.request_window,deadline_utc=None,
            owner_activity_id=OWNER_V6_TOKEN_GRANT_R3.grant_id,
            execution_lease=native_lease,
            grant_id=OWNER_V6_TOKEN_GRANT_R3.grant_id+':'+name+':chat_cheap'))
        sg=replace(original.grant,grant_id=permit.grant_id,deadline_utc=None)
        transport=XingAuthorizedTransport(sg,send_claim_callback=original.authorized_transport.send_claim_callback,
            free_model_permit=permit,sender=SimulatedXingSend(lambda *a:pytest.fail('assembly must not send')))
        xing=V6XingGenerationBinding(sg,original.capacity_counter,transport,permit)
        eb=item.free_model_bindings['embedding']
        ep=freeze(replace(eb.permit,deadline_utc=None,owner_activity_id=OWNER_V6_TOKEN_GRANT_R3.grant_id,
            execution_lease=native_lease,
            grant_id=OWNER_V6_TOKEN_GRANT_R3.grant_id+':'+name+':embedding'))
        et=SiliconFlowAuthorizedTransport(ep,send_claim_callback=eb.authorized_transport.send_claim_callback,
            sender=SimulatedXingSend(lambda *a:pytest.fail('assembly must not send')))
        guards=dict(item.model_usage_guards)
        guards['chat_cheap']=BudgetUsageGuard(InMemoryBudgetGate(0),registry.select('chat_cheap'),0,zero_price_permit=permit)
        guards['embedding']=BudgetUsageGuard(InMemoryBudgetGate(0),registry.select('embedding'),0,zero_price_permit=ep)
        items[name]=replace(item,xing_binding=xing,v6_binding=replace(item.v6_binding,grant=grant,activity_gate=gate),
            free_model_bindings={'embedding':replace(eb,permit=ep,authorized_transport=et)},model_usage_guards=guards)
    return replace(composition,datasets=items),receipts,cohort,ledger


def test_registered_native_r3_no_owner_cutoff_actual_formal_bootstrap_after_r2_expiry(r3_native_offline,tmp_path):
    from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT_R3
    composition,receipts,cohort,ledger=r3_native_offline
    SimulatedClock.current=datetime(2026,10,10,10,tzinfo=timezone.utc)
    native_lease=next(iter(composition.datasets.values())).v6_binding.activity_gate.execution_lease
    p=time_profile('owner-lease-small',native_execution_lease=native_lease)
    before=ledger.read_bytes()
    runtime=assemble(composition,cohort,p)
    try:
        for name,c in runtime.containers.items():
            assert type(c) is Container and type(c.quick_chain) is LangChainQuickChain
            role=c.quick_chain.generation_roles['chat_cheap']
            permit=role.gateway.transport.free_model_permit
            assert permit is composition.datasets[name].xing_binding.request_window
            assert permit.owner_activity_id==OWNER_V6_TOKEN_GRANT_R3.grant_id
            assert permit.owner_total_deadline_utc is None and permit.deadline_utc is None
            assert permit.owner_deadline()==native_lease.deadline and permit.execution_lease is native_lease
            assert c.quick_chain.generation_roles['chat_expensive'].v6_binding.activity_gate.grant.deadline_utc is None
            assert c.store.embedding_provider.free_binding.permit.owner_activity_id==permit.owner_activity_id
        assert ledger.read_bytes()==before
        assert all(not r['sf_wires'] and not r['ds_wires'] for r in receipts.values())
        (tmp_path/'native-r3-bootstrap-observations.json').write_text(json.dumps(dict(
            execution_kind='SIMULATED',formal_container_count=len(runtime.containers),
            actual_native_grant_id=OWNER_V6_TOKEN_GRANT_R3.grant_id,owner_total_deadline=None,
            native_permit_type='SiliconFlowFreePermit',finite_lease_deadline=p.absolute_deadline_utc,
            observed_utc=SimulatedClock.current.isoformat(),ledger_unchanged=True,
            real_provider_calls=0,real_database_calls=0)),encoding='utf-8')
    finally:
        for c in runtime.containers.values():c.engine.dispose()


@pytest.mark.parametrize('mismatch',['missing_lease_profile','wrong_owner','owner_deadline_present',
    'grant_id','grant_copy','grant_version','query_missing','query_wrong_owner','query_old_deadline','real_label'])
def test_native_r3_wrong_activity_or_nullable_deadline_not_generically_admitted(r3_native_offline,mismatch):
    composition,receipts,cohort,ledger=r3_native_offline
    name=next(iter(composition.datasets));item=composition.datasets[name]
    SimulatedClock.current=datetime(2026,10,10,10,tzinfo=timezone.utc)
    profile=time_profile('owner-lease-small',native_execution_lease=item.v6_binding.activity_gate.execution_lease)
    if mismatch=='missing_lease_profile':
        profile=time_profile('owner-small');SimulatedClock.current=datetime(2026,10,10,3,tzinfo=timezone.utc)
    elif mismatch in {'wrong_owner','owner_deadline_present'}:
        change={'owner_activity_id':'SIMULATED-foreign'} if mismatch=='wrong_owner' else {'deadline_utc':'2026-10-10T07:44:02Z'}
        item=replace(item,xing_binding=replace(item.xing_binding,request_window=freeze(replace(item.xing_binding.request_window,**change))))
    elif mismatch in {'grant_id','grant_copy','grant_version'}:
        change={'grant_id':'SIMULATED-foreign'} if mismatch=='grant_id' else {'version':2} if mismatch=='grant_version' else {}
        item=replace(item,v6_binding=replace(item.v6_binding,grant=replace(item.v6_binding.grant,**change)))
    elif mismatch=='query_missing':item=replace(item,free_model_bindings={})
    elif mismatch in {'query_wrong_owner','query_old_deadline'}:
        eb=item.free_model_bindings['embedding']
        change={'owner_activity_id':'SIMULATED-foreign'} if mismatch=='query_wrong_owner' else {'deadline_utc':'2026-10-10T07:44:02Z','owner_activity_id':None}
        item=replace(item,free_model_bindings={'embedding':replace(eb,permit=freeze(replace(eb.permit,**change)))})
    changed=replace(composition,datasets={**composition.datasets,name:item},
        execution_kind='REAL' if mismatch=='real_label' else composition.execution_kind)
    before=ledger.read_bytes()
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:assemble(changed,cohort,profile)
    expected='independent_shared_query_embedding_plan:' if mismatch.startswith('query_') else 'frozen_xing_request_window:'
    assert expected+name in exc.value.missing
    assert ledger.read_bytes()==before
    assert all(not r['sf_wires'] and not r['ds_wires'] for r in receipts.values())


def composition_with_native_lease(case,native,root):
    """Separate local SIMULATED fixture; preserves the inherited ledger bytes."""
    from backend.app.application.validation_usd_budget import V6OwnerActivityGate
    from backend.app.adapters.models.cloud import XingAuthorizedTransport,SimulatedXingSend,SiliconFlowAuthorizedTransport
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.application.budget import InMemoryBudgetGate
    from backend.app.domain.model_registry import ModelRegistry
    composition,receipts,cohort,source_ledger=case
    ledger=root/'SIMULATED-window-ledger.json';ledger.write_bytes(source_ledger.read_bytes())
    grant=next(iter(composition.datasets.values())).v6_binding.grant
    gate=V6OwnerActivityGate(ledger,grant=grant,execution_lease=native,clock=lambda:SimulatedClock.now(timezone.utc))
    gate.initialize_grant();registry=ModelRegistry.frozen_defaults();items={}
    for name,item in composition.datasets.items():
        x=item.xing_binding;p=freeze(replace(x.request_window,execution_lease=native))
        t=XingAuthorizedTransport(x.grant,send_claim_callback=x.authorized_transport.send_claim_callback,
            free_model_permit=p,sender=SimulatedXingSend(lambda *a:pytest.fail('assembly must not send')))
        eb=item.free_model_bindings['embedding'];ep=freeze(replace(eb.permit,execution_lease=native))
        et=SiliconFlowAuthorizedTransport(ep,send_claim_callback=eb.authorized_transport.send_claim_callback,
            sender=SimulatedXingSend(lambda *a:pytest.fail('assembly must not send')))
        guards=dict(item.model_usage_guards)
        guards['chat_cheap']=BudgetUsageGuard(InMemoryBudgetGate(0),registry.select('chat_cheap'),0,zero_price_permit=p)
        guards['embedding']=BudgetUsageGuard(InMemoryBudgetGate(0),registry.select('embedding'),0,zero_price_permit=ep)
        items[name]=replace(item,xing_binding=replace(x,request_window=p,authorized_transport=t),
            v6_binding=replace(item.v6_binding,activity_gate=gate),model_usage_guards=guards,
            free_model_bindings={'embedding':replace(eb,permit=ep,authorized_transport=et)})
    return replace(composition,datasets=items),receipts,cohort,ledger


def finite_owner_native():
    from backend.app.application.validation_usd_budget import V6ExecutionLease
    # Fixed history fixture only. Run40min is shorter than its configured
    # Owner end; no test date is exported into a production constant.
    return V6ExecutionLease('SIMULATED-owner-finite-run','2026-10-10T10:00:00Z',
        '2026-10-10T10:40:00Z','2026-10-10T12:00:00Z')


def test_configured_finite_owner_deadline_and_shorter_native_run_formal_bootstrap(r3_native_offline,tmp_path):
    native=finite_owner_native();composition,receipts,cohort,ledger=composition_with_native_lease(r3_native_offline,native,tmp_path)
    SimulatedClock.current=datetime(2026,10,10,10,5,tzinfo=timezone.utc)
    profile=time_profile('owner-lease-small',native_execution_lease=native)
    assert profile.owner_deadline_utc==native.owner_deadline_utc and profile.absolute_deadline_utc==native.deadline_utc
    before=ledger.read_bytes();runtime=assemble(composition,cohort,profile)
    try:
        assert all(type(c) is Container and type(c.quick_chain) is LangChainQuickChain for c in runtime.containers.values())
        assert all(item.xing_binding.request_window.owner_total_deadline_utc==native.owner_deadline_utc
            and item.xing_binding.request_window.execution_lease is native for item in composition.datasets.values())
        args=cli_args(tmp_path/'SIMULATED-entry.py',start=native.not_before_utc,end=native.deadline_utc)
        args.paired_lease_id=native.lease_id
        assert _paired_time_profile(args,composition)==profile
        assert ledger.read_bytes()==before and all(not r['sf_wires'] and not r['ds_wires'] for r in receipts.values())
        (tmp_path/'finite-owner-observations.json').write_text(json.dumps(dict(execution_kind='SIMULATED',
            owner_deadline_utc=profile.owner_deadline_utc,native_run_start_utc=profile.not_before_utc,
            native_run_deadline_utc=profile.absolute_deadline_utc,formal_container_count=len(runtime.containers),
            owner_start_field_present=hasattr(native,'owner_not_before_utc'),
            owner_start_utc=profile.owner_not_before_utc,real_provider_calls=0,real_database_calls=0)),encoding='utf-8')
    finally:
        for c in runtime.containers.values():c.engine.dispose()


@pytest.mark.parametrize('clock,error',[('2026-10-10T09:59:59+00:00','execution_lease_not_started'),
    ('2026-10-10T10:40:00+00:00','absolute_deadline_expired'),('2026-10-10T12:00:00+00:00','owner_total_deadline_expired')])
def test_configured_native_window_before_start_owner_cutoff_and_run_expiry_zero_bootstrap(r3_native_offline,tmp_path,clock,error,monkeypatch):
    native=finite_owner_native();composition,_,cohort,_=composition_with_native_lease(r3_native_offline,native,tmp_path)
    SimulatedClock.current=datetime.fromisoformat(clock)
    profile=time_profile('owner-lease-small',native_execution_lease=native)
    monkeypatch.setattr('eval_center.public_runner.build_container',lambda *a,**k:pytest.fail('no bootstrap'))
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:assemble(composition,cohort,profile)
    assert exc.value.missing==[error]


def test_native_profile_mismatch_cannot_replace_registered_lease_or_owner_configuration(r3_native_offline,tmp_path):
    native=finite_owner_native();composition,_,cohort,_=composition_with_native_lease(r3_native_offline,native,tmp_path)
    SimulatedClock.current=datetime(2026,10,10,10,5,tzinfo=timezone.utc)
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:
        assemble(composition,cohort,lease(start=native.not_before_utc,end=native.deadline_utc,id='SIMULATED-other'))
    assert exc.value.missing==['native_execution_lease_profile_binding:ValueError']
    with pytest.raises(ValueError,match='native_execution_lease_mismatch'):
        time_profile('owner-lease-small',native_execution_lease=native,lease_deadline_utc='2026-10-10T10:50:00Z')


def test_r3_final_native_send_rechecks_finite_run_deadline_after_durable_claim(r3_native_offline,tmp_path,monkeypatch):
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.adapters.models.deepseek import SimulatedDeepSeekTransport
    from backend.app.application.rule_router_v1 import GenerationRole
    from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.ports.providers import ProviderRequestNotSent
    from eval_center.paired_state import SQLiteProviderSendClaims,CurrentProviderSendClaims,provider_send_claim_scope
    from backend.tests.test_v6_generation_binding import envelope
    native=finite_owner_native();composition,_,_,ledger=composition_with_native_lease(r3_native_offline,native,tmp_path)
    item=next(iter(composition.datasets.values()));gate=item.v6_binding.activity_gate
    SimulatedClock.current=datetime(2026,10,10,10,5,tzinfo=timezone.utc)
    path=tmp_path/'SIMULATED-native-send-center.sqlite3';run='SIMULATED-R3-final-send';qid='SIMULATED-q'
    state.register_run(path,run_id=run,cohort_sha256='1'*64,source_manifest_sha256='2'*64,
        code_sha='3'*64,deadline_utc=native.deadline_utc,expected_qids=[qid])
    state.begin_attempt(path,run_id=run,qid=qid,arm='EXPENSIVE_ONLY',input_identity_sha256='4'*64)
    callback=SQLiteProviderSendClaims(path,run,qid,'EXPENSIVE_ONLY','4'*64)
    original=callback.check_before_send
    def expire_after_claim(*,request_id):
        original(request_id=request_id)
        SimulatedClock.current=native.deadline
    monkeypatch.setattr(type(callback),'check_before_send',lambda self,**kwargs:expire_after_claim(**kwargs))
    wires=[]
    gateway=ProviderFactory(ModelRegistry.frozen_defaults(),enabled_roles=('chat_expensive',)).build_v6_gateway(
        activity_gate=gate,gate_types=DEEPSEEK_GATE_TYPES,api_key='SIMULATED-ONLY',
        transport=SimulatedDeepSeekTransport(lambda req,t:wires.append(req.data) or {}))
    gateway.send_claim_callback=item.v6_binding.send_claim_callback
    role=GenerationRole('chat_expensive',gateway,v6_binding=item.v6_binding,
        usage_guard=item.model_usage_guards['chat_expensive'])
    used=gate._used(gate._read())
    with provider_send_claim_scope(callback),pytest.raises(ProviderRequestNotSent) as denial:
        role.invoke(envelope(),run_id=run,request_id='SIMULATED-final-claim',scope=item.scope)
    (tmp_path/'final-native-send-observations.json').write_text(json.dumps(dict(
        execution_kind='SIMULATED',denial_code=str(denial.value),gateway_receipt=getattr(gateway,'last_receipt',None),
        simulated_wire_count=len(wires),real_provider_calls=0,real_database_calls=0)),encoding='utf-8')
    child=state.provider_send_claim_record(path,request_id='SIMULATED-final-claim')
    assert child is not None and child['send_claimed_at_utc']
    assert not wires and gate._used(gate._read())==used
    before=child.copy()
    with provider_send_claim_scope(callback),pytest.raises(ProviderRequestNotSent):
        role.invoke(envelope(),run_id=run,request_id='SIMULATED-final-claim',scope=item.scope)
    assert not wires and state.provider_send_claim_record(path,request_id='SIMULATED-final-claim')==before
