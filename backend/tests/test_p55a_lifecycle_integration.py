"""REAL isolated PG/Redis; every model transport is explicitly SIMULATED."""
import json
import os
import sys
import time
import uuid
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Barrier
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.postgres.run_lifecycle import PostgresRunLifecycle
from backend.app.adapters.redis_stream import RedisStreamManager
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.run_lifecycle import RunLifecycle
from backend.app.ports.run_lifecycle import LifecycleDenied, current_execution
from backend.app.application.answer_service import AnswerService
from backend.app.application.quick_chain import QuickSettings
from backend.app.application.budget import PostgresBudgetGate
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.application.rule_router_v1 import RouterPolicy
from backend.app.ports.session_attempts import request_identity
from backend.app.domain.models import ChunkRecord
from backend.tests.test_p5_router_generation import make_chain, GOOD

ROOT = Path(__file__).resolve().parents[2]
URL = 'postgresql+psycopg://p55a:SIMULATED_TEST_ONLY@127.0.0.1:52352/p55a_lifecycle_test'
REDIS = 'redis://127.0.0.1:52355/0'


def connect():
    if os.getenv('P55A_ISOLATED_AUTHORIZED') != 'p5-redis-lifecycle-r1':
        raise RuntimeError('ISOLATED_RUNNER_REQUIRED')
    engine = create_engine(URL)
    with engine.connect() as c:
        assert c.execute(text('SELECT current_database(),oid,(SELECT system_identifier FROM pg_control_system()) FROM pg_database WHERE datname=current_database()')).one() == ('p55a_lifecycle_test',16384,7694635764170571814)
        expected = ('0020_long_term_memory' if os.getenv('P55B2_ISOLATED_AUTHORIZED') == 'p55b-memory-r1' else
                    '0019_context_checkpoints' if os.getenv('P55B_ISOLATED_AUTHORIZED') == 'p55b-context-r1' else '0018_run_attempt_lifecycle')
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==expected
    return engine


@pytest.fixture
def data(tmp_path):
    engine=connect()
    store=PostgresKnowledgeRepository(engine,ContentAddressedStorage(tmp_path/'isolated-storage'))
    kb=store.create_knowledge_base('SIMULATED-'+str(uuid.uuid4()),cloud_allowed=True)['id']
    doc,version,chunk=map(str,(uuid.uuid4(),uuid.uuid4(),uuid.uuid4()))
    content='A方案成本1000元。'
    import hashlib
    with engine.begin() as c:
        c.execute(text("INSERT INTO documents(id,knowledge_base_id,file_name,media_type,original_size) VALUES (:id,:kb,'SIMULATED.txt','text/plain',32)"),dict(id=doc,kb=kb))
        c.execute(text("INSERT INTO document_versions(id,document_id,version_no,source_sha256,storage_key,parser_version,index_status) VALUES (:id,:doc,1,:sha,'SIMULATED','SIMULATED','ready')"),dict(id=version,doc=doc,sha=hashlib.sha256(content.encode()).hexdigest()))
        c.execute(text("UPDATE documents SET active_version_id=:version WHERE id=:id"),dict(version=version,id=doc))
        c.execute(text("""INSERT INTO chunks(id,version_id,document_id,knowledge_base_id,chunk_index,content,content_sha256,start_pos,end_pos,locator)
            VALUES (:id,:version,:doc,:kb,0,:content,:sha,0,:length,CAST(:locator AS jsonb))"""),dict(id=chunk,version=version,doc=doc,kb=kb,content=content,sha=hashlib.sha256(content.encode()).hexdigest(),length=len(content),locator=json.dumps({'start':0,'end':len(content)})))
    conversation=store.create_conversation([kb])
    result=SimpleNamespace(engine=engine,store=store,conversation=conversation,chunk=chunk,version=version,doc=doc,kb=kb,content=content,root=tmp_path)
    yield result
    # All data remains confined to this new test-only cluster for review.
    engine.dispose()


