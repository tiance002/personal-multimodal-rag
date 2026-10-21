"""Scripted SQL boundary for admission; no model/provider or PG execution."""
import asyncio,json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from dataclasses import replace
import pytest

from backend.weknora_port.service import OrdinaryTurnRequest
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.storage.turns import PostgresTurnRepository,ScopeSnapshot
from backend.weknora_port.storage.local_principal import LocalPrincipalStore
from backend.weknora_port.storage.postgres import PostgresRetriever,IsolationPolicy
from backend.weknora_port.conversation.types import MemoryContext
from backend.weknora_port.models.types import TrustedScope

PRINCIPAL=LocalPrincipal(7,'local-owner');POLICY=IsolationPolicy('new',frozenset({'old'}))

class Cursor:
    def __init__(self,rows):self.rows=rows
    async def fetchall(self):return self.rows

class DB:
    def __init__(self,answers=(),deleted=False,fail=''):
        self.answers=list(answers);self.deleted=deleted;self.fail=fail;self.calls=[];self.tx=[]
    async def execute(self,sql,params=()):
        self.calls.append((sql,tuple(params)))
        if self.fail and self.fail in sql:raise RuntimeError('SYNTHETIC_FAILURE')
        if 'pg_control_system()' in sql:return Cursor([dict(database=POLICY.database,system_identifier='new')])
        if sql.startswith('SELECT pg_advisory'):return Cursor([])
        if sql.startswith('SELECT * FROM sessions'):return Cursor([] if self.deleted else [dict(id='session',knowledge_base_scope=['kb'],knowledge_base_id='kb',document_scope=[])])
        if sql.startswith('SELECT id FROM knowledge_bases'):return Cursor([dict(id='kb')])
        if sql.startswith('SELECT') or ' RETURNING ' in sql:return Cursor(self.answers.pop(0) if self.answers else [])
        return Cursor([])
    @asynccontextmanager
    async def transaction(self):
        self.tx.append('BEGIN')
        try:yield self
        except BaseException:self.tx.append('ROLLBACK');raise
        else:self.tx.append('COMMIT')

class Leases:
    def __init__(self,db):self.db=db;self.count=0
    @asynccontextmanager
    async def lease(self):
        self.count+=1;guard=PostgresRetriever(self.db,POLICY);await guard.verify_identity()
        try:yield guard
        finally:guard._verified=False

def stored(q0='private question'):
    return dict(session_id='session',q0=q0,client_request_id='client',run_id='run',user_message_id='user',assistant_message_id='assistant',knowledge_base_scope=['kb'],document_scope=[],payload_sha256='',status='pending',error_code='')

def test_new_admission_writes_both_messages_and_claim_atomically_before_model():
    async def run():
        db=DB([[],[]]);leases=Leases(db);repo=PostgresTurnRepository(leases,PRINCIPAL)
        accepted=await repo.accept(OrdinaryTurnRequest('session','private question','client'),PRINCIPAL)
        assert accepted.is_new and not accepted.replay_only and accepted.run_id==accepted.request.request_id
        assert accepted.memory_context.tenant_id==7 and accepted.context.scope.owner_id==PRINCIPAL.subject_id
        assert accepted.context.scope.knowledge_base_ids==('kb',) and leases.count==1
        assert sum(sql.startswith('INSERT INTO messages') for sql,_ in db.calls)==2
        assert db.calls[-1][0].startswith('INSERT INTO port_turns') and db.tx==['BEGIN','COMMIT']
        assert 'private question' not in repr(accepted) and 'q0' not in ScopeSnapshot('private question',('kb',),()).public()
    asyncio.run(run())

def test_repeat_replays_stable_ids_without_insert_and_payload_changes_fail():
    async def run():
        row=stored();db=DB();repo=PostgresTurnRepository(Leases(db),PRINCIPAL);request=OrdinaryTurnRequest('session','private question','client')
        row['payload_sha256']=repo._payload(request,ScopeSnapshot('private question',('kb',),()))
        db.answers=[[row],[{'id':'user','role':'user'},{'id':'assistant','role':'assistant'}]]
        accepted=await repo.accept(request,PRINCIPAL)
        assert not accepted.is_new and accepted.replay_only and accepted.assistant_message_id=='assistant'
        assert not any(sql.startswith('INSERT') for sql,_ in db.calls)
        db.answers=[[row]]
        with pytest.raises(ProviderError,match='PAYLOAD_CONFLICT'):await repo.accept(replace(request,content='different private'),PRINCIPAL)
    asyncio.run(run())

@pytest.mark.parametrize('turn_input',[
    OrdinaryTurnRequest('session','q','x'*37),
    OrdinaryTurnRequest('session','q','client',knowledge_base_scope=('foreign',)),
    OrdinaryTurnRequest('session','\x00','client'),
])
def test_invalid_primitive_or_scope_does_not_create_messages(turn_input):
    async def run():
        db=DB();repo=PostgresTurnRepository(Leases(db),PRINCIPAL)
        with pytest.raises(ProviderError):await repo.accept(turn_input,PRINCIPAL)
        assert not any(sql.startswith('INSERT') for sql,_ in db.calls)
    asyncio.run(run())

