"""REAL isolated PostgreSQL/Redis; all model dispatch SIMULATED."""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from backend.tests.test_p55a_lifecycle_integration import data,build,answer,rows
from backend.tests.test_p55b_context_window import window
from backend.tests.test_p55b_context_integration import Execution
from backend.app.adapters.postgres.memory import PostgresMemoryRepository
from backend.app.application.memory import MemoryService,MemoryRetriever
from backend.app.ports.memory import MemoryDenied,scope_value
from backend.app.application.budget import PostgresBudgetGate
from backend.app.application.context_window import ContextManager
from backend.app.adapters.postgres.context_checkpoint import PostgresContextStore
from backend.app.domain.scope import Scope
from backend.app.ports.model_usage import capture_usage

class Extractor:
    execution_kind='SIMULATED'
    def __init__(self,result=None):self.calls=[];self.result=result
    def extract(self,prompt,**kwargs):
        self.calls.append((prompt,kwargs))
        if isinstance(self.result,Exception):raise self.result
        return self.result or dict(finish_reason='stop',memories=[dict(kind='preference',fact_key='language',content='Prefer Chinese',source_index=0)],usage={'prompt_tokens':13,'completion_tokens':8})

@pytest.fixture
def memory(data):
    principal=str(uuid.uuid4());scope=Scope.from_ids([data.kb])
    repo=PostgresMemoryRepository(data.engine,principal)
    repo.settings(principal,dict(read_enabled=True,write_mode='explicit_only'))
    p=Extractor();gate=PostgresBudgetGate(data.engine,1000000000)
    service=MemoryService(repo,principal,extractor=p,budget_gate=gate,window=window(),reserve_microunits=7)
    return SimpleNamespace(repo=repo,principal=principal,scope=scope,p=p,gate=gate,service=service)

def prepare(data,m,monkeypatch,n=2):
    service,*_=build(data,monkeypatch)
    ids=[answer(service,data).run_id for _ in range(n)]
    m.repo.settings(m.principal,dict(write_mode='auto'))
    job=m.service.schedule(data.conversation['id'],m.scope)
    return ids,job

def test_explicit_cross_conversation_read_disable_delete_and_restart(data,memory):
    m=memory;item=m.repo.save(m.principal,m.scope,'preference','language','Prefer Chinese [E1]')
    data.store.create_conversation([data.kb])
    r=MemoryRetriever(m.repo,m.principal,token_budget=1000)
    assert r.recall('language',m.scope)[0]['id']==item
    m.repo.settings(m.principal,dict(read_enabled=False));assert r.recall('language',m.scope)==[]
    m.repo.settings(m.principal,dict(read_enabled=True));m.repo.transition(m.principal,m.scope,item,'deleted')
    assert MemoryRetriever(PostgresMemoryRepository(data.engine,m.principal),m.principal).recall('language',m.scope)==[]
    assert rows(data,'SELECT content,status FROM long_term_memory_items WHERE id=:id',id=item)==[dict(content='Prefer Chinese [E1]',status='deleted')]

def test_explicit_fact_dedup_update_versions_sources_and_subject_isolation(data,memory):
    m=memory
    first=m.repo.save(m.principal,m.scope,'fact','work','Work on RAG')
    assert m.repo.save(m.principal,m.scope,'fact','work','Work on RAG')==first
    second=m.repo.save(m.principal,m.scope,'fact','work','Work on retrieval',target=first)
    items=m.repo.list_items(m.principal,m.scope)
    assert [(r['status'],r['version']) for r in items]==[('superseded',1),('active',2)]
    assert items[1]['replaces_id']==first and all(len(r['sources'])==1 for r in items)
    assert m.repo.list_items(m.principal,Scope.from_ids([str(uuid.uuid4())]))==[]
    other=str(uuid.uuid4());other_repo=PostgresMemoryRepository(data.engine,other)
    assert other_repo.list_items(other,m.scope)==[]
    with pytest.raises(MemoryDenied,match='PRINCIPAL_DENIED'):m.repo.list_items(other,m.scope)
    with pytest.raises(MemoryDenied,match='NOT_FOUND'):other_repo.transition(other,m.scope,second,'deleted')