def build(data, monkeypatch, *, cheap=GOOD, policy=None, block=None, streams=None):
    def observe():
        ctx=current_execution.get()
        with data.engine.begin() as c:
            c.execute(text('INSERT INTO p55a_simulated_sends(run_id,role) VALUES (:id,\'chat_cheap\')'),dict(id=ctx[0].run_id))
        if block: block()
    chain, _, calls, expensive=make_chain(monkeypatch,cheap=cheap,policy=policy,after_cheap=observe)
    # Bounded SIMULATED-only transport fixture for 24 completion runs; this
    # does not change the production adapter's admission limits.
    chain.generation_roles['chat_cheap'].gateway.max_requests=24
    repo=InMemoryRetrievalRepository();repo.add(ChunkRecord(data.chunk,data.kb,data.doc,data.version,data.content,{'start':0,'end':len(data.content)}))
    chain.knowledge_gateway=KnowledgeGateway(HybridRetriever(repo))
    budget=PostgresBudgetGate(data.engine,10000000)
    chain.generation_roles['chat_cheap'].gateway.usage_guard.budget_gate=budget
    chain.generation_roles['chat_expensive'].usage_guard.budget_gate=budget
    original=expensive.answer_with_product_scope
    def exp_send(*a,**kw):
        with data.engine.begin() as c:c.execute(text("INSERT INTO p55a_simulated_sends(run_id,role) VALUES (:id,'chat_expensive')"),dict(id=kw['run_id']))
        return original(*a,**kw)
    expensive.answer_with_product_scope=exp_send
    streams=streams or RedisStreamManager.from_url(REDIS,live_ttl=3,event_ttl=1,namespace='p55a:r1')
    life=RunLifecycle(PostgresRunLifecycle(data.engine,lease_seconds=3),streams,data.store)
    service=AnswerService(knowledge_gateway=chain.knowledge_gateway,quick_chain=chain,runs=data.store,
        lifecycle=life,quick_settings=QuickSettings(cloud_enabled=True))
    return service,life,chain,calls,expensive


def answer(service,data,identity=None,question='A 的成本是多少？'):
    return service.answer(data.conversation,question,request_id=identity or str(uuid.uuid4()))


def rows(data, query, **params):
    with data.engine.connect() as c:return list(c.execute(text(query),params).mappings())


def sends(data, identity):
    return rows(data,'SELECT role FROM p55a_simulated_sends WHERE run_id=:id',id=identity)


def test_normal_completion(data,monkeypatch):
    service,life,chain,calls,exp=build(data,monkeypatch)
    result=answer(service,data)
    assert result.error_code is None and result.answer==GOOD and result.citations==('E1',)
    assert life.streams.get_live_run(data.conversation['id']) is None
    assert rows(data,'SELECT state,cleanup_pending FROM rag_run_leases WHERE run_id=:id',id=result.run_id)==[{'state':'COMPLETED','cleanup_pending':False}]
    assert len(calls)==1 and exp.calls==[]


@pytest.mark.parametrize('cancel',[False,True])
def test_failure_cancel_cleanup(data,monkeypatch,cancel):
    def stop():
        if cancel:data.store.cancel_run(current_execution.get()[0].run_id)
    service,life,*_=build(data,monkeypatch,cheap='bad [E99]',policy=RouterPolicy(mode='CHEAP_ONLY'),block=stop)
    result=answer(service,data)
    assert result.error_code and life.streams.get_live_run(data.conversation['id']) is None
    assert rows(data,'SELECT status FROM rag_runs WHERE id=:id',id=result.run_id)[0]['status']==('cancelled' if cancel else 'failed')


def test_concurrent_duplicate_only_one_simulated_send(data,monkeypatch):
    service,life,*_=build(data,monkeypatch)
    identity=str(uuid.uuid4());barrier=Barrier(4)
    def run(_):barrier.wait();return answer(service,data,identity)
    with ThreadPoolExecutor(max_workers=4) as p:result=list(p.map(run,range(4)))
    assert len(sends(data,identity))==1
    assert all(r.error_code in {None,'RUN_IN_PROGRESS'} for r in result)


@pytest.mark.parametrize('change',['content','scope','mode'])
def test_conflicting_request_rejected(data,monkeypatch,change):
    service,life,*_=build(data,monkeypatch);identity=str(uuid.uuid4());answer(service,data,identity)
    with pytest.raises(LifecycleDenied,match='REQUEST_ID_CONFLICT'):
        life.repository.claim_run(data.conversation['id'],[data.kb],['different'] if change=='scope' else [],
            'different' if change=='content' else 'A 的成本是多少？','smart' if change=='mode' else 'quick',identity)
    assert len(sends(data,identity))==1


def test_cheap_expensive_durable_role_limit(data,monkeypatch):
    service,life,*_=build(data,monkeypatch,cheap='bad [E99]')
    result=answer(service,data);assert result.error_code is None
    attempts=rows(data,'SELECT role,state,diagnostics FROM rag_model_attempts WHERE run_id=:id ORDER BY ordinal',id=result.run_id)
    assert [a['role'] for a in attempts]==['chat_cheap','chat_expensive']
    assert all(a['state']=='COMPLETED' and a['diagnostics']['budget_reservation_id'] for a in attempts)
    assert len(sends(data,result.run_id))==2


