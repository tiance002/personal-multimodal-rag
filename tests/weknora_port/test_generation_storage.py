"""Scripted SQL boundary only: no PostgreSQL/provider/budget execution."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from backend.weknora_port.generation.types import AttemptRecord, GenerationError, GenerationReplay
from backend.weknora_port.models.types import CallContext, TokenUsage, TrustedScope
from backend.weknora_port.storage.generation import PostgresGenerationLifecycle
from backend.weknora_port.storage.postgres import IsolationPolicy, PostgresRetriever

SHA = 'a' * 64
POLICY = IsolationPolicy('new-system',frozenset({'old-system'}))
CONTEXT = CallContext('run',TrustedScope('owner',('kb',),('doc',)),session_id='session')


def record(ordinal=1,role='cheap',**kwargs):
    return AttemptRecord(f'run/answer/{ordinal}/{role}',ordinal,role,'provider','requested-model','https://fixture.invalid/api',SHA,SHA,SHA,**kwargs)


def persisted(record):
    values = asdict(record); values['objective'] = list(record.objective)
    return values


def run_row(**kwargs):
    return dict(run_id='run',tenant_id=7,subject_id='owner',session_id='session',message_id='assistant',payload_sha256=SHA,knowledge_base_ids=['kb'],document_ids=['doc'],**kwargs)


class Cursor:
    def __init__(self, rows): self.rows=rows
    async def fetchall(self): return self.rows


class FixtureConnection:
    def __init__(self,answers=(), *, system='new-system',session=True,role='assistant',completed=False,fail=''):
        self.answers=list(answers);self.system=system;self.session=session;self.role=role;self.completed=completed;self.fail=fail
        self.calls=[];self.transactions=[]
    async def execute(self,sql,params=()):
        self.calls.append((sql,tuple(params)))
        if self.fail and self.fail in sql: raise RuntimeError('SYNTHETIC_SQL_FAILURE')
        if 'pg_control_system()' in sql: return Cursor([dict(database=POLICY.database,system_identifier=self.system)])
        if sql.startswith('SELECT id FROM sessions'): return Cursor([{'id':'session'}] if self.session else [])
        if sql.startswith('SELECT id,role,is_completed FROM messages'): return Cursor([dict(id='assistant',role=self.role,is_completed=self.completed)])
        if sql.startswith('SELECT') or ' RETURNING ' in sql: return Cursor(self.answers.pop(0) if self.answers else [])
        return Cursor([])
    @asynccontextmanager
    async def transaction(self):
        self.transactions.append('BEGIN')
        try: yield self
        except BaseException: self.transactions.append('ROLLBACK');raise
        else:self.transactions.append('COMMIT')


async def ready(db=None):
    db=db or FixtureConnection()
    guard=PostgresRetriever(db,POLICY)
    await guard.verify_identity()
    return PostgresGenerationLifecycle(guard,tenant_id=7,session_id='session',message_id='assistant'),db


def test_identity_unverified_or_old_instance_prevents_generation_sql():
    async def run():
        db=FixtureConnection(); guard=PostgresRetriever(db,POLICY)
        lifecycle=PostgresGenerationLifecycle(guard,tenant_id=7,session_id='session',message_id='assistant')
        with pytest.raises(PermissionError):await lifecycle.claim_run('run',SHA,CONTEXT)
        assert not db.calls
        db.system='old-system'
        with pytest.raises(PermissionError):await guard.verify_identity()
        with pytest.raises(PermissionError):await lifecycle.claim_run('run',SHA,CONTEXT)
        assert len(db.calls)==1 and not db.transactions
    asyncio.run(run())


def test_claim_binds_owner_session_assistant_and_keeps_payload_hash_only():
    async def run():
        lifecycle,db=await ready(FixtureConnection([[],[{'run_id':'run'}]]))
        await lifecycle.claim_run('run',SHA,CONTEXT)
        assert db.transactions==['BEGIN','COMMIT']
        sql,params=db.calls[-1]
        assert sql.startswith('INSERT INTO generation_runs') and 'ON CONFLICT DO NOTHING RETURNING' in sql
        assert params[:6]==('run',7,'owner','session','assistant',SHA)
        assert json.loads(params[6])==['kb'] and json.loads(params[7])==['doc']
        assert db.calls[1][1]==('session',7,'owner') and db.calls[2][1]==('assistant',7,'owner','session')
        assert all('budget' not in sql.lower() and 'reserve' not in sql.lower() and 'settle' not in sql.lower() for sql,_ in db.calls)
    asyncio.run(run())


@pytest.mark.parametrize('db_kwargs,code',[({'session':False},'SCOPE_DENIED'),({'role':'user'},'MESSAGE_DENIED'),({'completed':True},'ALREADY_COMPLETED')])
def test_claim_invalid_owned_target_never_inserts(db_kwargs,code):
    async def run():
        lifecycle,db=await ready(FixtureConnection([[]],**db_kwargs))
        with pytest.raises(GenerationError,match=code):await lifecycle.claim_run('run',SHA,CONTEXT)
        assert not any(sql.startswith('INSERT INTO generation_runs') for sql,_ in db.calls)
        assert db.transactions[-1]=='ROLLBACK'
    asyncio.run(run())


def test_same_payload_claim_replays_without_provider_or_budget_repeat_and_changed_payload_conflicts():
    async def run():
        lifecycle,db=await ready(FixtureConnection([[run_row()]]))
        with pytest.raises(GenerationReplay):await lifecycle.claim_run('run',SHA,CONTEXT)
        assert not any(sql.startswith('INSERT') for sql,_ in db.calls)
        db.answers=[[run_row()]]
        with pytest.raises(GenerationError,match='PAYLOAD_CONFLICT'):await lifecycle.claim_run('run','b'*64,CONTEXT)
        db.answers=[[dict(run_row(),knowledge_base_ids=['other'])]]
        with pytest.raises(GenerationError,match='PAYLOAD_CONFLICT'):await lifecycle.claim_run('run',SHA,CONTEXT)
    asyncio.run(run())


def test_global_run_or_same_message_identity_collision_is_not_an_attach_to_other_owner():
    async def run():
        lifecycle,db=await ready(FixtureConnection([[],[]]))
        with pytest.raises(GenerationError,match='IDENTITY_CONFLICT'):await lifecycle.claim_run('run',SHA,CONTEXT)
        select=next(sql for sql,_ in db.calls if sql.startswith('SELECT * FROM generation_runs'))
        assert 'tenant_id=%s AND subject_id=%s AND session_id=%s AND message_id=%s' in select
        assert db.transactions[-1]=='ROLLBACK'
    asyncio.run(run())


def test_begin_requires_claim_and_stores_started_unknown_identity_without_budget_side_effect():
    async def run():
        lifecycle,db=await ready(FixtureConnection([[run_row()],[]]))
        attempt=record(); context=replace(CONTEXT,request_id=attempt.attempt_id)
        await lifecycle.begin_attempt(attempt,context)
        sql,params=db.calls[-1]
        assert sql.startswith('INSERT INTO generation_attempts')
        assert params[:9]==(attempt.attempt_id,'run',7,'owner',1,'cheap','provider','requested-model','https://fixture.invalid/api')
        usage=json.loads(params[-1]);assert usage['prompt_tokens'] is None and usage['completion_tokens'] is None and usage['status']=='UNKNOWN'
        assert not any('budget' in sql.lower() for sql,_ in db.calls)
        lifecycle,db=await ready(FixtureConnection([[]]))
        with pytest.raises(GenerationError,match='NOT_CLAIMED'):await lifecycle.begin_attempt(attempt,context)
    asyncio.run(run())


def test_duplicate_begin_replays_instead_of_allowing_second_call_and_identity_mutation_conflicts():
    async def run():
        attempt=record();ctx=replace(CONTEXT,request_id=attempt.attempt_id)
        lifecycle,db=await ready(FixtureConnection([[run_row()],[persisted(attempt)]]))
        with pytest.raises(GenerationReplay):await lifecycle.begin_attempt(attempt,ctx)
        assert not any(sql.startswith('INSERT INTO generation_attempts') for sql,_ in db.calls)
        db.answers=[[run_row()],[persisted(attempt)]]
        with pytest.raises(GenerationError,match='IDENTITY_CONFLICT'):await lifecycle.begin_attempt(replace(attempt,model_requested='changed'),ctx)
    asyncio.run(run())


@pytest.mark.parametrize('bad',[
    record(2,'expensive',upgrade_reason='MODEL_EMPTY'),
    record(3,'expensive',upgrade_reason='MODEL_EMPTY'),
    replace(record(),attempt_id='forged'),
])
def test_out_of_order_third_or_forged_attempts_cannot_insert(bad):
    async def run():
        lifecycle,db=await ready(FixtureConnection([[run_row()],[]]))
        with pytest.raises(GenerationError):await lifecycle.begin_attempt(bad,replace(CONTEXT,request_id=bad.attempt_id))
        assert not any(sql.startswith('INSERT INTO generation_attempts') for sql,_ in db.calls)
    asyncio.run(run())


@pytest.mark.parametrize('first,reason,allowed',[
    (replace(record(),status='FAILED',error_code='MODEL_CALL_TIMEOUT'),'MODEL_CALL_TIMEOUT',False),
    (replace(record(),status='COMPLETED',objective=('EMPTY',)),'MODEL_EMPTY',True),
    (replace(record(),status='TRUNCATED',objective=('TRUNCATED',)),'TRUNCATION',True),
    (replace(record(),status='COMPLETED',objective=('INVALID_HANDLES',),invalid_handle_count=1),'INVALID_HANDLES',True),
    (replace(record(),status='CANCELLED',error_code='MODEL_CALL_CANCELLED',objective=('EMPTY',)),'MODEL_EMPTY',False),
    (replace(record(),status='FAILED',error_code='BUDGET_DENIED',objective=('EMPTY',)),'MODEL_EMPTY',False),
    (replace(record(),status='FAILED',error_code='PROVIDER_STREAM_FAILED'),'MODEL_CALL_TIMEOUT',False),
])
def test_second_is_only_one_expensive_upgrade_for_recorded_objective_or_model_timeout(first,reason,allowed):
    async def run():
        lifecycle,db=await ready(FixtureConnection([[run_row()],[persisted(first)]]))
        second=record(2,'expensive',upgrade_reason=reason)
        if allowed:await lifecycle.begin_attempt(second,replace(CONTEXT,request_id=second.attempt_id))
        else:
            with pytest.raises(GenerationError,match='UPGRADE_DENIED'):await lifecycle.begin_attempt(second,replace(CONTEXT,request_id=second.attempt_id))
        assert any(sql.startswith('INSERT INTO generation_attempts') for sql,_ in db.calls)==allowed
    asyncio.run(run())


def test_uniform_prompt_context_and_no_cheap_retry_or_second_after_expensive_first():
    async def run():
        first=replace(record(),status='COMPLETED',objective=('EMPTY',))
        for first_record,second in [(first,record(2,'cheap',upgrade_reason='MODEL_EMPTY')),
             (replace(first,role='expensive',attempt_id='run/answer/1/expensive'),record(2,'expensive',upgrade_reason='MODEL_EMPTY')),
             (first,replace(record(2,'expensive',upgrade_reason='MODEL_EMPTY'),prompt_sha256='b'*64))]:
            lifecycle,db=await ready(FixtureConnection([[run_row()],[persisted(first_record)]]))
            with pytest.raises(GenerationError,match='UPGRADE_DENIED'):await lifecycle.begin_attempt(second,replace(CONTEXT,request_id=second.attempt_id))
    asyncio.run(run())


def test_finish_preserves_requested_reported_unknown_partial_usage_and_no_settlement_sql():
    async def run():
        started=record();finished=replace(started,status='FAILED',model_reported='actually-reported-different',usage=TokenUsage(prompt_tokens=12),error_code='PROVIDER_MODEL_IDENTITY_MISMATCH')
        lifecycle,db=await ready(FixtureConnection([[run_row()],[persisted(started)]]))
        await lifecycle.finish_attempt(finished,replace(CONTEXT,request_id=finished.attempt_id))
        sql,params=db.calls[-1]
        assert sql.startswith('UPDATE generation_attempts') and params[0]=='actually-reported-different'
        usage=json.loads(params[3]);assert usage['prompt_tokens']==12 and usage['completion_tokens'] is None and usage['total_tokens'] is None and usage['status']=='UNKNOWN'
        assert params[5]=='UNKNOWN' and 'model_requested=' not in sql and not any('settle' in sql.lower() or 'reserve' in sql.lower() for sql,_ in db.calls)
    asyncio.run(run())


def test_absent_reported_identity_stays_null_and_terminal_repeat_is_persistence_only():
    async def run():
        started=record();finished=replace(started,status='COMPLETED',finish_reason='stop')
        lifecycle,db=await ready(FixtureConnection([[run_row()],[persisted(started)]]))
        await lifecycle.finish_attempt(finished,replace(CONTEXT,request_id=finished.attempt_id))
        assert db.calls[-1][1][0] is None
        db.answers=[[run_row()],[persisted(finished)]];before=len(db.calls)
        await lifecycle.finish_attempt(finished,replace(CONTEXT,request_id=finished.attempt_id))
        assert not any(sql.startswith('UPDATE generation_attempts') for sql,_ in db.calls[before:])
        db.answers=[[run_row()],[persisted(finished)]]
        with pytest.raises(GenerationError,match='TERMINAL_CONFLICT'):await lifecycle.finish_attempt(replace(finished,usage=TokenUsage(1,1,2,'REPORTED')),replace(CONTEXT,request_id=finished.attempt_id))
    asyncio.run(run())


def test_finished_unknown_attempt_and_terminal_identity_change_cannot_write():
    async def run():
        finished=replace(record(),status='FAILED',error_code='PROVIDER_STREAM_FAILED')
        lifecycle,db=await ready(FixtureConnection([[run_row()],[]]))
        with pytest.raises(GenerationError,match='NOT_STARTED'):await lifecycle.finish_attempt(finished,replace(CONTEXT,request_id=finished.attempt_id))
        db.answers=[[run_row()],[persisted(record())]]
        with pytest.raises(GenerationError,match='IDENTITY_CONFLICT'):await lifecycle.finish_attempt(replace(finished,provider='changed'),replace(CONTEXT,request_id=finished.attempt_id))
    asyncio.run(run())


def test_insert_failure_rolls_back_and_changed_owner_session_context_is_rejected():
    async def run():
        lifecycle,db=await ready(FixtureConnection([[],[{'run_id':'run'}]],fail='INSERT INTO generation_runs'))
        with pytest.raises(RuntimeError):await lifecycle.claim_run('run',SHA,CONTEXT)
        assert db.transactions==['BEGIN','ROLLBACK']
        lifecycle,db=await ready()
        with pytest.raises(GenerationError,match='SCOPE_MISMATCH'):await lifecycle.claim_run('run',SHA,replace(CONTEXT,session_id='other'))
        assert not db.transactions
    asyncio.run(run())


def test_migration_declares_attempt_limits_owner_fk_and_no_budget_tables_or_runtime_execution():
    sql=(Path(__file__).resolve().parents[2]/'backend/weknora_port/storage/migrations/004_generation.sql').read_text(encoding='utf-8')
    assert 'CHECK(ordinal IN(1,2))' in sql and 'UNIQUE(run_id,ordinal)' in sql
    assert 'FOREIGN KEY(run_id,tenant_id,subject_id)' in sql
    assert "DEFAULT 'UNKNOWN'" in sql and 'model_reported TEXT,' in sql
    assert 'CREATE TABLE IF NOT EXISTS messages' not in sql and 'budget_reserv' not in sql


def test_actual_isolated_pg_claim_two_attempts_unknown_and_no_duplicate_dispatch():
    from test_memory_storage_pg import approved_dsn, actual_pg_run, connection, save_pg_evidence
    from backend.weknora_port.storage.generation_migrate import apply_generation_schema
    import uuid
    dsn=approved_dsn()
    async def run():
        conn,guard=await connection(dsn)
        tenant=None;owner='web_user:pg-generation-'+str(uuid.uuid4())
        session,message,run_id=(str(uuid.uuid4()) for _ in range(3))
        try:
            digest=await apply_generation_schema(guard)
            assert len(digest)==64
            tenant=(await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',('P5 storage fixture','isolated-test'))).fetchall())[0]['id']
            await conn.execute("INSERT INTO sessions(id,tenant_id,subject_id,knowledge_base_scope,document_scope) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)",(session,tenant,owner,'["kb-fixture"]','[]'))
            await conn.execute("INSERT INTO messages(id,request_id,session_id,tenant_id,subject_id,role,content) VALUES(%s,%s,%s,%s,%s,'assistant','')",(message,run_id,session,tenant,owner))
            lifecycle=PostgresGenerationLifecycle(guard,tenant_id=tenant,session_id=session,message_id=message)
            context=CallContext(run_id,TrustedScope(owner,('kb-fixture',)),session_id=session)
            await lifecycle.claim_run(run_id,SHA,context)
            with pytest.raises(GenerationReplay):await lifecycle.claim_run(run_id,SHA,context)
            first=AttemptRecord(run_id+'/answer/1/cheap',1,'cheap','fixture-provider','fixture-requested','https://fixture.invalid/api',SHA,SHA,SHA)
            attempt_context=replace(context,request_id=first.attempt_id)
            await lifecycle.begin_attempt(first,attempt_context)
            with pytest.raises(GenerationReplay):await lifecycle.begin_attempt(first,attempt_context)
            first_done=replace(first,status='TRUNCATED',finish_reason='length',objective=('TRUNCATED',))
            await lifecycle.finish_attempt(first_done,attempt_context)
            await lifecycle.finish_attempt(first_done,attempt_context)
            second=AttemptRecord(run_id+'/answer/2/expensive',2,'expensive','fixture-provider','fixture-expensive','https://fixture.invalid/api',SHA,SHA,SHA,upgrade_reason='TRUNCATION')
            second_context=replace(context,request_id=second.attempt_id)
            await lifecycle.begin_attempt(second,second_context)
            second_done=replace(second,status='COMPLETED',finish_reason='stop',usage=TokenUsage(prompt_tokens=12))
            await lifecycle.finish_attempt(second_done,second_context)
            await lifecycle.finish_attempt(second_done,second_context)
            with pytest.raises(GenerationReplay):await lifecycle.begin_attempt(second,second_context)
            with pytest.raises(GenerationError):await lifecycle.begin_attempt(replace(second,ordinal=3,attempt_id=run_id+'/answer/3/expensive'),replace(context,request_id=run_id+'/answer/3/expensive'))
            rows=await (await conn.execute('SELECT * FROM generation_attempts WHERE run_id=%s AND tenant_id=%s AND subject_id=%s ORDER BY ordinal',(run_id,tenant,owner))).fetchall()
            assert len(rows)==2 and [row['ordinal'] for row in rows]==[1,2]
            assert all(row['model_reported'] is None and row['cost_status']=='UNKNOWN' for row in rows)
            assert rows[0]['usage']['prompt_tokens'] is None and rows[1]['usage']['prompt_tokens']==12
            assert all(row['usage']['total_tokens'] is None and row['usage']['status']=='UNKNOWN' for row in rows)
            wrong=replace(context,scope=TrustedScope('foreign-owner',('kb-fixture',)))
            with pytest.raises(GenerationError):await lifecycle.claim_run(run_id,SHA,wrong)
            assert (await (await conn.execute('SELECT count(*) AS count FROM generation_attempts WHERE run_id=%s',(run_id,))).fetchall())[0]['count']==2
            identity=await guard.verify_identity()
            saved_migration=(await (await conn.execute('SELECT version,sha256 FROM port_schema_migrations WHERE version=%s',('004_generation',))).fetchall())[0]
            save_pg_evidence('generation_claim_two_attempts',{'database':identity.database,'system_identifier':identity.system_identifier,'tenant_id':tenant,'subject_id':owner,
                'session_id':session,'message_id':message,'run_id':run_id,'migration':saved_migration,'attempts':rows,'attempt_count_after_duplicates':2,
                'duplicate_claim':'GenerationReplay','duplicate_begin':'GenerationReplay','duplicate_finish':'idempotent no update','third_attempt':'denied','foreign_owner':'denied',
                'assertions':'PASS','fixture_cleanup':'retained in new isolated database for UNKNOWN readback','model_calls':0,'budget_calls':0})
            # No model object or budget adapter is constructed by this real DB test.
        finally:
            # Owner requested UNKNOWN fixture readback evidence be retained.
            # Only synthetic records in the physically verified new DB remain.
            await conn.close()
    actual_pg_run(run())