def test_auto_batches_committed_pairs_pending_confirmation_independent_usage(data,memory,monkeypatch):
    m=memory;ids,job=prepare(data,m,monkeypatch)
    original=rows(data,'SELECT id,content,run_id FROM conversation_messages WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY id',ids=ids)
    with capture_usage() as capture:d=m.service.extract_once(job)
    assert len(m.p.calls)==1 and len(m.p.calls[0][0][1]['content'])>0
    assert d['fee']=='UNKNOWN' and capture.summary()['memory_extraction']['input_tokens']==13
    assert capture.summary()['answer']['call_count']==0
    assert rows(data,'SELECT state,purpose,jsonb_array_length(sources) AS n FROM memory_extraction_jobs WHERE id=:id',id=job)==[dict(state='COMPLETED',purpose='memory_extraction',n=2)]
    item=m.repo.list_items(m.principal,m.scope)[0]
    assert item['status']=='pending' and item['origin']=='extracted'
    assert str(item['sources'][0]['run_id'])==ids[0] and item['sources'][0]['message_id'] is not None
    assert MemoryRetriever(m.repo,m.principal).recall('language',m.scope)==[]
    m.repo.transition(m.principal,m.scope,item['id'],'active')
    assert MemoryRetriever(m.repo,m.principal).recall('language',m.scope)[0]['id']==item['id']
    assert original==rows(data,'SELECT id,content,run_id FROM conversation_messages WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY id',ids=ids)
    assert m.service.schedule(data.conversation['id'],m.scope) is None
    with pytest.raises(MemoryDenied,match='ALREADY_CONSUMED'):m.service.extract_once(job)
    assert len(m.p.calls)==1
    assert rows(data,"SELECT count(*) AS n FROM model_calls WHERE purpose='memory_extraction' AND run_id=ANY(CAST(:ids AS uuid[])) AND reservation_state='unknown'",ids=ids)==[{'n':1}]

@pytest.mark.parametrize('result',[
    {'finish_reason':'length','memories':[]},TimeoutError('never logged'),
    {'finish_reason':'stop','memories':'bad'},
    {'finish_reason':'stop','memories':[dict(kind='fact',fact_key='k',content='x',source_index=True)]},
])
def test_auto_fail_truncate_invalid_unknown_no_repeat(data,memory,monkeypatch,result):
    m=memory;ids,job=prepare(data,m,monkeypatch,1);m.p.result=result
    with pytest.raises(MemoryDenied):m.service.extract_once(job)
    with pytest.raises(MemoryDenied,match='ALREADY_CONSUMED'):m.service.extract_once(job)
    assert len(m.p.calls)==1 and m.repo.list_items(m.principal,m.scope)==[]
    assert rows(data,'SELECT state FROM memory_extraction_jobs WHERE id=:id',id=job)==[{'state':'UNKNOWN'}]
    assert rows(data,"SELECT reservation_state FROM model_calls WHERE run_id=:run AND purpose='memory_extraction'",run=ids[0])==[{'reservation_state':'unknown'}]

@pytest.mark.parametrize('state',['rejected','deleted'])
def test_rejected_or_deleted_fact_not_reextracted_from_next_batch(data,memory,monkeypatch,state):
    m=memory;prepare_ids,job=prepare(data,m,monkeypatch,1);m.service.extract_once(job)
    item=m.repo.list_items(m.principal,m.scope)[0]
    m.repo.transition(m.principal,m.scope,item['id'],state)
    prepare_ids,second=prepare(data,m,monkeypatch,1);m.service.extract_once(second)
    assert len(m.p.calls)==2
    assert [(x['id'],x['status']) for x in m.repo.list_items(m.principal,m.scope)]==[(item['id'],state)]
    assert MemoryRetriever(m.repo,m.principal).recall('language',m.scope)==[]