def test_timeout_unknown_no_retry_after_redis_ttl(data,monkeypatch):
    service,life,*_=build(data,monkeypatch,cheap=TimeoutError('SIMULATED'))
    identity=str(uuid.uuid4());result=answer(service,data,identity);assert result.error_code
    assert rows(data,'SELECT state FROM rag_model_attempts WHERE run_id=:id',id=identity)==[{'state':'UNKNOWN'}]
    assert rows(data,'SELECT reservation_state FROM model_calls WHERE run_id=:id',id=identity)==[{'reservation_state':'unknown'}]
    time.sleep(3.1)
    service2,*_=build(data,monkeypatch)
    answer(service2,data,identity)
    assert len(sends(data,identity))==1


def test_crashed_attempt_expiry_fences_late_commit(data,monkeypatch):
    service,life,*_=build(data,monkeypatch);identity=str(uuid.uuid4())
    claim=life.repository.claim_run(data.conversation['id'],[data.kb],[],'A 的成本是多少？','quick',identity)
    life.streams.set_live_run(data.conversation['id'],identity,claim.owner)
    life.repository.claim_attempt(run_id=identity,owner=claim.owner,attempt_id=request_identity(identity,'quick.router.chat_cheap',1),role='chat_cheap',ordinal=1,envelope_hash='a'*64,provider='SIMULATED',model='SIMULATED')
    time.sleep(3.1)
    repeat=life.repository.claim_run(data.conversation['id'],[data.kb],[],'A 的成本是多少？','quick',identity)
    assert repeat.state=='UNKNOWN' and not repeat.created
    with pytest.raises(LifecycleDenied):life.repository.renew(identity,claim.owner)
    from backend.app.ports.run_lifecycle import execution_owner
    token=execution_owner.set((identity,claim.owner))
    context_token=current_execution.set((claim,life,Event(),data.conversation['id']))
    try:
        with pytest.raises(LifecycleDenied,match='RUN_LEASE_LOST'):
            data.store.finalize_answer(run_id=identity,conversation_id=data.conversation['id'],answer='',
                citations=(),snapshots=(),error_code='SIMULATED_LATE_RESULT',mode='quick')
    finally:
        current_execution.reset(context_token)
        execution_owner.reset(token)
    assert rows(data,'SELECT status FROM rag_runs WHERE id=:id',id=identity)==[{'status':'running'}]
    result=answer(service,data,identity)
    assert result.error_code=='RUN_UNKNOWN' and sends(data,identity)==[]


def worker_args(data,identity,output):
    return [sys.executable,'-B','-X','utf8',str(ROOT/'scripts/verify_p55a_isolated.py'),'--worker',data.conversation['id'],identity,str(output)]