def test_deleted_session_foreign_principal_and_smart_are_denied():
    async def run():
        db=DB(deleted=True);repo=PostgresTurnRepository(Leases(db),PRINCIPAL)
        with pytest.raises(ProviderError,match='SESSION_UNAVAILABLE'):await repo.accept(OrdinaryTurnRequest('session','q','client'),PRINCIPAL)
        with pytest.raises(ProviderError,match='OWNER_MISMATCH'):await repo.accept(OrdinaryTurnRequest('session','q','client'),LocalPrincipal(7,'other'))
        with pytest.raises(ProviderError,match='ORDINARY_MODE_REQUIRED'):OrdinaryTurnRequest('session','q','client',mode='smart')
        assert not any(sql.startswith('INSERT') for sql,_ in db.calls)
    asyncio.run(run())

def test_insert_failure_rolls_back_messages_and_claim():
    async def run():
        db=DB([[],[]],fail='INSERT INTO port_turns');repo=PostgresTurnRepository(Leases(db),PRINCIPAL)
        with pytest.raises(RuntimeError):await repo.accept(OrdinaryTurnRequest('session','q','client'),PRINCIPAL)
        assert db.tx==['BEGIN','ROLLBACK']
    asyncio.run(run())

def test_failure_updates_only_assistant_and_no_budget_unknown_release():
    async def run():
        row=stored();db=DB([[row],[dict(is_completed=False)]]);repo=PostgresTurnRepository(Leases(db),PRINCIPAL);accepted=repo._accepted(row,new=True)
        assert await repo.commit_failure(accepted,'MODEL_CALL_TIMEOUT') is True
        update=next(sql for sql,_ in db.calls if sql.startswith('UPDATE messages'))
        assert "role='assistant' AND is_completed=false" in update
        assert 'content=' not in update and not any('send_calls' in sql or 'settle' in sql for sql,_ in db.calls)
        db.answers=[[row],[dict(is_completed=True)]]
        before=len(db.calls);assert await repo.commit_failure(accepted,'MODEL_CALL_TIMEOUT') is False
        assert not any(sql.startswith('UPDATE') for sql,_ in db.calls[before:])
    asyncio.run(run())

def test_validate_run_returns_replay_and_filters_owned_active_messages():
    async def run():
        row=stored();db=DB([[row],[{'id':'user','role':'user'},{'id':'assistant','role':'assistant'}]])
        repo=PostgresTurnRepository(Leases(db),PRINCIPAL)
        accepted=await repo.validate_run('session','assistant',PRINCIPAL)
        assert accepted.replay_only and not accepted.is_new
        assert any('deleted_at IS NULL' in sql and "id=ANY(%s)" in sql for sql,_ in db.calls)
    asyncio.run(run())

def test_principal_bootstrap_returns_existing_singleton_never_picks_other_tenant():
    async def run():
        db=DB([[dict(tenant_id=7,subject_id='sole-owner')]]);store=LocalPrincipalStore(Leases(db))
        principal=await store.bootstrap();assert principal==LocalPrincipal(7,'sole-owner')
        assert not any(sql.startswith('INSERT INTO tenants') for sql,_ in db.calls)
        assert any("p.slot='local' AND t.deleted_at IS NULL" in sql for sql,_ in db.calls)
    asyncio.run(run())


@pytest.mark.parametrize('method',['failure','cancelled'])
@pytest.mark.parametrize('memory_allowed',[True,False])
def test_s85_terminal_commit_uses_principal_identity_not_memory_policy(method,memory_allowed):
    async def run():
        row=stored(); db=DB([[row],[dict(is_completed=False,agent_steps=[])]])
        repo=PostgresTurnRepository(Leases(db),PRINCIPAL)
        accepted=repo._accepted(row,new=True)
        accepted=replace(accepted,memory_context=replace(accepted.memory_context,agent_memory_allowed=memory_allowed))
        if method=='failure':
            assert await repo.commit_failure(accepted,'S85C_OUTPUT_CONTRACT_STOP') is True
            updates=[(sql,args) for sql,args in db.calls if sql.startswith('UPDATE')]
            assert any('S85C_OUTPUT_CONTRACT_STOP' in args for _,args in updates)
        else:
            assert await repo.commit_cancelled(accepted,'partial','',[]) is True
        assert accepted.memory_context.agent_memory_allowed is memory_allowed
        assert db.tx==['BEGIN','COMMIT']
        assert all(PRINCIPAL.subject_id in args for sql,args in db.calls if sql.startswith('UPDATE'))
    asyncio.run(run())


@pytest.mark.parametrize('method',['failure','cancelled'])
@pytest.mark.parametrize('change',['tenant','subject','tenant_string','tenant_bool','scope_owner','untyped_memory'])
def test_s85_terminal_commit_still_denies_foreign_or_untyped_principal_before_sql(method,change):
    async def run():
        db=DB(); repo=PostgresTurnRepository(Leases(db),PRINCIPAL)
        accepted=repo._accepted(stored(),new=True)
        replacements={'tenant':MemoryContext(8,PRINCIPAL.subject_id),
            'subject':MemoryContext(7,'other'), 'tenant_string':MemoryContext('7',PRINCIPAL.subject_id),
            'tenant_bool':MemoryContext(True,PRINCIPAL.subject_id),
            'untyped_memory':SimpleNamespace(tenant_id=7,principal_storage_id=PRINCIPAL.subject_id,agent_memory_allowed=True)}
        if change=='scope_owner':
            accepted=replace(accepted,context=replace(accepted.context,scope=TrustedScope('other',('kb',))))
        else: accepted=replace(accepted,memory_context=replacements[change])
        with pytest.raises(ProviderError,match='TURN_OWNER_MISMATCH'):
            if method=='failure': await repo.commit_failure(accepted,'S85C_OUTPUT_CONTRACT_STOP')
            else: await repo.commit_cancelled(accepted,'partial','',[])
        assert db.calls==[]
    asyncio.run(run())