def test_source_changed_or_budget_denial_zero_send_and_durable_consumption(data,memory,monkeypatch):
    m=memory;ids,job=prepare(data,m,monkeypatch,1)
    with data.engine.begin() as c:c.execute(text("UPDATE conversation_messages SET content='changed SIMULATED source' WHERE run_id=:id AND role='assistant'"),dict(id=ids[0]))
    with pytest.raises(MemoryDenied,match='SOURCE_CHANGED'):m.service.extract_once(job)
    assert m.p.calls==[]
    assert rows(data,'SELECT state,diagnostics FROM memory_extraction_jobs WHERE id=:id',id=job)[0]['state']=='NOT_SENT'
    assert rows(data,"SELECT reservation_state FROM model_calls WHERE run_id=:run AND purpose='memory_extraction'",run=ids[0])==[{'reservation_state':'released'}]

def test_real_pg_concurrent_extraction_claim_one_send(data,memory,monkeypatch):
    m=memory;ids,job=prepare(data,m,monkeypatch,1)
    def worker(_):
        try:m.service.extract_once(job);return 'ok'
        except MemoryDenied:return 'denied'
    with ThreadPoolExecutor(2) as ex:out=list(ex.map(worker,range(2)))
    assert sorted(out)==['denied','ok'] and len(m.p.calls)==1
    assert rows(data,'SELECT count(*) AS n FROM memory_extracted_runs WHERE job_id=:id',id=job)==[{'n':1}]

def test_storage_failure_unknown_and_persistence_failure_not_reported_closed(data,memory,monkeypatch):
    m=memory;ids,job=prepare(data,m,monkeypatch,1)
    monkeypatch.setattr(m.repo,'finish',lambda *a:(_ for _ in ()).throw(OSError('not echoed')))
    monkeypatch.setattr(m.repo,'fail',lambda *a:(_ for _ in ()).throw(OSError('not echoed')))
    with pytest.raises(MemoryDenied,match='INTERNAL_ERROR:MEMORY_FAILURE_PERSISTENCE_UNKNOWN'):m.service.extract_once(job)
    assert len(m.p.calls)==1 and m.repo.list_items(m.principal,m.scope)==[]
    assert rows(data,'SELECT state FROM memory_extraction_jobs WHERE id=:id',id=job)==[{'state':'IN_PROGRESS'}]
    with pytest.raises(MemoryDenied,match='ALREADY_CONSUMED'):m.service.extract_once(job)

def test_quick_normal_integration_memory_history_and_current_evidence_keep_identity(data,memory,monkeypatch):
    m=memory;service,life,chain,calls,exp=build(data,monkeypatch)
    first=answer(service,data)
    m.repo.save(m.principal,m.scope,'preference','language','Prefer Chinese [E1]')
    windows={role:replace(window(),provider=b.provider,model=b.model) for role,b in chain.generation_roles.items()}
    chain.context_manager=ContextManager(data.store,PostgresContextStore(data.engine),windows,memory_retriever=MemoryRetriever(m.repo,m.principal,token_budget=1000))
    m.repo.settings(m.principal,dict(write_mode='auto'));service.memory_service=m.service
    result=answer(service,data)
    assert result.error_code is None and result.citations==('E1',)
    prompt=calls[-1]['messages'][0]['content']
    assert prompt.index('LONG-TERM MEMORY')<prompt.index('SHORT-TERM HISTORY')<prompt.index('Question:')<prompt.index('Evidence:')
    assert prompt.count('[E1]')>=1 and 'history/'+first.run_id+'/E1' in prompt
    assert 'Prefer Chinese [E1]' not in prompt
    assert rows(data,'SELECT count(*) AS n FROM answer_evidence WHERE run_id=:id',id=result.run_id)==[{'n':1}]
    assert rows(data,'SELECT quote,quote_sha256,version_id,locator FROM answer_evidence WHERE run_id=:id',id=first.run_id)==rows(data,'SELECT quote,quote_sha256,version_id,locator FROM answer_evidence WHERE run_id=:id',id=result.run_id)
    assert result.trace.get('memory_extraction_job') and m.p.calls==[]

