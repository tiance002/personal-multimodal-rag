"""Real isolated PostgreSQL; all model providers SIMULATED."""
import uuid
from types import SimpleNamespace
from sqlalchemy import text
from backend.tests.test_p55a_lifecycle_integration import data,build,answer,rows
from backend.tests.test_p55b2_memory_integration import memory
from backend.app.application.memory import MemoryService
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.scope import Scope
from dataclasses import replace
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.app.application.context_window import ContextManager
from backend.app.adapters.postgres.context_checkpoint import PostgresContextStore
from backend.app.adapters.postgres.query_understand import PostgresQueryAttemptStore
from backend.app.application.query_understand import QueryUnderstandService
from backend.app.application.memory import MemoryRetriever
from backend.tests.test_p55c_query_understand import Parser
from backend.tests.test_p55b_context_window import window
from backend.app.application.budget import PostgresBudgetGate
from backend.app.ports.providers import ProviderRequestNotSent

def query_service(data,life,m=None,parser=None):
    p=parser or Parser()
    context=ContextManager(data.store,PostgresContextStore(data.engine),{},
        memory_retriever=MemoryRetriever(m.repo,m.principal) if m else None)
    service=QueryUnderstandService(context,provider=p,attempt_store=PostgresQueryAttemptStore(data.engine),
        budget_gate=PostgresBudgetGate(data.engine,1000000000),window=replace(window(20000),provider=p.provider_name,model=p.chat_model),
        reserve_microunits=7,egress_enabled=True)
    return service,p

def claims(data,life,q='它的成本是多少？'):
    return life.repository.claim_run(data.conversation['id'],[data.kb],[],q,'quick',str(uuid.uuid4()))

def understand(service,data,run):
    return service.understand('它的成本是多少？',conversation=data.conversation['id'],scope=Scope.from_ids([data.kb]),
        run=run,cloud_allowed_by_kb={data.kb:True})

def test_real_pg_query_attempt_success_independent_usage_and_no_replay(data,memory,monkeypatch):
    service,life,chain,*_=build(data,monkeypatch);old=answer(service,data)
    q,p=query_service(data,life,memory)
    item=memory.repo.save(memory.principal,memory.scope,'preference','language','Chinese [E1]')
    claim=claims(data,life)
    with life.executing(claim,data.conversation['id']):
        result=understand(q,data,claim.run_id)
        assert not result.needs_clarification and result.history_run_ids==(old.run_id,)
        assert result.memory_identities==((item,1),) and result.model_status=='SIMULATED_COMPLETED'
        # Reconstructed adapter, same run identity, persistent refusal.
        q.attempts=PostgresQueryAttemptStore(data.engine)
        duplicate=understand(q,data,claim.run_id)
        assert duplicate.reason=='QUERY_ATTEMPT_ALREADY_CONSUMED' and len(p.calls)==1
        assert rows(data,"SELECT count(*) AS n FROM rag_model_attempts WHERE run_id=:id",id=claim.run_id)==[{'n':0}]
        assert rows(data,"SELECT purpose,reservation_state FROM model_calls WHERE run_id=:id",id=claim.run_id)==[{'purpose':'query_understand','reservation_state':'unknown'}]
        states=rows(data,"SELECT payload->>'state' AS state FROM retrieval_events WHERE run_id=:id AND event_type='query_understand.attempt' ORDER BY seq",id=claim.run_id)
        assert [x['state'] for x in states]==['CLAIMED','RESERVED','DISPATCHING','COMPLETED']
        data.store.complete_run(claim.run_id,'failed','SIMULATED_QUERY_TEST_ONLY')