def test_two_independent_processes_and_restart(data,monkeypatch,tmp_path):
    identity=str(uuid.uuid4())
    children=[subprocess.Popen(worker_args(data,identity,tmp_path/f'worker-{i}.json'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
    for child in children:
        out,err=child.communicate(timeout=30);assert child.returncode==0,(out,err)
    repeat=subprocess.run(worker_args(data,identity,tmp_path/'restarted.json'),capture_output=True,text=True,timeout=30)
    assert repeat.returncode==0,(repeat.stdout,repeat.stderr)
    assert len(sends(data,identity))==1
    assert json.loads((tmp_path/'restarted.json').read_text())['replayed'] is True


@pytest.mark.parametrize('stage',['admission','lost','cleanup'])
def test_redis_failure_recovery_no_regeneration(data,monkeypatch,stage):
    real=RedisStreamManager.from_url(REDIS,live_ttl=3,event_ttl=1,namespace='p55a:r1')
    service,life,chain,calls,*_=build(data,monkeypatch,streams=real)
    identity=str(uuid.uuid4())
    method={'admission':'set_live_run','lost':'renew_live_run','cleanup':'clear_live_run'}[stage]
    original=getattr(real,method)
    def fail(*a,**k):raise OSError('SIMULATED_REDIS_CONNECTION_LOSS')
    monkeypatch.setattr(real,method,fail)
    result=answer(service,data,identity)
    assert len(calls)==(1 if stage=='cleanup' else 0)
    if stage=='cleanup':
        assert result.error_code is None
        assert rows(data,'SELECT cleanup_pending FROM rag_run_leases WHERE run_id=:id',id=identity)[0]['cleanup_pending']
    if stage=='admission':
        assert rows(data,'SELECT status,error_code FROM rag_runs WHERE id=:id',id=identity)==[{'status':'failed','error_code':'STREAM_ADMISSION_DENIED'}]
        assert rows(data,'SELECT state FROM rag_run_leases WHERE run_id=:id',id=identity)==[{'state':'NOT_SENT'}]
    monkeypatch.setattr(real,method,original)
    answer(service,data,identity)
    assert len(calls)==(1 if stage=='cleanup' else 0)


def test_real_redis_connection_error_fails_closed(data,monkeypatch):
    # Same client and endpoint: close its owned pool, then injected connection
    # acquisition failure. No other port or container is contacted.
    real=RedisStreamManager.from_url(REDIS,live_ttl=3,namespace='p55a:r1')
    real.client.connection_pool.disconnect()
    monkeypatch.setattr(real.client.connection_pool,'get_connection',lambda *a,**k: (_ for _ in ()).throw(ConnectionError('SIMULATED')))
    service,life,*_=build(data,monkeypatch,streams=real)
    result=answer(service,data)
    assert result.error_code=='STREAM_ADMISSION_DENIED' and sends(data,result.run_id)==[]


@pytest.mark.parametrize('stage',['claim_attempt','finish_attempt'])
def test_real_postgres_transaction_error_no_retry(data,monkeypatch,stage):
    service,life,chain,calls,*_=build(data,monkeypatch)
    def fail(*a,**k):
        with data.engine.begin() as c:c.execute(text('SELECT 1/0'))
    monkeypatch.setattr(life.repository,stage,fail)
    identity=str(uuid.uuid4());result=answer(service,data,identity)
    assert result.error_code and len(calls)==(1 if stage=='finish_attempt' else 0)
    answer(service,data,identity)
    assert len(calls)==(1 if stage=='finish_attempt' else 0)
    if stage=='finish_attempt':
        assert rows(data,'SELECT state FROM rag_model_attempts WHERE run_id=:id',id=identity)==[{'state':'UNKNOWN'}]
        assert rows(data,'SELECT reservation_state FROM model_calls WHERE run_id=:id',id=identity)==[{'reservation_state':'unknown'}]


def test_late_cleanup_cannot_delete_new_owner(data):
    streams=RedisStreamManager.from_url(REDIS,live_ttl=3,namespace='p55a:r1')
    session=data.conversation['id'];old,new=str(uuid.uuid4()),str(uuid.uuid4())
    assert streams.set_live_run(session,old,'old-owner')
    assert not streams.set_live_run(session,new,'new-owner')
    assert not streams.renew_live_run(session,old,'wrong-owner')
    assert streams.clear_live_run(session,old,'old-owner')
    assert streams.set_live_run(session,new,'new-owner')
    assert not streams.clear_live_run(session,old,'old-owner')
    assert streams.get_live_run(session)=={'run_id':new,'owner':'new-owner'}
    assert streams.clear_live_run(session,new,'new-owner')


def test_sse_replay_and_expired_cache_pg_final(data,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.routes import router
    service,life,chain,calls,*_=build(data,monkeypatch);result=answer(service,data)
    events,saved=life.events(data.store,result.run_id,data.conversation['id'],[data.kb],[],0)
    assert events and saved['answer']==GOOD
    assert life.streams.get_events(data.conversation['id'],result.run_id,0)
    time.sleep(1.1);assert life.streams.get_events(data.conversation['id'],result.run_id,0)==[]
    app=FastAPI();app.include_router(router);app.state.container=SimpleNamespace(store=data.store,lifecycle=life)
    app.state.settings=SimpleNamespace(service_name='SIMULATED')
    with TestClient(app) as client:
        response=client.get(f'/api/v1/runs/{result.run_id}/events',params={'conversation_id':data.conversation['id']},headers={'Last-Event-ID':str(events[0]['seq'])})
        assert response.status_code==200 and GOOD in response.text
        assert f"id: {events[0]['seq']}\n" not in response.text
        assert client.get(f'/api/v1/runs/{result.run_id}/events',params={'conversation_id':str(uuid.uuid4())}).status_code==404
    assert len(calls)==1


def test_multiple_sessions_parallel_allowed(data,monkeypatch):
    service,life,*_=build(data,monkeypatch)
    second=data.store.create_conversation([data.kb])
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(service.answer,data.conversation,'A 的成本是多少？',request_id=str(uuid.uuid4()))
        b=pool.submit(service.answer,second,'A 的成本是多少？',request_id=str(uuid.uuid4()))
        assert a.result().error_code is None and b.result().error_code is None


def test_history_budget_citation_unchanged(data,monkeypatch):
    service,life,*_=build(data,monkeypatch)
    budget=PostgresBudgetGate(data.engine,10000000)
    old=budget.reserve(run_id=None,provider='SIMULATED_HISTORY',model_name='SIMULATED',capability='chat',estimate_microunits=123)
    budget.mark_unknown(old.reservation_id)
    before=rows(data,'SELECT * FROM model_calls WHERE id=:id',id=old.reservation_id)
    result=answer(service,data)
    citation=data.store.get_citation(result.run_id,'E1')
    assert citation['quote']==data.content and citation['version_id']==data.version
    assert rows(data,'SELECT * FROM model_calls WHERE id=:id',id=old.reservation_id)==before
    messages=rows(data,'SELECT role,content,run_id FROM conversation_messages WHERE run_id=:id ORDER BY created_at',id=result.run_id)
    assert [m['role'] for m in messages]==['user','assistant'] and str(messages[1]['run_id'])==result.run_id
    with pytest.raises(LifecycleDenied):life.repository.read_run(result.run_id,data.conversation['id'],['wrong-kb'],[])


def test_large_completed_history_not_in_process_identity_store(data,monkeypatch):
    service,life,chain,calls,*_=build(data,monkeypatch)
    # E removes old sets only after the first real integration round passes.
    for _ in range(24):assert answer(service,data).error_code is None
    assert life.streams.get_live_run(data.conversation['id']) is None
    assert not hasattr(life,'historical_runs')
    if os.getenv('P55A_REQUIRE_OLD_REMOVED')=='1':
        assert not hasattr(chain,'_router_runs') and not hasattr(chain,'_router_attempts')
    assert len(calls)==24


def test_existing_rerank_budget_does_not_require_chat_attempt(data,monkeypatch):
    """Actual normal guard + real PG; no model transport/API call."""
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.domain.model_registry import ModelRegistry
    service,life,*_=build(data,monkeypatch)
    identity=str(uuid.uuid4())
    claim=life.repository.claim_run(data.conversation['id'],[data.kb],[],'SIMULATED_RERANK_BUDGET','quick',identity)
    spec=ModelRegistry.frozen_defaults().select('rerank')
    guard=BudgetUsageGuard(PostgresBudgetGate(data.engine,10000000),spec,1)
    with life.executing(claim,data.conversation['id']):
        reservation=guard.reserve(model_key=spec.model_key,role=spec.role,model_id=spec.model_id,planned_tokens=None)
        guard.settle(reservation,None,sent=True)
        assert rows(data,'SELECT state FROM rag_model_attempts WHERE run_id=:id',id=identity)==[]
        assert rows(data,'SELECT purpose,reservation_state FROM model_calls WHERE id=:id',id=reservation)==[{'purpose':'rerank','reservation_state':'unknown'}]
        data.store.finalize_answer(run_id=identity,conversation_id=data.conversation['id'],answer='',citations=(),snapshots=(),error_code='SIMULATED_NO_GENERATION',mode='quick')
    assert life.streams.get_live_run(data.conversation['id']) is None and sends(data,identity)==[]


def test_owner_unhandled_exception_terminal_preserves_unknown(data,monkeypatch,tmp_path):
    service,life,*_=build(data,monkeypatch)
    identity=str(uuid.uuid4()); budget=PostgresBudgetGate(data.engine,10000000)
    observations={}
    def unexpected(*args,**kwargs):
        claim=current_execution.get()[0]
        life.repository.claim_attempt(run_id=identity,owner=claim.owner,
            attempt_id=request_identity(identity,'quick.router.chat_cheap',1),role='chat_cheap',ordinal=1,
            envelope_hash='a'*64,provider='SIMULATED',model='SIMULATED')
        reservation=budget.reserve(run_id=identity,provider='SIMULATED',model_name='SIMULATED',capability='chat',estimate_microunits=123)
        budget.mark_unknown(reservation.reservation_id)
        observations['budget']=rows(data,'SELECT * FROM model_calls WHERE id=:id',id=reservation.reservation_id)
        observations['reservation_id']=reservation.reservation_id
        raise RuntimeError('SIMULATED_UNHANDLED_PRIVATE_MESSAGE')
    monkeypatch.setattr(service,'_answer',unexpected)
    with pytest.raises(RuntimeError): answer(service,data,identity)
    observations['run']=rows(data,'SELECT status,error_code FROM rag_runs WHERE id=:id',id=identity)
    observations['lease']=rows(data,'SELECT state FROM rag_run_leases WHERE run_id=:id',id=identity)
    (tmp_path/'observed-exception-path.json').write_text(json.dumps(observations,default=str,indent=2))
    assert observations['run']==[{'status':'failed','error_code':'RUN_EXECUTION_FAILED'}]
    assert rows(data,'SELECT state FROM rag_model_attempts WHERE run_id=:id',id=identity)==[{'state':'UNKNOWN'}]
    assert rows(data,'SELECT * FROM model_calls WHERE id=:id',id=observations['reservation_id'])==observations['budget']
    assert life.streams.get_live_run(data.conversation['id']) is None
    repeat=answer(service,data,identity)
    assert repeat.error_code=='RUN_EXECUTION_FAILED' and repeat.trace['replayed']
    assert sends(data,identity)==[]


def test_owner_exception_never_overwrites_other_owner_or_cancel(data,monkeypatch):
    service,life,*_=build(data,monkeypatch)
    identity=str(uuid.uuid4())
    claim=life.repository.claim_run(data.conversation['id'],[data.kb],[],'SIMULATED','quick',identity)
    with pytest.raises(LifecycleDenied,match='RUN_FAILURE_OWNER_MISMATCH'):
        life.repository.fail_run(identity,str(uuid.uuid4()))
    assert rows(data,'SELECT status FROM rag_runs WHERE id=:id',id=identity)==[{'status':'running'}]
    with pytest.raises(RuntimeError):
        with life.executing(claim,data.conversation['id']):
            assert data.store.cancel_run(identity)
            raise RuntimeError('SIMULATED_PRIVATE_EXCEPTION')
    assert rows(data,'SELECT status,error_code FROM rag_runs WHERE id=:id',id=identity)==[{'status':'cancelled','error_code':'CANCELLED'}]
    assert life.streams.get_live_run(data.conversation['id']) is None


def test_owner_cache_writes_only_committed_stage_and_final_events(data,monkeypatch):
    service,life,*_=build(data,monkeypatch)
    observed=[]; original=life.streams.append_event
    def cache(session,identity,event):
        persisted=rows(data,'SELECT payload FROM retrieval_events WHERE run_id=:id AND seq=:seq',id=identity,seq=event['seq'])
        assert persisted and persisted[0]['payload']==event['data']
        if event['event']=='answer.completed':
            assert rows(data,'SELECT status FROM rag_runs WHERE id=:id',id=identity)==[{'status':'completed'}]
            assert rows(data,"SELECT content FROM conversation_messages WHERE run_id=:id AND role='assistant'",id=identity)
        observed.append(event['event']); return original(session,identity,event)
    monkeypatch.setattr(life.streams,'append_event',cache)
    result=answer(service,data)
    assert result.error_code is None and {'run.created','retrieval.started','answer.completed'}<=set(observed)
    assert 'answer.completed' in [e['event'] for e in life.streams.get_events(data.conversation['id'],result.run_id,0)]


def test_owner_final_rollback_never_caches_uncommitted_answer(data,monkeypatch):
    from sqlalchemy import event
    service,life,*_=build(data,monkeypatch); identity=str(uuid.uuid4())
    def reject_final(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('UPDATE rag_runs SET status=') and parameters.get('status')=='completed':
            raise RuntimeError('SIMULATED_FINAL_TX_ROLLBACK')
    event.listen(data.engine,'before_cursor_execute',reject_final)
    try:
        with pytest.raises(RuntimeError): answer(service,data,identity)
    finally: event.remove(data.engine,'before_cursor_execute',reject_final)
    assert rows(data,"SELECT seq FROM retrieval_events WHERE run_id=:id AND event_type='answer.completed'",id=identity)==[]
    assert rows(data,'SELECT label FROM answer_evidence WHERE run_id=:id',id=identity)==[]
    assert rows(data,"SELECT content FROM conversation_messages WHERE run_id=:id AND role='assistant'",id=identity)==[]
    assert 'answer.completed' not in [e['event'] for e in life.streams.get_events(data.conversation['id'],identity,0)]
    assert rows(data,'SELECT status,error_code FROM rag_runs WHERE id=:id',id=identity)==[{'status':'failed','error_code':'RUN_EXECUTION_FAILED'}]
    assert len(sends(data,identity))==1


def test_owner_citation_order_e2_e1_replay_and_sse(data,monkeypatch):
    import hashlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.routes import router
    # Equal keyword scores are ordered by chunk UUID, not insertion order.
    # Keep real UUID identities but deterministically bind A=E1, B=E2.
    content='B方案成本2000元。'; second=str(uuid.UUID(int=uuid.UUID(data.chunk).int+1))
    with data.engine.begin() as c:
        c.execute(text('''INSERT INTO chunks(id,version_id,document_id,knowledge_base_id,chunk_index,content,content_sha256,start_pos,end_pos,locator)
            VALUES (:id,:version,:doc,:kb,1,:content,:sha,0,:length,CAST(:locator AS jsonb))'''),dict(id=second,version=data.version,doc=data.doc,kb=data.kb,content=content,sha=hashlib.sha256(content.encode()).hexdigest(),length=len(content),locator=json.dumps({'start':0,'end':len(content)})))
    good='B方案成本2000元 [E2]；A方案成本1000元 [E1]'
    service,life,chain,calls,*_=build(data,monkeypatch,cheap=good,policy=RouterPolicy(mode='CHEAP_ONLY'))
    repo=InMemoryRetrievalRepository()
    for identity,body in ((data.chunk,data.content),(second,content)):
        repo.add(ChunkRecord(identity,data.kb,data.doc,data.version,body,{'start':0,'end':len(body)}))
    chain.knowledge_gateway=KnowledgeGateway(HybridRetriever(repo));service.knowledge_gateway=chain.knowledge_gateway
    identity=str(uuid.uuid4()); question='A 和 B 的成本是多少？'
    from backend.app.domain.text_normalization import normalize_query
    from backend.app.domain.scope import Scope
    ranked=repo.keyword_candidates(Scope.from_ids([data.kb]),normalize_query(question),10)
    assert [hit.chunk_id for hit in ranked]==[data.chunk,second]
    result=answer(service,data,identity,question)
    assert result.error_code is None and result.citations==('E2','E1')
    replay=answer(service,data,identity,question)
    assert replay.citations==('E2','E1') and replay.answer==good and replay.trace['replayed']
    assert data.store.get_citation(identity,'E2')['chunk_id']==second
    assert data.store.get_citation(identity,'E1')['chunk_id']==data.chunk
    # PostgreSQL remains authoritative even without any usable event cache.
    monkeypatch.setattr(life.streams,'get_events',lambda *a: (_ for _ in ()).throw(ConnectionError('SIMULATED')))
    monkeypatch.setattr(life.streams,'append_event',lambda *a: (_ for _ in ()).throw(ConnectionError('SIMULATED')))
    app=FastAPI();app.include_router(router);app.state.container=SimpleNamespace(store=data.store,lifecycle=life)
    app.state.settings=SimpleNamespace(service_name='SIMULATED')
    with TestClient(app) as client:
        response=client.get(f'/api/v1/runs/{identity}/events',params={'conversation_id':data.conversation['id']})
        assert response.status_code==200
        app.state.container.lifecycle=None
        uncached=client.get(f'/api/v1/runs/{identity}/events',params={'conversation_id':data.conversation['id']})
        assert uncached.status_code==200 and uncached.text==response.text
    terminal=[json.loads(line[6:]) for block in response.text.split('\n\n') if 'event: answer.completed' in block for line in block.splitlines() if line.startswith('data: ')][0]
    assert terminal['citations']==['E2','E1'] and terminal['answer']==good
    assert len(calls)==1
    # Corrupt only this newly-created synthetic row; no prior history touched.
    with data.engine.begin() as c:
        c.execute(text("UPDATE answer_evidence SET quote_sha256=:sha WHERE run_id=:id AND label='E2'"),dict(sha='0'*64,id=identity))
    with pytest.raises(LifecycleDenied,match='RUN_CITATION_INTEGRITY_ERROR'):
        life.repository.read_run(identity,data.conversation['id'],[data.kb],[])


def test_owner_terminal_commit_between_status_and_events_reads(data,monkeypatch):
    service,life,*_=build(data,monkeypatch);result=answer(service,data)
    original=life.repository.read_run;calls=[]
    def raced_read(*args):
        saved=original(*args);calls.append(saved['status'])
        # Deterministic simulation of the first read preceding the final TX.
        return {**saved,'status':'running','answer':'','citations':()} if len(calls)==1 else saved
    monkeypatch.setattr(life.repository,'read_run',raced_read)
    events,saved=life.events(data.store,result.run_id,data.conversation['id'],[data.kb],[],0)
    assert len(calls)==2 and saved['status']=='completed'
    assert events[-1]['event']=='answer.completed' and events[-1]['data']['answer']==GOOD
    assert events[-1]['data']['citations']==['E1']


def test_owner_cache_seq_is_unique_under_concurrent_conflicting_writes(data):
    from backend.app.adapters.redis_stream import StreamUnavailable
    streams=RedisStreamManager.from_url(REDIS,namespace='p55a:owner:r1')
    identity=str(uuid.uuid4());session=data.conversation['id']; barrier=Barrier(2)
    def insert(value):
        barrier.wait()
        try: streams.append_event(session,identity,dict(seq=1,event='stage',data={'value':value}));return 'OK'
        except StreamUnavailable as e:return str(e)
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(insert,('a','b')))
    assert sorted(results)==['OK','STREAM_EVENT_CONFLICT']
    events=streams.get_events(session,identity,0);assert len(events)==1
    streams.append_event(session,identity,events[0])
    assert streams.get_events(session,identity,0)==events


@pytest.mark.parametrize('limit',['event','count','bytes'])
def test_owner_cache_limits_fall_back_to_pg_without_losing_facts(data,monkeypatch,limit):
    from backend.app.adapters.redis_stream import StreamUnavailable
    kwargs={'max_event_bytes':16} if limit=='event' else {'max_run_events':1} if limit=='count' else {'max_run_bytes':60}
    streams=RedisStreamManager.from_url(REDIS,namespace='p55a:owner:r1',**kwargs)
    session=data.conversation['id'];identity=str(uuid.uuid4())
    if limit=='event':
        with pytest.raises(StreamUnavailable,match='STREAM_CACHE_LIMIT'):
            streams.append_event(session,identity,dict(seq=1,event='stage',data={'text':'中'*20}))
        assert streams.get_events(session,identity,0)==[]
    else:
        first=dict(seq=1,event='stage',data={});streams.append_event(session,identity,first)
        with pytest.raises(StreamUnavailable,match='STREAM_CACHE_LIMIT'):
            streams.append_event(session,identity,dict(seq=2,event='stage',data={}))
        assert streams.get_events(session,identity,0)==[first]
    service,life,chain,calls,*_=build(data,monkeypatch,streams=streams)
    result=answer(service,data);assert result.error_code is None
    canonical=data.store.list_events(result.run_id)
    events,saved=life.events(data.store,result.run_id,session,[data.kb],[],0)
    assert [e['seq'] for e in events]==[e['seq'] for e in canonical]
    assert saved['answer']==GOOD and events[-1]['data']['answer']==GOOD
    assert len(calls)==1 and len(rows(data,'SELECT label FROM answer_evidence WHERE run_id=:id',id=result.run_id))==1


def test_owner_stage_sse_during_post_then_scoped_reconnect(data,monkeypatch,tmp_path):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api import routes
    held,release=Event(),Event()
    def pause():
        held.set(); assert release.wait(15)
    service,life,chain,calls,*_=build(data,monkeypatch,block=pause)
    identity=str(uuid.uuid4());session=data.conversation['id']
    app=FastAPI();app.include_router(routes.router)
    app.state.container=SimpleNamespace(store=data.store,lifecycle=life)
    app.state.settings=SimpleNamespace(service_name='SIMULATED')
    monkeypatch.setattr(routes,'_answer_service',lambda request:service)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        post=pool.submit(client.post,f'/api/v1/conversations/{session}/messages',json=dict(content='A 的成本是多少？',mode='quick',request_id=identity,expected_knowledge_base_scope=[data.kb],expected_document_scope=[]))
        assert held.wait(10)
        assert not post.done()
        cached=life.streams.get_events(session,identity,0)
        assert 'retrieval.started' in [e['event'] for e in cached]
        parts=[]
        async def first_stream():
            disconnect=asyncio.Event()
            async def receive():
                await disconnect.wait();return {'type':'http.disconnect'}
            async def send(message):
                if message['type']=='http.response.start':assert message['status']==200
                if message['type']=='http.response.body':
                    body=message.get('body',b'');parts.append(body.decode())
                    if b'event: retrieval.started' in body:disconnect.set()
            scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method='GET',scheme='http',path=f'/api/v1/runs/{identity}/events',raw_path=f'/api/v1/runs/{identity}/events'.encode(),query_string=f'conversation_id={session}'.encode(),headers=[],server=('testserver',80),client=('testclient',1),root_path='')
            await asyncio.wait_for(app(scope,receive,send),5)
        try:
            asyncio.run(first_stream())
            assert not post.done() and GOOD not in ''.join(parts)
            seen=[int(line[4:]) for part in parts for line in part.splitlines() if line.startswith('id: ')]
            assert seen
        finally:release.set()
        response=post.result(timeout=10);assert response.status_code==201
        replay=client.get(f'/api/v1/runs/{identity}/events',params={'conversation_id':session},headers={'Last-Event-ID':str(max(seen))})
        assert replay.status_code==200 and GOOD in replay.text
        assert all(int(line[4:])>max(seen) for line in replay.text.splitlines() if line.startswith('id: '))
        assert client.get(f'/api/v1/runs/{identity}/events',params={'conversation_id':str(uuid.uuid4())}).status_code==404
    assert len(calls)==1
    (tmp_path/'live-post-sse-evidence.json').write_text(json.dumps(dict(request_id=identity,stage_during_post=parts,reconnect=replay.text,simulated_provider_sends=len(calls))))