def test_api_local_identity_crud_origin_and_no_client_user_selection(data,memory):
    from backend.app.main import create_app
    from backend.app.config import Settings
    from fastapi.testclient import TestClient
    container=SimpleNamespace(settings=Settings(),store=data.store,memory_service=memory.service,langfuse=None)
    app=create_app(container=container)
    scope=dict(knowledge_base_scope=[data.kb],document_scope=[])
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1000)) as client:
        assert client.post('/api/v1/memory/list',json={**scope,'subject_id':str(uuid.uuid4())}).status_code==422
        assert client.post('/api/v1/memory/list',json=scope,headers={'Origin':'https://evil.example'}).status_code==403
        payload=dict(**scope,kind='preference',fact_key='language',content='Prefer Chinese')
        response=client.post('/api/v1/memory/items',json=payload);assert response.status_code==201
        item=response.json()['data']['id']
        assert client.post('/api/v1/memory/list',json=scope).json()['data'][0]['id']==item
        assert client.patch('/api/v1/memory/items/'+item,json={**payload,'content':'Prefer English'}).status_code==200
        active=[r for r in client.post('/api/v1/memory/list',json=scope).json()['data'] if r['status']=='active'][0]
        assert client.post('/api/v1/memory/items/'+active['id']+'/delete',json=scope).status_code==200
    with TestClient(app,base_url='http://127.0.0.1',client=('192.0.2.5',1000)) as remote:
        assert remote.get('/api/v1/memory/settings').status_code==403

def test_only_completed_scope_bound_sources_are_scheduled(data,memory,monkeypatch):
    m=memory;ids,job=prepare(data,m,monkeypatch,1)
    service,life,chain,*_=build(data,monkeypatch);answer(service,data)
    with pytest.raises(MemoryDenied,match='SOURCE_SCOPE_DENIED'):m.service.schedule(data.conversation['id'],Scope.from_ids([str(uuid.uuid4())]))
    with data.engine.begin() as c:
        c.execute(text("UPDATE rag_runs SET status='failed',error_code='SIMULATED' WHERE conversation_id=:id AND id<>:good"),dict(id=data.conversation['id'],good=ids[0]))
    assert m.service.schedule(data.conversation['id'],m.scope) is None

def test_zero_budget_not_sent_consumed_and_no_auto_retry(data,memory,monkeypatch):
    m=memory;ids,job=prepare(data,m,monkeypatch,1)
    m.service.budget=PostgresBudgetGate(data.engine,0)
    with pytest.raises(MemoryDenied):m.service.extract_once(job)
    assert m.p.calls==[]
    row=rows(data,'SELECT state,diagnostics,budget_reservation_id FROM memory_extraction_jobs WHERE id=:id',id=job)[0]
    assert row['state']=='NOT_SENT' and row['diagnostics']['fee']=='NOT_INCURRED' and row['budget_reservation_id'] is None
    with pytest.raises(MemoryDenied,match='ALREADY_CONSUMED'):m.service.extract_once(job)

def test_pending_new_fact_cannot_replace_confirmed_until_owner_confirmation(data,memory,monkeypatch):
    m=memory;first=m.repo.save(m.principal,m.scope,'preference','language','Prefer English')
    ids,job=prepare(data,m,monkeypatch,1);m.service.extract_once(job)
    assert MemoryRetriever(m.repo,m.principal).recall('language',m.scope)[0]['id']==first
    pending=[r for r in m.repo.list_items(m.principal,m.scope) if r['status']=='pending'][0]
    assert pending['replaces_id']==first and pending['version']==2
    m.repo.transition(m.principal,m.scope,pending['id'],'active')
    active=MemoryRetriever(m.repo,m.principal).recall('language',m.scope)
    assert len(active)==1 and active[0]['id']==pending['id']

