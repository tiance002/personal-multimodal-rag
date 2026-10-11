"""SIMULATED providers; pinned Xing counter and SQLite execute REAL OFFLINE."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os

import pytest

from backend.tests.test_v6_xing_generation_binding import counter, no_live_sender, NOW, DEADLINE, response
from backend.app.adapters.models.cloud import (
    SimulatedXingSend, XING_CALIBRATION_REQUEST_ID, XING_CALIBRATION_WIRE_SHA256,
    XING_CALIBRATION_SCOPE, XingCohortCalibrationClaimStore,
    XingCalibrationIdentity, LEGACY_XING_CALIBRATION_IDENTITY, XING_OWNER_CALIBRATION_REQUEST_ID,
)
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from eval_center.paired_state import (
    register_run, SQLiteProviderSendClaims, CurrentProviderSendClaims,
    provider_send_claim_scope, provider_send_claim_record,
)
from scripts.verify_v6_xing_calibration_once import (
    RUN_ID, admit_identity, compose_simulated, invoke_simulated, verify_frozen_sources,
    freeze_identity,configuration_admission,historical_runs_digest,
)


def setup(counter, tmp_path, monkeypatch, *, result=None, budget=100, clock=lambda: NOW,
          identity=LEGACY_XING_CALIBRATION_IDENTITY):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr('eval_center.paired_state.datetime', FixedDatetime)
    path = tmp_path / 'SIMULATED-existing-center.sqlite3'
    register_run(path, run_id=identity.run_id, cohort_sha256='a' * 64,
        source_manifest_sha256='b' * 64, code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',
        deadline_utc=DEADLINE, now_utc=NOW.isoformat(), expected_qids=[identity.request_id],
        execution_purpose='CALIBRATION')
    bound = SQLiteProviderSendClaims(path, identity.run_id,
        identity.request_id, 'CHEAP_ONLY', XING_CALIBRATION_WIRE_SHA256)
    callback = CurrentProviderSendClaims()
    ledger, wires = InMemoryBudgetGate(budget), []
    def send(request, timeout):
        assert timeout == 10
        row = provider_send_claim_record(path, request_id=identity.request_id)
        assert row['status'] == 'pending' and row['purpose'] == 'calibration'
        wires.append(request.data)
        if isinstance(result, Exception): raise result
        return result if result is not None else response()
    def build():
        return compose_simulated(counter=counter, callback=callback, budget_gate=ledger,
            sender=SimulatedXingSend(send), price_html=os.path.join(os.environ['XING_TEST_ASSETS'],
                'siliconflow-models.html'), clock=clock, identity=identity)
    gateway, transport = build()
    return gateway, transport, ledger, wires, bound, path, build


def test_fixed_new_run_is_rejected_before_credentials_database_or_network():
    assert RUN_ID == 'v6-xing-calibration-20261009-r1'
    assert RUN_ID != XingCohortCalibrationClaimStore.run_id
    assert admit_identity(now=NOW) == 'CALIBRATION_IDENTITY_MAPPING_NOT_FROZEN'
    assert verify_frozen_sources()


def test_native_new_run_receipt_is_rejected_by_frozen_store_zero_wire(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, _, path, _ = setup(counter, tmp_path, monkeypatch)
    register_run(path, run_id=RUN_ID, cohort_sha256='d' * 64,
        source_manifest_sha256='e' * 64, code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',
        deadline_utc=DEADLINE, now_utc=NOW.isoformat(), expected_qids=[XING_CALIBRATION_REQUEST_ID],
        execution_purpose='CALIBRATION')
    new_bound = SQLiteProviderSendClaims(path, RUN_ID, XING_CALIBRATION_REQUEST_ID,
        'CHEAP_ONLY', XING_CALIBRATION_WIRE_SHA256)
    with provider_send_claim_scope(new_bound), pytest.raises(ProviderRequestNotSent, match='XING_DURABLE_CLAIM_DENIED'):
        invoke_simulated(gateway)
    row = provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)
    assert row['run_id'] == RUN_ID and row['status'] == 'pending'
    assert not wires and next(iter(ledger.reservations.values()))['state'] == 'released'


def test_same_final_wire_counter_permit_model_scope_and_native_claim(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, bound, path, _ = setup(counter, tmp_path, monkeypatch)
    with provider_send_claim_scope(bound): invoke_simulated(gateway)
    assert len(wires) == 1
    wire, = wires
    body = json.loads(wire)
    assert body == dict(model='XingChenAGI/Xing4.0-29B', messages=[{'role':'user','content':'Reply exactly OK.'}],
                       max_tokens=32, stream=False, enable_thinking=False)
    assert hashlib.sha256(wire).hexdigest() == XING_CALIBRATION_WIRE_SHA256
    measured = counter.measure('siliconflow', body['model'], body, scope=XING_CALIBRATION_SCOPE, wire_bytes=wire)
    assert measured.wire_bytes == wire and 0 < measured.reservation_bound <= 32
    assert measured.production_ready is False and measured.hosted_total_token_bound is None
    assert gateway.receipts[0]['wire_request_sha256'] == measured.wire_request_sha256
    assert provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)['status'] == 'simulated'
    assert next(iter(ledger.reservations.values()))['state'] == 'unknown'


@pytest.mark.parametrize('condition', ['claim_unbound', 'budget_denied', 'expired'])
def test_pretransport_denials_send_zero(counter, tmp_path, monkeypatch, condition):
    expires = lambda: datetime(2026, 10, 10, 4, 25, 40, tzinfo=timezone.utc)
    gateway, transport, ledger, wires, bound, path, _ = setup(counter, tmp_path, monkeypatch,
        budget=0 if condition == 'budget_denied' else 100, clock=expires if condition == 'expired' else lambda: NOW)
    with pytest.raises(ProviderRequestNotSent):
        if condition == 'claim_unbound': invoke_simulated(gateway)
        else:
            with provider_send_claim_scope(bound): invoke_simulated(gateway)
    assert not wires and provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID) is None
    if condition == 'claim_unbound':
        assert next(iter(ledger.reservations.values()))['state'] == 'released'
    else: assert not ledger.reservations


@pytest.mark.parametrize('unknown', [False, True])
def test_success_or_unknown_same_id_reconstruction_never_resends(counter, tmp_path, monkeypatch, unknown):
    gateway, transport, ledger, wires, bound, path, build = setup(counter, tmp_path, monkeypatch,
        result=TimeoutError('SIMULATED uncertain send') if unknown else None)
    with provider_send_claim_scope(bound):
        if unknown:
            with pytest.raises(ProviderUnavailable): invoke_simulated(gateway)
        else: invoke_simulated(gateway)
    row = provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)
    assert row['status'] == ('unknown' if unknown else 'simulated') and len(wires) == 1
    restored, _ = build()
    with provider_send_claim_scope(bound), pytest.raises(ProviderRequestNotSent): invoke_simulated(restored)
    assert len(wires) == 1 and provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID) == row
    assert [r['state'] for r in ledger.reservations.values()] == ['unknown', 'released']


def new_identity():
    return XingCalibrationIdentity('d95a0082-20a2-43e8-97bc-881f6b83343e',XING_OWNER_CALIBRATION_REQUEST_ID)


@pytest.mark.parametrize('unknown',[False,True])
def test_new_identity_native_recovery_and_history_unchanged(counter,tmp_path,monkeypatch,unknown):
    identity=new_identity()
    gateway,transport,ledger,wires,bound,path,build=setup(counter,tmp_path,monkeypatch,identity=identity,
        result=TimeoutError('SIMULATED unknown') if unknown else None)
    register_run(path,run_id=LEGACY_XING_CALIBRATION_IDENTITY.run_id,cohort_sha256='d'*64,
        source_manifest_sha256='e'*64,code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',
        deadline_utc=DEADLINE,now_utc=NOW.isoformat(),expected_qids=[XING_CALIBRATION_REQUEST_ID],execution_purpose='CALIBRATION')
    history=historical_runs_digest(path,identity.run_id)
    with provider_send_claim_scope(bound):
        if unknown:
            with pytest.raises(ProviderUnavailable):invoke_simulated(gateway)
        else:invoke_simulated(gateway)
    row=provider_send_claim_record(path,request_id=identity.request_id)
    assert row['run_id']==identity.run_id and row['status']==('unknown' if unknown else 'simulated')
    assert row['result']['dispatch_contract']['calibration_run_id']==identity.run_id
    assert row['result']['dispatch_contract']['request_id']==identity.request_id
    assert len(wires)==1 and hashlib.sha256(wires[0]).hexdigest()==XING_CALIBRATION_WIRE_SHA256
    restarted,_=build()
    with provider_send_claim_scope(bound),pytest.raises(ProviderRequestNotSent):invoke_simulated(restarted)
    assert len(wires)==1 and provider_send_claim_record(path,request_id=identity.request_id)==row
    assert historical_runs_digest(path,identity.run_id)==history
    assert admit_identity(identity,now=NOW) is None
    assert admit_identity(identity,now=datetime(2026,10,11,tzinfo=timezone.utc)) is not None


def test_cross_run_receipt_cannot_authorize_new_identity(counter,tmp_path,monkeypatch):
    identity=new_identity()
    gateway,_,ledger,wires,_,path,_=setup(counter,tmp_path,monkeypatch,identity=identity)
    other='1d0c3fbd-a346-4b04-bceb-4e132f8475f6'
    register_run(path,run_id=other,cohort_sha256='d'*64,source_manifest_sha256='e'*64,
        code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',deadline_utc=DEADLINE,
        now_utc=NOW.isoformat(),expected_qids=[identity.request_id],execution_purpose='CALIBRATION')
    wrong=SQLiteProviderSendClaims(path,other,identity.request_id,identity.arm,XING_CALIBRATION_WIRE_SHA256)
    with provider_send_claim_scope(wrong),pytest.raises(ProviderRequestNotSent,match='XING_DURABLE_CLAIM_DENIED'):
        invoke_simulated(gateway)
    assert not wires and next(iter(ledger.reservations.values()))['state']=='released'


def test_changed_request_or_scope_is_rejected_without_claim(counter,tmp_path,monkeypatch):
    identity=new_identity()
    gateway,_,ledger,wires,bound,path,_=setup(counter,tmp_path,monkeypatch,identity=identity)
    from backend.app.ports.model_access import model_access
    with provider_send_claim_scope(bound),model_access('chat_cheap',allowed=True),pytest.raises(ProviderRequestNotSent):
        gateway.generate_with_xing_scope([{'role':'user','content':'Reply exactly OK.'}],timeout_seconds=10,max_tokens=32,
            scope=identity.scope,purpose='calibration',request_id=identity.request_id+'-retry')
    with pytest.raises(ProviderRequestNotSent):XingCalibrationIdentity(identity.run_id,identity.request_id+'-retry').validate()
    with pytest.raises(ProviderRequestNotSent):XingCalibrationIdentity(identity.run_id,identity.request_id,scope='other').validate()
    assert not wires and not ledger.reservations
    assert provider_send_claim_record(path,request_id=identity.request_id) is None


def test_frozen_mapping_reads_same_run_and_refuses_new_identity(tmp_path):
    mapping=tmp_path/'frozen.json'
    state=tmp_path/'existing-center.sqlite3'
    first,record=freeze_identity(mapping,state_path=state)
    second,again=freeze_identity(mapping,state_path=state)
    assert first==second and first.run_id!=first.request_id and record==again
    assert first.request_id==XING_OWNER_CALIBRATION_REQUEST_ID
    record['request_id']+='-retry'
    mapping.write_text(json.dumps(record),encoding='utf-8')
    with pytest.raises(ValueError,match='FROZEN_IDENTITY_MAPPING_MISMATCH'):freeze_identity(mapping,state_path=state)


def test_default_business_db_and_zero_budget_never_admit(tmp_path):
    from backend.app.config import Settings
    missing,db=configuration_admission(Settings(),key_present=False,state_path=tmp_path/'absent.sqlite3')
    assert 'NORMAL_BUDGET_DATABASE_NOT_PUBLIC_ISOLATED' in missing
    assert 'MONTHLY_BUDGET_ZERO' in missing and 'POSITIVE_MONTHLY_RESERVATION_NOT_CONFIGURED' in missing
    assert 'CONFIGURED_SILICONFLOW_CREDENTIAL_MISSING' in missing
    assert db['identity']=='REJECTED_BY_EXISTING_PUBLIC_ISOLATION_GUARD'


# V6 window contracts. All provider sends below are explicit SIMULATED fixtures.
from dataclasses import replace
from backend.app.adapters.models.cloud import V6XingPlannedCall, V6XingRequestWindow
from backend.app.application.rule_router_v1 import GenerationRole
from backend.tests.test_v6_xing_generation_binding import setup as ordinary_setup, invoke as ordinary_invoke, PUBLIC_SCOPE
from eval_center.paired_state import begin_attempt, stable_provider_request_id


def frozen_window(binding, qids, *, run='SIMULATED-window', helpers=()):
    calls = tuple(V6XingPlannedCall(stable_provider_request_id(run_id=run, qid=qid,
        arm=arm, purpose=purpose), qid, arm, purpose, 8192, 512)
        for qid in qids for arm in ('CHEAP_ONLY', 'DYNAMIC') for purpose in ('answer', *helpers))
    plan = V6XingRequestWindow(run, binding.grant.grant_id, binding.grant.scope, DEADLINE,
        'a'*64, 'b'*64, calls, '0'*64)
    return replace(plan, frozen_plan_sha256=plan.fingerprint)


def window_fixture(counter, tmp_path, *, helpers=(), qids=None):
    role, transport, ledger, wires, factory = ordinary_setup(counter,
        purposes=('answer', 'understand', 'rewrite', 'expansion'))
    transport.send_claim_callback = CurrentProviderSendClaims()
    qids = qids or [f'SIMULATED-q{i:02d}' for i in range(8)]
    plan = frozen_window(role.xing_binding, qids, helpers=helpers)
    binding = replace(role.xing_binding, request_window=plan)
    gateway = factory.build('chat_cheap', xing_binding=binding)
    role = GenerationRole('chat_cheap', gateway, xing_binding=binding)
    path = tmp_path/'SIMULATED-existing-center.sqlite3'
    register_run(path, run_id=plan.run_id, cohort_sha256=plan.cohort_sha256,
        source_manifest_sha256=plan.source_manifest_sha256, code_sha='c'*40,
        deadline_utc=DEADLINE, now_utc=NOW.isoformat(), expected_qids=qids)
    begun=set()
    def invoke(call):
        if (call.qid,call.arm) not in begun:
            begin_attempt(path, run_id=plan.run_id, qid=call.qid, arm=call.arm,
                input_identity_sha256='d'*64, now_utc=NOW.isoformat())
            begun.add((call.qid,call.arm))
        cb = SQLiteProviderSendClaims(path,plan.run_id,call.qid,call.arm,'d'*64)
        with provider_send_claim_scope(cb):
            if call.purpose=='answer':
                return ordinary_invoke(role,request_id=call.request_id)
            return role.invoke_auxiliary([{'role':'user','content':'SIMULATED helper'}],
                purpose=call.purpose, request_id=call.request_id, run_id=plan.run_id,
                scope=PUBLIC_SCOPE, timeout_seconds=10, max_tokens=256)
    return role, transport, ledger, wires, factory, plan, path, invoke


def test_default_ten_cap_still_blocks_eleventh(counter):
    role, _, ledger, wires, _ = ordinary_setup(counter)
    for i in range(10): ordinary_invoke(role,request_id=f'SIMULATED-default-{i}')
    with pytest.raises(ProviderRequestNotSent,match='MODEL_REQUEST_BUDGET_EXCEEDED'):
        ordinary_invoke(role,request_id='SIMULATED-default-11')
    assert role.gateway.requests==10 and len(wires)==len(ledger.reservations)==10


def test_frozen_sixteen_call_window_and_default_token_budget(counter,tmp_path):
    role, _, ledger, wires, _, plan, path, invoke = window_fixture(counter,tmp_path)
    assert role.gateway.max_requests==10 and role.gateway.max_planned_tokens==20000
    for call in plan.calls: invoke(call)
    assert role.gateway.requests==len(wires)==len(ledger.reservations)==16
    assert role.gateway.planned_tokens>0
    assert all(provider_send_claim_record(path,request_id=c.request_id)['status']=='simulated' for c in plan.calls)
    with pytest.raises(ProviderRequestNotSent,match='XING_REQUEST_ALREADY_CONSUMED'):invoke(plan.calls[0])
    with pytest.raises(ProviderRequestNotSent,match='V6_XING_WINDOW_CALL_NOT_PLANNED'):
        ordinary_invoke(role,request_id='SIMULATED-extra-17')
    assert role.gateway.requests==len(wires)==len(ledger.reservations)==16


def test_purpose_counters_are_not_reset_and_helpers_must_be_explicit(counter,tmp_path):
    role, _, _, wires, _, plan, _, invoke = window_fixture(counter,tmp_path,qids=['q'],helpers=('rewrite',))
    counts=[]
    for call in plan.calls:
        invoke(call); counts.append((role.gateway.requests,role.gateway.planned_tokens))
    assert [x[0] for x in counts]==[1,2,3,4]
    assert all(b[1]>a[1] for a,b in zip(counts,counts[1:])) and len(wires)==4
    with pytest.raises(ProviderRequestNotSent,match='V6_XING_WINDOW_CALL_NOT_PLANNED'):
        role.invoke_auxiliary([{'role':'user','content':'SIMULATED'}],purpose='expansion',
            request_id='SIMULATED-unplanned-helper',run_id=plan.run_id,scope=PUBLIC_SCOPE)
    assert role.gateway.requests==4


@pytest.mark.parametrize('change', ['bool','model','role','scope','deadline','hash','duplicate','expensive_answer','calibration','many_qids'])
def test_invalid_window_never_builds(counter,tmp_path,change):
    role,_,_,wires,factory,plan,_,_=window_fixture(counter,tmp_path)
    if change=='bool':bad=True
    elif change=='duplicate':bad=replace(plan,calls=plan.calls+(plan.calls[0],))
    elif change in ('expensive_answer','calibration'):
        call=replace(plan.calls[0],arm='EXPENSIVE_ONLY') if change=='expensive_answer' else replace(plan.calls[0],purpose='calibration')
        bad=replace(plan,calls=(call,))
    elif change=='many_qids':bad=frozen_window(role.xing_binding,[f'q{i}' for i in range(25)])
    else:bad=replace(plan,**{dict(model='model',role='role',scope='scope',deadline='deadline_utc',hash='frozen_plan_sha256')[change]:
        {'model':'deepseek-flash','role':'chat_expensive','scope':PUBLIC_SCOPE.from_ids(['other']),
         'deadline':'2026-10-11T04:25:44Z','hash':'f'*64}[change]})
    if type(bad) is V6XingRequestWindow and change!='hash':bad=replace(bad,frozen_plan_sha256=bad.fingerprint)
    with pytest.raises(ValueError,match='V6_XING_REQUEST_WINDOW_INVALID'):
        factory.build('chat_cheap',xing_binding=replace(role.xing_binding,request_window=bad))
    assert not wires


@pytest.mark.parametrize('input_bound,output_bound', [(True,512),(0,512),(262144,512),(8192,True),(8192,513)])
def test_window_rejects_invalid_explicit_token_budgets(counter,tmp_path,input_bound,output_bound):
    role,_,_,wires,factory,plan,_,_=window_fixture(counter,tmp_path)
    bad=replace(plan,calls=(replace(plan.calls[0],input_token_bound=input_bound,output_token_bound=output_bound),))
    bad=replace(bad,frozen_plan_sha256=bad.fingerprint)
    with pytest.raises(ValueError,match='V6_XING_REQUEST_WINDOW_INVALID'):
        factory.build('chat_cheap',xing_binding=replace(role.xing_binding,request_window=bad))
    assert not wires


def test_window_actual_final_measurement_must_fit_frozen_per_call_budget(counter,tmp_path):
    role,_,ledger,wires,factory,plan,_,_=window_fixture(counter,tmp_path)
    small=replace(plan,calls=(replace(plan.calls[0],input_token_bound=1,output_token_bound=1),))
    small=replace(small,frozen_plan_sha256=small.fingerprint)
    binding=replace(role.xing_binding,request_window=small)
    gateway=factory.build('chat_cheap',xing_binding=binding)
    with pytest.raises(ProviderRequestNotSent,match='V6_XING_WINDOW_TOKEN_BOUND_EXCEEDED'):
        ordinary_invoke(GenerationRole('chat_cheap',gateway,xing_binding=binding),request_id=small.calls[0].request_id)
    assert gateway.requests==0 and not wires and not ledger.reservations


def test_window_monthly_denial_and_expired_deadline_are_zero_send(counter,tmp_path):
    role,transport,ledger,wires,_,plan,_,invoke=window_fixture(counter,tmp_path)
    # Use the actual existing monthly guard's denial, not a replaced sender.
    role.gateway.usage_guard.estimate_microunits=101
    with pytest.raises(ProviderRequestNotSent,match='MODEL_BUDGET_DENIED'):invoke(plan.calls[0])
    assert role.gateway.requests==0 and not wires
    transport.clock=lambda:datetime(2026,10,10,4,25,40,tzinfo=timezone.utc)
    with pytest.raises(ProviderRequestNotSent,match='XING_DEADLINE_OR_TIMEOUT_INVALID'):invoke(plan.calls[1])
    assert not wires and not ledger.reservations


def test_window_requires_native_run_qid_arm_receipt(counter,tmp_path):
    role,transport,ledger,wires,_,plan,path,invoke=window_fixture(counter,tmp_path)
    class WrongRun:
        def __call__(self,**kwargs):return replace(CurrentProviderSendClaims()(**kwargs),run_id='SIMULATED-other')
        def settle(self,**kwargs):CurrentProviderSendClaims().settle(**kwargs)
        def check_before_send(self,**kwargs):CurrentProviderSendClaims().check_before_send(**kwargs)
    transport.send_claim_callback=WrongRun()
    with pytest.raises(ProviderRequestNotSent,match='V6_XING_WINDOW_NATIVE_CLAIM_MISMATCH'):invoke(plan.calls[0])
    assert not wires and role.gateway.requests==1
    assert provider_send_claim_record(path,request_id=plan.calls[0].request_id)['status']=='blocked'
    assert next(iter(ledger.reservations.values()))['state']=='released'


def test_window_does_not_open_ordinary_real_hosted_admission(counter,tmp_path):
    role,transport,_,wires,_,plan,_,_=window_fixture(counter,tmp_path)
    from backend.app.adapters.models.cloud import XingAuthorizedTransport
    real=XingAuthorizedTransport(replace(transport.grant,execution_kind='REAL'),
        clock=lambda:NOW,send_claim_callback=CurrentProviderSendClaims())
    binding=replace(role.xing_binding,grant=real.grant,authorized_transport=real)
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.domain.model_registry import ModelRegistry
    gateway=ProviderFactory(ModelRegistry.frozen_defaults(),enabled_roles=('chat_cheap',),
        usage_guards={'chat_cheap':role.gateway.usage_guard}).build('chat_cheap',xing_binding=binding)
    with pytest.raises(ProviderRequestNotSent,match='XING_HOSTED_CAPACITY_AND_THINKING_UNVERIFIED'):
        ordinary_invoke(GenerationRole('chat_cheap',gateway,xing_binding=binding),request_id=plan.calls[0].request_id)
    assert not wires


def test_window_native_24_by_three_cohort_and_resume(counter,tmp_path,monkeypatch):
    """Native adapters + normal paired loop; SIMULATED providers/evidence/DB."""
    from eval_center.tests.test_public_paired_meet import native_fixture
    from eval_center.paired_state import load_frozen_cohort, sha256_file
    from eval_center.public_benchmark_cli import _parser, _run
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.ports.session_attempts import request_identity
    from uuid import uuid5, NAMESPACE_URL
    source=Path('var/reports/v6-first-evaluation-20261010/source_manifest.json')
    cohort=Path('var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl')
    rows=load_frozen_cohort(cohort)
    run='SIMULATED-window-native-cohort'
    # Freeze bounds from the counter's actual final-wire measurement in the
    # normal QuickChain/adapter path. Capture denies before reserve/claim/send.
    capture_runtime,_=native_fixture(tmp_path/'SIMULATED-plan-capture',counter)
    measured={}
    for name,container in capture_runtime.containers.items():
        gateway=container.quick_chain.generation_roles['chat_cheap'].gateway
        def capture(path,payload,timeout,validate,planned_tokens=None,*,request_policy=None,**kwargs):
            estimate=request_policy.estimate
            estimate.assert_wire_bytes(json.dumps(payload).encode('utf-8'))
            request_id=request_policy.transport._context.get()[3]
            measured[request_id]={'input_token_bound':estimate.reservation_bound,
                'output_token_bound':estimate.output_reservation,'wire_sha256':estimate.wire_request_sha256}
            raise ProviderRequestNotSent('SIMULATED_PLAN_CAPTURE_ZERO_SF_SEND')
        gateway._request=capture
    capture_args=_parser().parse_args(['--phase','paired-run','--run-id',run,'--state-path',str(tmp_path/'SIMULATED-plan-capture/center.sqlite3')])
    capture_report=_run(capture_args,runtime=capture_runtime)
    assert len(measured)==48 and capture_runtime.provider_observations()['cheap_fixture_calls']==0
    (tmp_path/'actual-wire-measurements.json').write_text(json.dumps(measured,indent=2),encoding='utf-8')
    runtime,receipts=native_fixture(tmp_path,counter)
    plans={}
    for name,container in runtime.containers.items():
        role=container.quick_chain.generation_roles['chat_cheap']
        assert role.gateway.requests==role.gateway.planned_tokens==0
        # Explicit plan is composed before any operation; never reset a used adapter.
        plan=frozen_window(role.xing_binding,[r['qid'] for r in rows if r['dataset']==name],run=run)
        # Exact existing runner/QuickChain identities, not the standalone fixture's p7 IDs.
        calls=tuple(replace(call,request_id=request_identity(
            str(uuid5(NAMESPACE_URL,f'{run}:{call.qid}:{call.arm}')),'quick.router.chat_cheap',1)) for call in plan.calls)
        calls=tuple(replace(call,input_token_bound=measured[call.request_id]['input_token_bound'],
            output_token_bound=measured[call.request_id]['output_token_bound']) for call in calls)
        plan=replace(plan,calls=calls,cohort_sha256=sha256_file(cohort),source_manifest_sha256=sha256_file(source))
        plan=replace(plan,frozen_plan_sha256=plan.fingerprint)
        binding=replace(role.xing_binding,request_window=plan)
        plans[name]={'fingerprint':plan.fingerprint,'answer_count':len(plan.calls),
            'planned_token_cap':plan.planned_token_cap,'helper_counts':{'understand':0,'rewrite':0,'expansion':0}}
        factory=ProviderFactory(ModelRegistry.frozen_defaults(),enabled_roles=('chat_cheap',),
            usage_guards={'chat_cheap':role.gateway.usage_guard})
        gateway=factory.build('chat_cheap',xing_binding=binding)
        container.quick_chain.generation_roles['chat_cheap']=replace(role,gateway=gateway,xing_binding=binding)
    args=_parser().parse_args(['--phase','paired-run','--run-id',run,'--state-path',str(tmp_path/'center.sqlite3')])
    report=_run(args,runtime=runtime)
    (tmp_path/'native-window-first.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    assert report['status']=='SIMULATED_PAIRED_COHORT_COMPLETED'
    assert report['qid_count']==24 and report['quickchain_invocations']==72
    assert report['provider_claim_counts']=={'answer/simulated':72}
    assert report['provider_calls']==report['database_queries']==0
    assert runtime.provider_observations()['cheap_fixture_calls']==48
    assert runtime.provider_observations()['deepseek_fixture_calls']==24
    assert all(c.quick_chain.generation_roles['chat_cheap'].gateway.requests==16 for c in runtime.containers.values())
    before=runtime.provider_observations()
    resumed=_run(args,runtime=runtime)
    assert resumed['results_sha256']==report['results_sha256']
    assert resumed['new_quickchain_invocations']==0 and runtime.provider_observations()==before
    (tmp_path/'native-window-result.json').write_text(json.dumps({'first':report,'resume':resumed,
        'plans':plans,'capture_report':capture_report,'observations':before,
        'execution_kind':'SIMULATED','real_provider_calls':0},indent=2),encoding='utf-8')


def test_window_final_cancel_retains_consumption_and_releases_budget(counter,tmp_path):
    role,transport,ledger,wires,_,plan,path,invoke=window_fixture(counter,tmp_path)
    class Cancelled:
        def __call__(self,**kwargs):return CurrentProviderSendClaims()(**kwargs)
        def settle(self,**kwargs):CurrentProviderSendClaims().settle(**kwargs)
        def check_before_send(self,**kwargs):raise ProviderRequestNotSent('SIMULATED_CANCELLED')
    transport.send_claim_callback=Cancelled()
    with pytest.raises(ProviderRequestNotSent):invoke(plan.calls[0])
    assert not wires and role.gateway.requests==1
    assert provider_send_claim_record(path,request_id=plan.calls[0].request_id)['status']=='blocked'
    assert next(iter(ledger.reservations.values()))['state']=='released'
    with pytest.raises(ProviderRequestNotSent,match='XING_REQUEST_ALREADY_CONSUMED'):invoke(plan.calls[0])
    assert len(ledger.reservations)==1


def meeting_fixture(tmp_path,monkeypatch):
    import scripts.verify_v6_xing_calibration_once as module
    original=json.loads((module.REPAIR_ROOT/'identity-manifest.json').read_text(encoding='utf-8'))
    meeting=dict(original,original_mapping_sha256=module.ORIGINAL_MAPPING_SHA256)
    center=tmp_path/'SIMULATED-existing-center.sqlite3'
    center.touch()
    meeting['state_path']=str(center.resolve())
    meeting['source_hashes']={name:hashlib.sha256((module.REPORT_ROOT.parents[2]/name).read_bytes()).hexdigest()
        for name in original['source_hashes']}
    path=tmp_path/'runtime-binding.json'
    path.write_text(json.dumps(meeting,indent=2),encoding='utf-8')
    monkeypatch.setattr(module,'MEETING_ROOT',tmp_path)
    monkeypatch.setattr(module,'EXISTING_V6_CENTER',center)
    return module,path,meeting


def test_runtime_meeting_explicit_pin_preserves_original_uuid_and_request(tmp_path,monkeypatch):
    module,path,meeting=meeting_fixture(tmp_path,monkeypatch)
    old=(module.REPAIR_ROOT/'identity-manifest.json').read_bytes()
    identity,record,state=module.read_runtime_meeting(hashlib.sha256(path.read_bytes()).hexdigest())
    assert identity.run_id=='b82329fe-b0ee-493c-9498-0aefdb5fc22e'
    assert identity.request_id==XING_OWNER_CALIBRATION_REQUEST_ID
    assert record==meeting and state==module.EXISTING_V6_CENTER.resolve()
    assert (module.REPAIR_ROOT/'identity-manifest.json').read_bytes()==old


@pytest.mark.parametrize('change',['pin','run','request','scope','deadline','state','source'])
def test_runtime_meeting_mismatch_never_loads_or_sends(tmp_path,monkeypatch,change):
    module,path,meeting=meeting_fixture(tmp_path,monkeypatch)
    if change=='source':meeting['source_hashes']['backend/app/adapters/models/cloud.py']='f'*64
    elif change!='pin':
        name={'run':'run_id','request':'request_id','scope':'scope','deadline':'deadline_utc','state':'state_path'}[change]
        meeting[name]+='-different'
    path.write_text(json.dumps(meeting,indent=2),encoding='utf-8')
    pin='f'*64 if change=='pin' else hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):module.read_runtime_meeting(pin)
