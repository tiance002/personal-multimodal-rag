"""SIMULATED provider/SQL, REAL OFFLINE pinned counter; no live permissions."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib,json
import pytest

from backend.tests.test_v6_xing_generation_binding import counter, no_live_sender, NOW, response
from backend.tests.test_v6_xing_calibration_once import setup
from backend.app.adapters.models.cloud import (
    XingCalibrationIdentity, XING_OWNER_CALIBRATION_REQUEST_ID, XING_CALIBRATION_WIRE_SHA256,
    XingZeroPriceContract, XingCalibrationPermit, XingCohortCalibrationClaimStore,
)
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.budget import InMemoryBudgetGate, PostgresBudgetGate, BudgetDenied
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.xing_zero_reservation import XingCalibrationZeroReservation, OWNER_CALIBRATION_RUN_ID
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from backend.app.ports.model_access import model_access
from eval_center.paired_state import provider_send_claim_scope, provider_send_claim_record
from scripts.verify_v6_xing_calibration_once import invoke_simulated, configuration_admission


def zero_fixture(counter,tmp_path,monkeypatch,**kwargs):
    identity=XingCalibrationIdentity(OWNER_CALIBRATION_RUN_ID,XING_OWNER_CALIBRATION_REQUEST_ID)
    gateway,transport,ledger,wires,bound,path,build=setup(counter,tmp_path,monkeypatch,identity=identity,budget=0,**kwargs)
    def attach(gateway):
        gateway.usage_guard=BudgetUsageGuard(ledger,gateway.spec,0,zero_price_permit=gateway.transport.calibration_permit)
        return gateway
    return attach(gateway),transport,ledger,wires,bound,path,lambda:attach(build()[0])


@pytest.mark.parametrize('failure',[None,'timeout','missing_usage','wrong_model'])
def test_zero_normal_guard_claim_unknown_no_retry(counter,tmp_path,monkeypatch,failure):
    result=({'timeout':TimeoutError('SIMULATED'),'missing_usage':response(usage=None),
             'wrong_model':response(model='Pro/XingChenAGI/Xing4.0-29B')}.get(failure))
    gateway,transport,ledger,wires,bound,path,build=zero_fixture(counter,tmp_path,monkeypatch,result=result)
    with provider_send_claim_scope(bound):
        if failure:
            with pytest.raises(ProviderUnavailable):invoke_simulated(gateway)
        else:invoke_simulated(gateway)
    assert len(wires)==1 and hashlib.sha256(wires[0]).hexdigest()==XING_CALIBRATION_WIRE_SHA256
    row=next(iter(ledger.reservations.values()))
    assert row['reserved_microunits']==0 and row['settled_microunits'] is None and row['state']=='unknown'
    assert ledger.monthly_budget_microunits==0 and ledger._used(row['month'])==0
    record=provider_send_claim_record(path,request_id=XING_OWNER_CALIBRATION_REQUEST_ID)
    assert record['run_id']==OWNER_CALIBRATION_RUN_ID
    assert record['result']['budget_reservation_id'] in ledger.reservations
    restored=build()
    with provider_send_claim_scope(bound),pytest.raises(ProviderRequestNotSent):invoke_simulated(restored)
    assert len(wires)==1 and provider_send_claim_record(path,request_id=XING_OWNER_CALIBRATION_REQUEST_ID)==record
    assert [r['state'] for r in ledger.reservations.values()]==['unknown','released']


def test_zero_not_sent_releases_only_own_reservation(counter,tmp_path,monkeypatch):
    gateway,_,ledger,wires,bound,path,_=zero_fixture(counter,tmp_path,monkeypatch,
        result=ProviderRequestNotSent('SIMULATED_PROVEN_NOT_SENT'))
    with provider_send_claim_scope(bound),pytest.raises(ProviderRequestNotSent):invoke_simulated(gateway)
    assert len(wires)==1 # local fixture observation, no provider wire
    assert next(iter(ledger.reservations.values()))['state']=='released'
    assert gateway.receipts[-1]['settlement']=='NOT_SENT'


@pytest.mark.parametrize('condition',['request','scope','wire','expired'])
def test_zero_preflight_denials_have_no_reservation(counter,tmp_path,monkeypatch,condition):
    gateway,transport,ledger,wires,bound,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    scope=transport.grant.scope;request_id=XING_OWNER_CALIBRATION_REQUEST_ID;prompt='Reply exactly OK.'
    if condition=='request':request_id+='-retry'
    if condition=='scope':scope='other'
    if condition=='wire':prompt+='changed'
    if condition=='expired':transport.clock=lambda:datetime(2026,10,10,4,25,40,tzinfo=timezone.utc)
    with provider_send_claim_scope(bound),model_access('chat_cheap',allowed=True),pytest.raises(ProviderRequestNotSent):
        gateway.generate_with_xing_scope([{'role':'user','content':prompt}],timeout_seconds=10,max_tokens=32,
            scope=scope,purpose='calibration',request_id=request_id)
    assert not wires and not ledger.reservations


@pytest.mark.parametrize('value',[0,False,True,-1,0.0])
def test_bare_zero_bool_negative_stays_denied(value):
    spec=ModelRegistry.frozen_defaults().select('chat_cheap')
    with pytest.raises(ValueError):BudgetUsageGuard(InMemoryBudgetGate(0),spec,value)
    with pytest.raises(ValueError):InMemoryBudgetGate(0).reserve(run_id=None,provider=spec.provider,
        model_name=spec.model_id,capability=spec.role,estimate_microunits=value)


@pytest.mark.parametrize('condition',['bool_permit','wrong_price','wrong_run','other_provider'])
def test_zero_constructor_not_general_allow(counter,tmp_path,monkeypatch,condition):
    gateway,transport,_,_,_,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    permit=transport.calibration_permit;spec=gateway.spec
    if condition=='bool_permit':permit=True
    if condition=='wrong_price':permit=replace(permit,price_contract=replace(permit.price_contract,source_sha256='0'*64))
    if condition=='wrong_run':
        identity=XingCalibrationIdentity('d95a0082-20a2-43e8-97bc-881f6b83343e',XING_OWNER_CALIBRATION_REQUEST_ID)
        permit=replace(permit,claim_store=XingCohortCalibrationClaimStore(permit.claim_store.callback,identity=identity))
    if condition=='other_provider':spec=ModelRegistry.frozen_defaults().select('chat_expensive')
    with pytest.raises((ValueError,ProviderRequestNotSent)):BudgetUsageGuard(InMemoryBudgetGate(0),spec,0,zero_price_permit=permit)


def authority_for(gateway,counter):
    body=dict(model=gateway.spec.model_id,messages=[{'role':'user','content':'Reply exactly OK.'}],
              max_tokens=32,stream=False,enable_thinking=False)
    wire=json.dumps(body).encode()
    estimate=counter.measure('siliconflow',body['model'],body,scope=gateway.transport.grant.scope,wire_bytes=wire)
    return XingCalibrationZeroReservation(gateway.transport.calibration_permit,gateway.transport,estimate,wire)


@pytest.mark.parametrize('condition',['Pro','scope','wire','forged_wire','run','no_context'])
def test_zero_budget_gate_independently_revalidates(counter,tmp_path,monkeypatch,condition):
    gateway,transport,ledger,_,_,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    authority=authority_for(gateway,counter)
    args=dict(run_id=None,provider='siliconflow',model_name=gateway.spec.model_id,capability='chat_cheap',
              estimate_microunits=0,zero_price_authority=authority)
    if condition=='Pro':args['model_name']='Pro/'+gateway.spec.model_id
    if condition=='run':args['run_id']='different-run'
    if condition=='wire':args['zero_price_authority']=replace(authority,wire_bytes=authority.wire_bytes+b' ')
    if condition=='forged_wire':
        forged=authority.wire_bytes+b' '
        args['zero_price_authority']=replace(authority,wire_bytes=forged,
            estimate=replace(authority.estimate,wire_bytes=forged))
    if condition=='scope':args['zero_price_authority']=replace(authority,estimate=replace(authority.estimate,scope_sha256='0'*64))
    if condition=='no_context':
        with pytest.raises((ValueError,ProviderRequestNotSent)):ledger.reserve(**args)
    else:
        with transport.authorize(authority.estimate,scope=transport.grant.scope,purpose='calibration',
                request_id=XING_OWNER_CALIBRATION_REQUEST_ID,timeout_seconds=10),pytest.raises((ValueError,ProviderRequestNotSent)):
            ledger.reserve(**args)
    assert not ledger.reservations


@pytest.mark.parametrize('planned',[None,False,True,0,1])
def test_zero_usage_guard_requires_actual_input_output_budget(counter,tmp_path,monkeypatch,planned):
    gateway,transport,ledger,_,_,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    authority=authority_for(gateway,counter)
    with transport.authorize(authority.estimate,scope=transport.grant.scope,purpose='calibration',
            request_id=XING_OWNER_CALIBRATION_REQUEST_ID,timeout_seconds=10),pytest.raises(ValueError):
        gateway.usage_guard.reserve(model_key=gateway.spec.model_key,role=gateway.spec.role,
            model_id=gateway.spec.model_id,planned_tokens=planned,zero_price_authority=authority)
    assert not ledger.reservations


def test_zero_does_not_offset_historical_unknown(counter,tmp_path,monkeypatch):
    gateway,transport,ledger,_,bound,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    ledger.monthly_budget_microunits=7
    old=ledger.reserve(run_id=None,provider='SIMULATED-history',model_name='old',capability='chat',estimate_microunits=7)
    ledger.mark_unknown(old.reservation_id);before=ledger.reservations[old.reservation_id].copy()
    with provider_send_claim_scope(bound):invoke_simulated(gateway)
    assert ledger.reservations[old.reservation_id]==before and ledger._used(old.month)==7
    with pytest.raises(BudgetDenied):ledger.reserve(run_id=None,provider='cloud',model_name='paid',capability='chat',estimate_microunits=1)


def test_zero_postgres_atomic_insert_and_null_settlement_simulated_sql(counter,tmp_path,monkeypatch):
    gateway,transport,_,_,_,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    authority=authority_for(gateway,counter);calls=[]
    class Result:
        def scalar_one(self):return 0
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,params):calls.append((str(sql),params));return Result()
    class Engine:
        def begin(self):return Connection()
    gate=PostgresBudgetGate(Engine(),0)
    with transport.authorize(authority.estimate,scope=transport.grant.scope,purpose='calibration',
            request_id=XING_OWNER_CALIBRATION_REQUEST_ID,timeout_seconds=10):
        reservation=gate.reserve(run_id=None,provider='siliconflow',model_name=gateway.spec.model_id,
            capability='chat_cheap',estimate_microunits=0,zero_price_authority=authority)
    assert reservation.reservation_id and reservation.reserved_microunits==0
    assert 'pg_advisory_xact_lock' in calls[0][0] and 'SUM(CASE' in calls[1][0]
    assert 'INSERT INTO model_calls' in calls[2][0] and "NULL,'reserved'" in calls[2][0]
    assert calls[2][1]['reserved']==0
    gate.mark_unknown(reservation.reservation_id)
    assert "reservation_state='unknown'" in calls[-1][0] and 'settled_cost_microunits=' not in calls[-1][0]


def test_configuration_zero_requires_typed_fixed_owner_permit(counter,tmp_path,monkeypatch):
    gateway,transport,_,_,_,_,_=zero_fixture(counter,tmp_path,monkeypatch)
    from backend.app.config import Settings
    state=tmp_path/'existing-center';state.touch()
    settings=replace(Settings(),cloud_enabled=True,chat_egress_enabled=True,
        database_url='postgresql+psycopg://rag@127.0.0.1:25438/rag_clean_dev_20261008t072656z_352f705b')
    monkeypatch.setattr('scripts.verify_v6_xing_calibration_once.admit_identity',lambda identity,**kwargs:None)
    missing,_=configuration_admission(settings,key_present=True,state_path=state,zero_price_permit=transport.calibration_permit)
    assert not missing
    for permit in (None,True,replace(transport.calibration_permit,price_contract=replace(XingZeroPriceContract(),source_sha256='0'*64))):
        missing,_=configuration_admission(settings,key_present=True,state_path=state,zero_price_permit=permit)
        assert 'MONTHLY_BUDGET_ZERO' in missing and 'POSITIVE_MONTHLY_RESERVATION_NOT_CONFIGURED' in missing


@pytest.mark.parametrize('url',[
    'postgresql+psycopg://rag_eval@127.0.0.1:25436/rag_eval_trust_0ccc68d6f380_a',
    'postgresql+psycopg://rag_eval@127.0.0.1:25436/rag_eval_trust_0ccc68d6f380_b',
    'postgresql+psycopg://rag@127.0.0.1:5432/personal_rag',
    'postgresql+psycopg://rag@remote:25438/rag_clean_dev_20261008t072656z_352f705b',
])
def test_calibration_ledger_rejects_arbitrary_ab_business_or_remote(url):
    from scripts.verify_v6_xing_calibration_once import calibration_ledger_identity
    with pytest.raises(ValueError,match='AUTHORITATIVE_CALIBRATION_LEDGER_MISMATCH'):
        calibration_ledger_identity(url)


def test_zero_never_erases_existing_over_cap_unknown(counter,tmp_path,monkeypatch):
    gateway,_,ledger,wires,bound,path,_=zero_fixture(counter,tmp_path,monkeypatch)
    ledger.monthly_budget_microunits=10
    old=ledger.reserve(run_id=None,provider='SIMULATED-history',model_name='old',capability='embedding',estimate_microunits=10)
    ledger.mark_unknown(old.reservation_id);before=ledger.reservations.copy()
    ledger.monthly_budget_microunits=0
    with provider_send_claim_scope(bound),pytest.raises(ProviderRequestNotSent,match='MODEL_BUDGET_DENIED'):
        invoke_simulated(gateway)
    assert ledger.reservations==before and ledger._used(old.month)==10 and not wires
    assert provider_send_claim_record(path,request_id=XING_OWNER_CALIBRATION_REQUEST_ID) is None