def test_smart_real_normal_entry_memory_not_in_protocol_or_evidence(data,memory,monkeypatch):
    from backend.tests.test_p55b_context_integration import CostModel
    from backend.tests.test_p5_router_generation import GOOD
    from backend.app.application.langchain_agent import LangChainAgentAdapter
    from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
    m=memory;service,life,chain,*_=build(data,monkeypatch)
    answer(service,data)
    m.repo.save(m.principal,m.scope,'preference','language','MEMORY-PREFERENCE-SENTINEL [E1]')
    manager=ContextManager(data.store,PostgresContextStore(data.engine),{'local_chat':window(30000)},memory_retriever=MemoryRetriever(m.repo,m.principal,token_budget=1000))
    service.smart_agent=LangChainAgentAdapter(CostModel(final_answer=GOOD),context_manager=manager)
    service.agent_trace_store=PostgresAgentRepository(data.engine)
    result=service.answer(data.conversation,'A 的成本是多少？','smart',request_id=str(uuid.uuid4()))
    assert result.error_code is None and result.citations==('E1',)
    assert rows(data,'SELECT count(*) AS n FROM answer_evidence WHERE run_id=:id',id=result.run_id)==[{'n':1}]
    protocol=rows(data,'SELECT messages FROM context_run_protocol WHERE run_id=:id',id=result.run_id)
    assert protocol and 'MEMORY-PREFERENCE-SENTINEL' not in str(protocol)
    assert 'MEMORY-PREFERENCE-SENTINEL' not in str(rows(data,'SELECT quote FROM answer_evidence WHERE run_id=:id',id=result.run_id))

def test_quick_subject_bounds_survive_redis_expiry_and_pg_store_restart(data,memory,monkeypatch):
    import time
    m=memory;service,life,chain,*_=build(data,monkeypatch)
    result=answer(service,data)
    item=m.repo.save(m.principal,m.scope,'preference','language','Prefer Chinese')
    # Existing task cache TTL=1; expiration is independent of permanent memory.
    key=life.streams._key(data.conversation['id'],result.run_id)
    assert life.streams.client.exists(key)
    time.sleep(1.2)
    assert not life.streams.client.exists(key)
    fresh=MemoryRetriever(PostgresMemoryRepository(data.engine,m.principal),m.principal)
    assert fresh.recall('language',m.scope)[0]['id']==item

def test_fresh_guarded_process_recovers_only_pg_confirmed_memory(data,memory):
    import subprocess,sys,json
    from pathlib import Path
    m=memory
    item=m.repo.save(m.principal,m.scope,'preference','language','Prefer Chinese')
    script=Path(__file__).resolve().parents[2]/'scripts/verify_p55b2_memory_isolated.py'
    result=subprocess.run([sys.executable,'-B','-X','utf8',str(script),'--memory-recovery-worker',m.principal,data.kb],capture_output=True,text=True,timeout=30)
    assert result.returncode==0
    evidence=json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence['ids']==[item] and evidence['commercial_calls']==0
    assert evidence['guards']==dict(network=0,secret=0,subprocess=0,database=0,stdlib_socketpair=0)

def test_capacity_rejects_new_fact_but_allows_atomic_replacement(data,memory):
    from backend.app.ports.memory import item_value,identity
    import json
    m=memory;old=str(uuid.uuid4());value=item_value('fact','cap0','SIMULATED old fact')
    scope=scope_value(m.scope)
    with data.engine.begin() as c:
        for i in range(200):
            c.execute(text('''INSERT INTO long_term_memory_items(id,subject_id,scope,scope_hash,kind,fact_key,content,content_hash,origin,status,version)
                VALUES (:id,:subject,CAST(:scope AS jsonb),:scope_hash,'fact',:key,:content,:hash,'explicit','active',1)'''),
                dict(id=old if i==0 else str(uuid.uuid4()),subject=m.principal,scope=json.dumps(scope),scope_hash=identity(scope),key='cap'+str(i),content=value['content'],hash=value['content_hash']))
    with pytest.raises(MemoryDenied,match='CAPACITY_EXCEEDED'):m.repo.save(m.principal,m.scope,'fact','extra','SIMULATED extra')
    new=m.repo.save(m.principal,m.scope,'fact','cap0','SIMULATED new fact',target=old)
    assert new!=old
    assert rows(data,"SELECT count(*) AS n FROM long_term_memory_items WHERE subject_id=:id AND status='active'",id=m.principal)==[{'n':200}]
    assert rows(data,'SELECT status FROM long_term_memory_items WHERE id=:id',id=old)==[{'status':'superseded'}]