@pytest.mark.parametrize('error,state',[(TimeoutError('do not echo'),'unknown'),(ProviderRequestNotSent('do not echo'),'released')])
def test_real_pg_query_failure_unknown_and_unsent_no_second_request(data,monkeypatch,error,state):
    service,life,*_=build(data,monkeypatch);answer(service,data)
    q,p=query_service(data,life,parser=Parser(error));claim=claims(data,life)
    with life.executing(claim,data.conversation['id']):
        first=understand(q,data,claim.run_id);second=understand(q,data,claim.run_id)
        assert first.needs_clarification and second.reason=='QUERY_ATTEMPT_ALREADY_CONSUMED'
        assert len(p.calls)==1 and 'do not echo' not in str(first.public())
        assert rows(data,'SELECT reservation_state FROM model_calls WHERE run_id=:id',id=claim.run_id)==[{'reservation_state':state}]
        data.store.complete_run(claim.run_id,'failed','SIMULATED_QUERY_TEST_ONLY')

def test_real_pg_concurrent_connections_one_query_dispatch(data,monkeypatch):
    service,life,*_=build(data,monkeypatch);answer(service,data)
    q,p=query_service(data,life);claim=claims(data,life)
    with life.executing(claim,data.conversation['id']):
        contexts=[copy_context(),copy_context()]
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(c.run,understand,q,data,claim.run_id) for c in contexts]
            results=[f.result() for f in futures]
        assert len(p.calls)==1 and sum(r.model_status=='SIMULATED_COMPLETED' for r in results)==1
        assert rows(data,"SELECT count(*) AS n FROM model_calls WHERE run_id=:id AND purpose='query_understand'",id=claim.run_id)==[{'n':1}]
        data.store.complete_run(claim.run_id,'failed','SIMULATED_QUERY_TEST_ONLY')

@pytest.mark.parametrize('mode',['quick','smart'])
def test_normal_entry_shared_interpretation_q0_citations_and_generation_limits(data,memory,monkeypatch,mode):
    from backend.tests.test_p55b_context_integration import CostModel
    from backend.tests.test_p5_router_generation import GOOD
    from backend.app.application.langchain_agent import LangChainAgentAdapter
    from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
    service,life,chain,*_=build(data,monkeypatch);previous=answer(service,data)
    q,p=query_service(data,life,memory);service.query_understand=q
    if mode=='smart':
        manager=ContextManager(data.store,PostgresContextStore(data.engine),{'local_chat':window(30000)})
        service.smart_agent=LangChainAgentAdapter(CostModel(final_answer=GOOD),context_manager=manager)
        service.agent_trace_store=PostgresAgentRepository(data.engine)
    result=service.answer(data.conversation,'它的成本是多少？',mode,request_id=str(uuid.uuid4()))
    assert result.error_code is None and result.citations==('E1',)
    u=result.trace['query_understand']
    assert u['q0']=='它的成本是多少？' and u['rewrite_query']=='A 的成本是多少？'
    assert u['history_run_ids']==(previous.run_id,) and u['intent']=='lookup'
    assert len(p.calls)==1
    assert len([r for r in result.trace['metrics']['model_calls'] if r['stage']=='query_understand'])==1
    assert rows(data,'SELECT q0 FROM rag_runs WHERE id=:id',id=result.run_id)==[{'q0':'它的成本是多少？'}]
    assert rows(data,'SELECT quote,quote_sha256,version_id,locator FROM answer_evidence WHERE run_id=:id',id=previous.run_id)==rows(data,'SELECT quote,quote_sha256,version_id,locator FROM answer_evidence WHERE run_id=:id',id=result.run_id)
    if mode=='quick':
        assert rows(data,'SELECT role FROM rag_model_attempts WHERE run_id=:id',id=result.run_id)==[{'role':'chat_cheap'}]
        router=result.trace['metrics']['rule_router']
        assert router['threshold']=='0.35' and router['query_understand']['task_count']==1

