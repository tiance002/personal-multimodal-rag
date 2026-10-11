"""Public QID identity contracts. Providers SIMULATED; SQLite temporary/local."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, re, subprocess, sys
import pytest
from backend.app.ports import xing_zero_reservation as identity
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.session_attempts import request_identity

ROOT = Path(__file__).resolve().parents[2]
COHORT = ROOT/'var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl'
COHORT_SHA = 'cc67e423a370cfd67437ebf70976caef140b2b9f71f5731307a528b9720bf97d'
MIRACL = ('277616#0','5704915#0','5721594#0','692002#0',
          '704170#0','7049630#0','7696#0','91294#0')
REGISTRY = ModelRegistry.frozen_defaults()


@pytest.fixture(autouse=True)
def offline_only(monkeypatch, tmp_path):
    import eval_center.paired_state as state
    from sqlalchemy.engine import Engine
    import socket
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026,10,10,6,0,0,tzinfo=timezone.utc)
            return value if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(state,'datetime',Frozen)
    monkeypatch.setattr(Engine,'connect',lambda *a,**k:pytest.fail('REAL database forbidden'))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('REAL network forbidden'))
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-ONLY')
    monkeypatch.setenv('DEEPSEEK_API_KEY','SIMULATED-ONLY')
    monkeypatch.setenv('V6_SIMULATED_RESULT_ROOT',str(tmp_path/'SIMULATED-results'))


def call_for(qid, arm='CHEAP_ONLY'):
    return identity.FreeModelPlannedCall(request_identity('SIMULATED:'+qid+':'+arm,'quick.router.chat_cheap'),
        qid,arm,'answer','SIMULATED-batch','a'*64,'b'*64,2048,512,1,30)


def permit_for(calls):
    spec=REGISTRY.select('chat_cheap')
    permit=identity.SiliconFlowFreePermit('SIMULATED-QID-GRANT','SIMULATED-QID-RUN',
        Scope.from_ids(['SIMULATED-KB'],['SIMULATED-DOC']),identity.FREE_OWNER_DEADLINE,
        '1'*64,'2'*64,spec.model_id,spec.role,identity.SiliconFlowZeroPriceContract(spec.model_id),
        tuple(calls),sum(c.input_token_bound+c.output_token_bound for c in calls),'0'*64,'SIMULATED')
    return replace(permit,frozen_plan_sha256=permit.fingerprint)


@pytest.mark.parametrize('qid',MIRACL)
def test_actual_frozen_miracl_qid_is_admitted_without_rewriting(qid):
    call=call_for(qid)
    permit=permit_for([call])
    permit.validate(REGISTRY.select('chat_cheap'))
    assert call.qid==qid and asdict(call)['qid']==qid
    assert 'qid_identity' not in asdict(call)
    assert identity.public_qid_from_identity(call.qid_identity)==qid


def test_all_24_original_frozen_qids_and_48_slots_remain_exact():
    raw=COHORT.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==COHORT_SHA
    rows=[json.loads(line) for line in raw.splitlines()]
    assert tuple(r['qid'] for r in rows if r['dataset']=='miracl-zh')==MIRACL
    calls=tuple(call_for(r['qid'],arm) for r in rows for arm in ('CHEAP_ONLY','DYNAMIC'))
    permit_for(calls).validate(REGISTRY.select('chat_cheap'))
    assert len(calls)==len({c.request_id for c in calls})==48
    assert len({c.qid_identity for c in calls})==24
    assert {c.qid for c in calls}=={r['qid'] for r in rows}


def test_reversible_identity_never_collapses_aliases_or_leading_zeroes():
    qids=['277616#0','277616:0','277616-0','277616_0','0277616#0','277616#00']
    qids.append(identity.public_qid_identity(qids[0]))
    keys=[identity.public_qid_identity(q) for q in qids]
    assert len(set(keys))==len(qids)
    assert [identity.public_qid_from_identity(k) for k in keys]==qids
    assert all(re.fullmatch('[A-Za-z0-9_-]+',k) for k in keys)


@pytest.mark.parametrize('qid',['../','../../etc/passwd','C:/Windows','a/b:c.d'])
def test_legacy_opaque_ids_cannot_become_encoded_paths(qid):
    key=identity.public_qid_identity(qid)
    assert '/' not in key and '\\' not in key and '..' not in key and ':' not in key
    assert identity.public_qid_from_identity(key)==qid


@pytest.mark.parametrize('qid',[None,True,1,'',' ','277616#','x#0','#0','277616#-1',
    '277616#0#1','277616#../x','../277616#0','277616#0/../x','277616%230',
    '277616#0?x=1','277616#0\x00','277616#0\n','277616#0\t','２７７#0',
    "277616#0'); DROP TABLE paired_runs;--",'a'*161])
def test_malicious_or_undeclared_qid_shapes_stay_rejected(qid):
    with pytest.raises(ValueError,match='PUBLIC_QID_INVALID'):
        identity.public_qid_identity(qid)
    with pytest.raises(ValueError,match='SF_FREE_PERMIT_INVALID'):
        permit_for([replace(call_for('safe'),qid=qid)]).validate(REGISTRY.select('chat_cheap'))


def test_exact_type_and_length_boundary():
    class Alias(str): pass
    with pytest.raises(ValueError,match='PUBLIC_QID_INVALID'):
        identity.public_qid_identity(Alias('277616#0'))
    for qid in ['a'*160,'0'*79+'#'+'1'*80]:
        assert identity.public_qid_from_identity(identity.public_qid_identity(qid))==qid
    with pytest.raises(ValueError,match='PUBLIC_QID_INVALID'):
        identity.public_qid_identity('0'*80+'#'+'1'*80)


@pytest.mark.parametrize('suffix',['=','/','\\','%23','\n'])
def test_noncanonical_internal_aliases_are_rejected(suffix):
    with pytest.raises(ValueError,match='PUBLIC_QID_IDENTITY_INVALID'):
        identity.public_qid_from_identity(identity.public_qid_identity('277616#0')+suffix)


def test_unused_base64_bits_cannot_create_a_second_spelling():
    # YQ and YR decode to the same byte if a decoder ignores unused pad bits.
    assert identity.public_qid_identity('a')=='rag-public-qid-v1-YQ'
    with pytest.raises(ValueError,match='PUBLIC_QID_IDENTITY_INVALID'):
        identity.public_qid_from_identity('rag-public-qid-v1-YR')


@pytest.mark.parametrize('field',['request_id','batch_id'])
def test_public_qid_compatibility_does_not_relax_other_identifiers(field):
    call=replace(call_for('277616#0'),**{field:'277616#0'})
    with pytest.raises(ValueError,match='SF_FREE_PERMIT_INVALID'):
        permit_for([call]).validate(REGISTRY.select('chat_cheap'))


def test_original_qid_claim_and_fingerprint_cannot_be_rebound_to_alias():
    from types import SimpleNamespace
    call=call_for('277616#0');permit=permit_for([call])
    claim=SimpleNamespace(run_id=permit.run_id,qid=call.qid,arm=call.arm,
        request_id=call.request_id,purpose=call.purpose,call_ordinal=call.call_ordinal)
    permit.validate_claim(call,claim)
    for qid in ['277616:0',call.qid_identity]:
        claim.qid=qid
        with pytest.raises(ValueError,match='SF_FREE_NATIVE_CLAIM_MISMATCH'):
            permit.validate_claim(call,claim)
        assert replace(permit,calls=(replace(call,qid=qid),)).fingerprint!=permit.fingerprint


def test_duplicate_original_slot_stays_rejected_after_encoding():
    call=call_for('277616#0')
    duplicate=replace(call,request_id=request_identity('SIMULATED-second','quick.router.chat_cheap'))
    with pytest.raises(ValueError,match='SF_FREE_PERMIT_INVALID'):
        permit_for([call,duplicate]).validate(REGISTRY.select('chat_cheap'))


def test_identity_is_exactly_stable_across_fresh_processes():
    code=('import json; from backend.app.ports.xing_zero_reservation import public_qid_identity; '
          'print(json.dumps([public_qid_identity(q) for q in '+repr(list(MIRACL))+']))')
    runs=[subprocess.run([sys.executable,'-B','-X','utf8','-c',code],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',check=True).stdout for _ in range(2)]
    assert runs[0]==runs[1]
    assert json.loads(runs[0])==[identity.public_qid_identity(q) for q in MIRACL]


@pytest.mark.parametrize('failure',[None,TimeoutError('SIMULATED unknown')])
def test_normal_provider_native_claim_and_ledger_keep_original_qid(monkeypatch,tmp_path,failure):
    from backend.tests.test_v6_free_model_mainpath import embedding_setup, permit_for as embed_permit, context_for, SCOPE
    from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.ports.model_access import model_access
    from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
    from eval_center.paired_state import (ProviderBatchCall,ProviderBatchPlan,SQLiteProviderBatchSendClaims,
        register_run,begin_provider_batch_scope,provider_send_claim_record,stable_provider_request_id)
    adapter,old,_,gate,wires,payloads,_=embedding_setup(monkeypatch,failure=failure)
    qid=MIRACL[0];payload=payloads[0]
    call=replace(old.calls[0],qid=qid,arm='SHARED_RETRIEVAL',
        request_id=stable_provider_request_id(run_id=old.run_id,qid=qid,arm='SHARED_RETRIEVAL',purpose='query_embedding'),
        input_sha256=(hashlib.sha256(payload['input'][0].encode()).hexdigest(),),
        query_sha256=hashlib.sha256(payload['input'][0].encode()).hexdigest())
    permit=embed_permit('embedding',[call]);permit.validate(REGISTRY.select('embedding'))
    native=ProviderBatchCall(1,(),(),call.input_sha256,call.wire_sha256,identity.free_dispatch_identity_sha256(permit,call))
    plan=ProviderBatchPlan('query_embedding','siliconflow',permit.model,adapter.identity.fingerprint,
        hashlib.sha256(adapter.identity.chunking_index_identity.encode()).hexdigest(),
        identity.free_dispatch_contract(permit,call)['scope_sha256'],(native,),call.query_sha256)
    path=tmp_path/'SIMULATED-QID.sqlite3'
    register_run(path,run_id=permit.run_id,cohort_sha256='1'*64,source_manifest_sha256='2'*64,
        code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',deadline_utc=permit.deadline_utc,
        expected_qids=[qid],execution_purpose='RETRIEVAL')
    arm=begin_provider_batch_scope(path,run_id=permit.run_id,qid=qid,input_identity_sha256='3'*64)
    def callback():
        return SQLiteProviderBatchSendClaims(path,permit.run_id,qid,arm,'3'*64,plan,'4'*64,lambda lease:None)
    original=adapter.transport
    transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=callback(),sender=original.sender,clock=original.clock)
    binding=identity.SiliconFlowFreeBinding(permit,transport,adapter.admission,adapter.free_binding.result_storage)
    guard=BudgetUsageGuard(gate,adapter.spec,0,zero_price_permit=permit)
    gateway=ProviderFactory(REGISTRY,enabled_roles=['embedding'],usage_guards={'embedding':guard},
        free_model_bindings={'embedding':binding}).build('embedding')
    context=replace(context_for(permit,call),batch_plan=plan)
    with identity.free_model_requests((context,)),model_access('embedding',allowed=True,scope=SCOPE):
        if failure:
            with pytest.raises(ProviderUnavailable):gateway.embed(payload['input'],10)
        else:
            assert len(gateway.embed(payload['input'],10).vectors)==1
    row=provider_send_claim_record(path,request_id=call.request_id)
    assert row['qid']==qid and row['request_id']==call.request_id
    assert row['status']==('unknown' if failure else 'simulated') and len(wires)==1
    transport.send_claim_callback=callback()  # New object, same durable native state.
    with identity.free_model_requests((context,)),model_access('embedding',allowed=True,scope=SCOPE):
        with pytest.raises(ProviderRequestNotSent):gateway.embed(payload['input'],10)
    assert len(wires)==1 and provider_send_claim_record(path,request_id=call.request_id)['qid']==qid
