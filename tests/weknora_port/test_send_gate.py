"""Synthetic send/budget authorities and SQL doubles; no actual HTTP/model."""
import asyncio,json
from dataclasses import asdict,replace
from datetime import datetime,date,timedelta,timezone
from contextlib import asynccontextmanager
from pathlib import Path
import pytest

from backend.weknora_port.models.types import PreparedCall,CallContext,TrustedScope,TokenUsage,ProviderError
from backend.weknora_port.storage.send_gate import P1SendGate,ContentOrigin,SendAuthorization,MonthlyAuthority,ReservationEstimate,ActualCost
from backend.weknora_port.storage.postgres import PostgresRetriever,IsolationPolicy

MONTH=date.today().replace(day=1);SHA='c'*64
POLICY=IsolationPolicy('fixture-new',frozenset({'fixture-old'}))

def call(id='call',model='model',provider='provider',kbs=('kb',)):
    return PreparedCall(CallContext(id,TrustedScope('owner',kbs),session_id='session'),provider,model,'model-id','chat','https://fixture.invalid/chat',SHA,100,32)

def grant(prepared,**kwargs):
    values=dict(request_id=prepared.context.request_id,owner_id='owner',provider=prepared.provider,model=prepared.model,model_id=prepared.model_id,endpoint=prepared.endpoint,capability=prepared.capability,wire_sha256=prepared.wire_sha256,
      origins=(ContentOrigin('question',SHA,('kb',),True),),readable_origin_kbs=frozenset({'kb'}),cloud_allowed_origin_kbs=frozenset({'kb'}),approval_source='fixture-only-no-http',authorized=True,global_outbound_allowed=True,provenance_complete=True,max_input_tokens=100,max_output_tokens=64,expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    values.update(kwargs);return SendAuthorization(**values)

class Permissions:
    def __init__(self,transform=lambda g:g):self.transform=transform;self.calls=[]
    async def resolve(self,prepared):self.calls.append(prepared);return self.transform(grant(prepared))

class Costs:
    def __init__(self,authority=None,estimate=None,actual=None):
        self.authority=authority or MonthlyAuthority('account',MONTH,100,10,'KNOWN','fixture-explicit-carry-source','fixture-approved-monthly')
        self.quote=estimate or ReservationEstimate(30,16,'fixture-price-v1','fixture-upper-bound')
        self.actual_quote=actual or ActualCost(12,'fixture-price-v1');self.actual_calls=[]
    async def monthly(self,prepared):return self.authority
    async def estimate(self,prepared):return self.quote
    async def actual(self,prepared,usage,source):self.actual_calls.append((prepared,usage,source));return self.actual_quote

def month_row(**kwargs):
    values=dict(account_id='account',month=MONTH,budget_microunits=100,carry_in_microunits=10,carry_in_status='KNOWN',carry_in_source='fixture-explicit-carry-source',approval_source='fixture-approved-monthly',blocked_reason='');values.update(kwargs);return values

def ledger(prepared,**kwargs):
    values=dict(request_id=prepared.context.request_id,tenant_id=1,subject_id='owner',session_id='session',account_id='account',budget_month=MONTH,provider=prepared.provider,model=prepared.model,model_id=prepared.model_id,capability=prepared.capability,endpoint=prepared.endpoint,wire_sha256=prepared.wire_sha256,wire_bytes=100,output_budget=32,retrieval_kbs=list(prepared.context.scope.knowledge_base_ids),retrieval_documents=[],price_source='fixture-price-v1',reserved_microunits=30,state='reserved',outcome='UNKNOWN',usage=None);values.update(kwargs);return values

class Cursor:
    def __init__(self,rows):self.rows=rows
    async def fetchall(self):return self.rows

class SQLDouble:
    def __init__(self,answers=()):self.answers=list(answers);self.calls=[];self.tx=[]
    async def execute(self,sql,params=()):
        self.calls.append((sql,tuple(params)))
        if 'pg_control_system()' in sql:return Cursor([dict(database=POLICY.database,system_identifier='fixture-new')])
        if sql.startswith("SELECT (date_trunc"):return Cursor([dict(month=MONTH)])
        if sql.startswith('SELECT pg_advisory'):return Cursor([])
        if sql.startswith('SELECT'):return Cursor(self.answers.pop(0) if self.answers else [])
        return Cursor([])
    @asynccontextmanager
    async def transaction(self):
        self.tx.append('BEGIN')
        try:yield self
        except BaseException:self.tx.append('ROLLBACK');raise
        else:self.tx.append('COMMIT')

async def ready(db=None,permissions=None,costs=None):
    db=db or SQLDouble([[],[month_row()],[dict(used=0)]])
    guard=PostgresRetriever(db,POLICY);await guard.verify_identity()
    return P1SendGate(guard,tenant_id=1,permissions=permissions or Permissions(),costs=costs or Costs()),db

def test_default_closed_and_unverified_never_reserve():
    async def run():
        db=SQLDouble();guard=PostgresRetriever(db,POLICY);gate=P1SendGate(guard,tenant_id=1)
        with pytest.raises(PermissionError):await gate.authorize(call())
        await guard.verify_identity()
        with pytest.raises(ProviderError,match='AUTHORIZATION_REQUIRED'):await gate.authorize(call())
        assert len(db.calls)==1
    asyncio.run(run())

@pytest.mark.parametrize('transform,code',[
 (lambda g:replace(g,authorized=False),'AUTHORIZATION_REQUIRED'),
 (lambda g:replace(g,global_outbound_allowed=False),'AUTHORIZATION_REQUIRED'),
 (lambda g:replace(g,provenance_complete=False),'PROVENANCE_UNKNOWN'),
 (lambda g:replace(g,model='different'),'IDENTITY_MISMATCH'),
 (lambda g:replace(g,wire_sha256='b'*64),'IDENTITY_MISMATCH'),
 (lambda g:replace(g,origins=(ContentOrigin('memory',SHA,(),False),)),'PROVENANCE_UNKNOWN'),
 (lambda g:replace(g,origins=(ContentOrigin('history',SHA,('blocked-kb',),True),),readable_origin_kbs=frozenset({'kb','blocked-kb'})),'EGRESS_DENIED'),
 (lambda g:replace(g,max_output_tokens=16),'OUTPUT_HARD_LIMIT'),
])
def test_permission_model_wire_provenance_and_capacity_fail_closed_before_sql(transform,code):
    async def run():
        gate,db=await ready(permissions=Permissions(transform))
        with pytest.raises(ProviderError,match=code):await gate.authorize(call())
        assert len(db.calls)==1 and not db.tx
    asyncio.run(run())

@pytest.mark.parametrize('costs,code',[
 (Costs(authority=MonthlyAuthority('account',MONTH,100,None,'UNKNOWN','','approval')),'CARRY_IN_AUTHORITY'),
 (Costs(authority=MonthlyAuthority('account',MONTH,100,0,'UNKNOWN','unknown-explicit','approval')),'UNKNOWN_NOT_ZERO'),
 (Costs(estimate=ReservationEstimate(None,None,'','')),'ESTIMATE_UNAVAILABLE'),
 (Costs(estimate=ReservationEstimate(0,16,'price','estimator')),'ZERO_PRICE_AUTHORITY'),
 (Costs(estimate=ReservationEstimate(30,None,'price','estimator')),'INPUT_HARD_LIMIT'),
])
def test_unknown_price_carry_in_or_tokens_never_become_free(costs,code):
    async def run():
        gate,db=await ready(costs=costs)
        with pytest.raises(ProviderError,match=code):await gate.authorize(call())
        assert not any(sql.startswith('INSERT') for sql,_ in db.calls)
    asyncio.run(run())

def test_reserved_unknown_sum_carry_and_origin_scope_does_not_expand_retrieval():
    async def run():
        permissions=Permissions(lambda g:replace(g,origins=(*g.origins,ContentOrigin('history',SHA,('history-kb',),True)),readable_origin_kbs=frozenset({'kb','history-kb'}),cloud_allowed_origin_kbs=frozenset({'kb','history-kb'})))
        gate,db=await ready(permissions=permissions)
        await gate.authorize(call())
        sql,params=db.calls[-1];assert sql.startswith('INSERT INTO send_calls')
        assert json.loads(params[14])==['kb'] and json.loads(params[16])==['history-kb','kb']
        assert any("state IN ('reserved','unknown') THEN reserved_microunits" in sql and "state='settled' THEN actual_microunits" in sql for sql,_ in db.calls)
        gate,db=await ready(SQLDouble([[],[month_row()],[dict(used=61)]]))
        with pytest.raises(ProviderError,match='BUDGET_EXCEEDED'):await gate.authorize(call())
        assert not any(sql.startswith('INSERT INTO send_calls') for sql,_ in db.calls)
    asyncio.run(run())

def test_call_id_reuse_never_reopens_ledger_and_upgrade_rechecks_permissions():
    async def run():
        permissions=Permissions();gate,db=await ready(permissions=permissions)
        await gate.authorize(call('cheap-call'))
        permissions.transform=lambda g:replace(g,global_outbound_allowed=False)
        with pytest.raises(ProviderError):await gate.authorize(call('expensive-call','expensive-model'))
        assert len(permissions.calls)==2
        gate,db=await ready(SQLDouble([[dict(request_id='call')]]))
        with pytest.raises(ProviderError,match='REUSE_DENIED'):await gate.authorize(call())
        assert not any(sql.startswith('INSERT') for sql,_ in db.calls)
    asyncio.run(run())

@pytest.mark.parametrize('outcome,usage,state',[
 ('NOT_SENT',TokenUsage(),'released'),
 ('UNKNOWN',TokenUsage(),'unknown'),
 ('UNKNOWN',TokenUsage(1,1,2,'REPORTED'),'unknown'),
 ('RESPONSE_RECEIVED',TokenUsage(),'unknown'),
 ('RESPONSE_RECEIVED',TokenUsage(1,1,2,'REPORTED'),'settled'),
])
def test_finish_only_proven_not_sent_releases_unknown_stays_reserved(outcome,usage,state):
    async def run():
        prepared=call();gate,db=await ready(SQLDouble([[ledger(prepared)],[ledger(prepared)]]))
        await gate.finish(prepared,outcome=outcome,usage=usage)
        assert db.calls[-1][1][0]==state
        assert db.calls[-1][1][1]==(12 if state=='settled' else None)
        assert json.loads(db.calls[-1][1][3])['total_tokens']==usage.total_tokens
        if outcome=='UNKNOWN':assert not gate.costs.actual_calls
    asyncio.run(run())

def test_unknown_cannot_later_be_released_and_overrun_commits_unknown_then_blocks_month():
    async def run():
        prepared=call();current=ledger(prepared,state='unknown')
        gate,db=await ready(SQLDouble([[current],[current]]))
        with pytest.raises(ProviderError,match='CANNOT_RELEASE'):await gate.finish(prepared,outcome='NOT_SENT',usage=TokenUsage())
        gate,db=await ready(SQLDouble([[ledger(prepared)],[ledger(prepared)]]),costs=Costs(actual=ActualCost(31,'fixture-price-v1')))
        with pytest.raises(ProviderError,match='EXCEEDS_RESERVATION'):await gate.finish(prepared,outcome='RESPONSE_RECEIVED',usage=TokenUsage(1,1,2,'REPORTED'))
        assert db.tx[-1]=='COMMIT' and db.calls[-1][1][0]=='unknown'
        assert any("blocked_reason='ACTUAL_COST_EXCEEDS_RESERVATION'" in sql for sql,_ in db.calls)
    asyncio.run(run())

def test_finish_identity_and_terminal_replay_are_immutable():
    async def run():
        prepared=call();usage=TokenUsage(1,1,2,'REPORTED');row=ledger(prepared,state='settled',actual_microunits=12,outcome='RESPONSE_RECEIVED',usage=asdict(usage))
        gate,db=await ready(SQLDouble([[row],[row]]))
        await gate.finish(prepared,outcome='RESPONSE_RECEIVED',usage=usage)
        assert not any(sql.startswith('UPDATE send_calls') for sql,_ in db.calls)
        gate,db=await ready(SQLDouble([[ledger(prepared)]]))
        with pytest.raises(ProviderError,match='IDENTITY_MISMATCH'):await gate.finish(replace(prepared,wire_sha256='d'*64),outcome='NOT_SENT',usage=TokenUsage())
    asyncio.run(run())

def test_manifest_hash_and_no_legacy_runtime_import_or_free_unknown_defaults():
    import hashlib
    root=Path(__file__).resolve().parents[2]/'backend/weknora_port/storage'
    sql=(root/'migrations/005_send_gate.sql').read_text(encoding='utf-8')
    manifest=json.loads((root/'migrations/source_manifest_send_gate.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(sql.encode()).hexdigest()==manifest['generated_sha256']
    code=(root/'send_gate.py').read_text(encoding='utf-8')
    assert 'from backend.app' not in code and 'import backend.app' not in code
    assert 'actual_microunits BIGINT CHECK' in sql and 'carry_in_source' in sql


@pytest.mark.parametrize('readable,cloud,code',[
    ({'kb'},{'kb'},'MODEL_ORIGIN_KB_ACCESS_DENIED'),
    ({'kb','private'},{'kb'},'MODEL_ORIGIN_KB_EGRESS_DENIED')])
def test_mandatory_derived_origins_cannot_be_omitted_by_resolver(readable,cloud,code):
    prepared=call();prepared=replace(prepared,context=replace(prepared.context,required_origin_kbs=('private',)))
    gate=P1SendGate.__new__(P1SendGate)
    authorization=grant(prepared,readable_origin_kbs=frozenset(readable),cloud_allowed_origin_kbs=frozenset(cloud))
    with pytest.raises(ProviderError,match=code):gate._permission(prepared,authorization)
    assert prepared.context.scope.knowledge_base_ids==('kb',)

def test_mandatory_derived_origins_recorded_only_with_explicit_permission():
    prepared=call();prepared=replace(prepared,context=replace(prepared.context,required_origin_kbs=('private',)))
    gate=P1SendGate.__new__(P1SendGate)
    authorization=grant(prepared,readable_origin_kbs=frozenset({'kb','private'}),cloud_allowed_origin_kbs=frozenset({'kb','private'}))
    assert gate._permission(prepared,authorization)==('kb','private')