def test_actual_history_scope_barrier_and_memory_deleted_never_used(data,memory,monkeypatch):
    service,life,*_=build(data,monkeypatch);answer(service,data)
    item=memory.repo.save(memory.principal,memory.scope,'preference','language','Chinese')
    memory.repo.transition(memory.principal,memory.scope,item,'deleted')
    q,p=query_service(data,life,memory);claim=claims(data,life)
    with life.executing(claim,data.conversation['id']):
        r=understand(q,data,claim.run_id);assert r.memory_identities==()
        other=Scope.from_ids([str(uuid.uuid4())])
        denied=q.understand('它的成本是多少？',conversation=data.conversation['id'],scope=other,run=claim.run_id,
            cloud_allowed_by_kb={k:True for k in other.knowledge_base_ids})
        assert denied.needs_clarification and len(p.calls)==1
        data.store.complete_run(claim.run_id,'failed','SIMULATED_QUERY_TEST_ONLY')

def test_real_pg_finish_failure_retains_dispatch_and_unknown(data,monkeypatch):
    service,life,*_=build(data,monkeypatch);answer(service,data)
    q,p=query_service(data,life);claim=claims(data,life)
    monkeypatch.setattr(q.attempts,'finish',lambda *a:(_ for _ in ()).throw(OSError('do not echo')))
    with life.executing(claim,data.conversation['id']):
        u=understand(q,data,claim.run_id);assert u.reason=='QUERY_FAILURE_PERSISTENCE_UNKNOWN'
        assert rows(data,'SELECT reservation_state FROM model_calls WHERE run_id=:id',id=claim.run_id)==[{'reservation_state':'unknown'}]
        q.attempts=PostgresQueryAttemptStore(data.engine)
        assert understand(q,data,claim.run_id).reason=='QUERY_ATTEMPT_ALREADY_CONSUMED' and len(p.calls)==1
        data.store.complete_run(claim.run_id,'failed','SIMULATED_QUERY_TEST_ONLY')

def test_default_root_no_source_claim_then_trusted_model_can_backfill(data,memory,monkeypatch,tmp_path):
    m=memory
    original,*_=build(data,monkeypatch);run=answer(original,data).run_id
    m.repo.settings(m.principal,{'write_mode':'auto'})
    c=build_container(Settings(database_url=str(data.engine.url.render_as_string(hide_password=False)),
        storage_root=tmp_path,memory_local_principal=m.principal),model=None)
    assert c.memory_service.after_completed(data.conversation['id'],m.scope) is None
    assert rows(data,'SELECT count(*) AS n FROM memory_extracted_runs WHERE subject_id=:id',id=m.principal)==[{'n':0}]
    assert rows(data,'SELECT count(*) AS n FROM memory_extraction_jobs WHERE subject_id=:id',id=m.principal)==[{'n':0}]
    assert c.memory_service.settings()['automatic_extraction']=='MODEL_IDENTITY_NOT_CONFIGURED'
    job=m.service.schedule(data.conversation['id'],m.scope)
    assert rows(data,'SELECT run_id FROM memory_extracted_runs WHERE job_id=:id',id=job)[0]['run_id']==uuid.UUID(run)
    assert m.p.calls==[]
    c.engine.dispose()

def test_document_limited_memory_api_read_confirm_delete_isolated(data,memory):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    m=memory
    docs=rows(data,'SELECT id FROM documents WHERE knowledge_base_id=:id',id=data.kb)
    assert docs
    doc=str(docs[0]['id']);A=Scope.from_ids([data.kb],[doc]);B=Scope.from_ids([data.kb],[])
    item=m.repo.save(m.principal,A,'fact','document','Document A fact')
    assert m.repo.list_items(m.principal,B)==[]
    app=create_app(container=SimpleNamespace(settings=Settings(),store=data.store,memory_service=m.service,langfuse=None))
    payload={'knowledge_base_scope':[data.kb],'document_scope':[doc]}
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1000)) as client:
        assert client.post('/api/v1/memory/list',json=payload).json()['data'][0]['id']==item
        assert client.post('/api/v1/memory/list',json={**payload,'document_scope':[]}).json()['data']==[]
        assert client.post(f'/api/v1/memory/items/{item}/delete',json={**payload,'document_scope':[]}).status_code==409
        assert client.post(f'/api/v1/memory/items/{item}/delete',json=payload).status_code==200
    assert m.repo.list_items(m.principal,A,active_only=True)==[]
