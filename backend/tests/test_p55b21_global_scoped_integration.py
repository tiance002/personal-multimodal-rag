"""REAL isolated PostgreSQL/Redis; all model transports/counters SIMULATED."""
import uuid,json,copy,time
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from backend.tests.test_p55b2_memory_integration import data,memory,prepare
from backend.tests.test_p55a_lifecycle_integration import build,answer,rows
from backend.tests.test_p55b_context_window import window
from backend.tests.test_p55c_query_integration import query_service,claims,understand
from backend.app.application.memory import MemoryRetriever
from backend.app.application.context_window import ContextManager,ContextDenied
from backend.app.adapters.postgres.context_checkpoint import PostgresContextStore
from backend.app.adapters.postgres.memory import PostgresMemoryRepository
from backend.app.ports.memory import MemoryDenied,item_value
from backend.app.domain.scope import Scope

GLOBAL=Scope.from_ids([])
def save_global(m,content='GLOBAL-CHINESE [E1]',key='language'):
    return m.repo.save(m.principal,GLOBAL,'preference',key,content,scope_type='GLOBAL')
def other_kb(data):
    return data.store.create_knowledge_base('SIMULATED-B-'+str(uuid.uuid4()),cloud_allowed=True)['id']

def test_global_cross_kb_scoped_and_document_never_cross(data,memory):
    m=memory;B=Scope.from_ids([other_kb(data)])
    g=save_global(m);s=m.repo.save(m.principal,m.scope,'fact','contract','Contract cost 987654')
    a=Scope.from_ids([data.kb],[data.doc]);doc=str(uuid.uuid4())
    with data.engine.begin() as c:
        c.execute(text("INSERT INTO documents(id,knowledge_base_id,file_name,media_type,original_size) VALUES (:id,:kb,'SIMULATED-B','text/plain',0)"),dict(id=doc,kb=data.kb))
    d=m.repo.save(m.principal,a,'fact','document','Document A cost 654321')
    r=MemoryRetriever(m.repo,m.principal)
    assert {x['id'] for x in r.recall('cost',B)}=={g}
    assert {x['id'] for x in r.recall('cost',m.scope)}=={g,s}
    assert {x['id'] for x in r.recall('cost',a)}=={g,d}
    assert {x['id'] for x in r.recall('cost',Scope.from_ids([data.kb],[doc]))}=={g}
    with pytest.raises(MemoryDenied):m.repo.recall_items(str(uuid.uuid4()),B)

def test_scoped_priority_update_delete_global_unchanged(data,memory):
    m=memory;g=save_global(m);B=Scope.from_ids([other_kb(data)])
    before=rows(data,'SELECT * FROM long_term_memory_items WHERE id=:id',id=g)
    s=m.repo.save(m.principal,m.scope,'preference','language','SCOPED-English')
    r=MemoryRetriever(m.repo,m.principal,token_budget=1000)
    assert [x['id'] for x in r.recall('language',m.scope)]==[s]
    assert [x['id'] for x in r.recall('language',B)]==[g]
    prompt=r.quick_context('SYSTEM\nQuestion: language\nEvidence: current [E1]',scope=m.scope,windows=[window()],output_tokens=100)
    new=m.repo.save(m.principal,m.scope,'preference','language','SCOPED-French',target=s)
    with pytest.raises(ContextDenied,match='REVOKED'):r.validate_frozen(prompt,m.scope)
    assert r.recall('language',m.scope)[0]['id']==new
    m.repo.transition(m.principal,m.scope,new,'deleted')
    assert r.recall('language',m.scope)[0]['id']==g
    assert rows(data,'SELECT * FROM long_term_memory_items WHERE id=:id',id=g)==before

@pytest.mark.parametrize('kind,origin',[('fact','explicit'),('task','explicit'),('preference','extracted')])
def test_global_document_fact_or_auto_promotion_rejected(data,memory,kind,origin):
    m=memory
    with pytest.raises(MemoryDenied):
        with data.engine.begin() as c:
            m.repo._write(c,GLOBAL,item_value(kind,'invalid','Contract amount 100'),origin,[],scope_type='GLOBAL')
    assert m.repo.list_items(m.principal,GLOBAL,scope_type='GLOBAL')==[]

def test_global_api_local_principal_scope_type_crud_and_source(data,memory):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    from backend.app.config import Settings
    m=memory;app=create_app(container=SimpleNamespace(settings=Settings(),store=data.store,memory_service=m.service,langfuse=None))
    g={'scope_type':'GLOBAL','knowledge_base_scope':[],'document_scope':[]}
    payload={**g,'kind':'preference','fact_key':'language','content':'Chinese'}
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',1000)) as c:
        assert c.post('/api/v1/memory/items',json={**payload,'knowledge_base_scope':[data.kb]}).status_code==409
        assert c.post('/api/v1/memory/items',json={**payload,'kind':'fact'}).status_code==409
        assert c.post('/api/v1/memory/list',json={'knowledge_base_scope':[]}).status_code==409
        assert c.post('/api/v1/memory/items',json={**payload,'subject_id':str(uuid.uuid4())}).status_code==422
        res=c.post('/api/v1/memory/items',json=payload);assert res.status_code==201
        item=res.json()['data']['id'];listed=c.post('/api/v1/memory/list',json=g).json()['data']
        assert listed[0]['scope_type']=='GLOBAL' and listed[0]['sources'][0]['source']['type']=='explicit_owner_action'
        assert c.patch('/api/v1/memory/items/'+item,json={**payload,'content':'English'}).status_code==200
        active=[x for x in c.post('/api/v1/memory/list',json=g).json()['data'] if x['status']=='active'][0]
        assert c.post('/api/v1/memory/items/'+active['id']+'/delete',json=g).status_code==200
    with TestClient(app,base_url='http://127.0.0.1',client=('192.0.2.7',1000)) as c:
        assert c.post('/api/v1/memory/list',json=g).status_code==403

def test_auto_pending_confirm_stays_scoped_across_kb(data,memory,monkeypatch):
    m=memory;B=Scope.from_ids([other_kb(data)]);g=save_global(m)
    ids,job=prepare(data,m,monkeypatch,1);m.service.extract_once(job)
    pending=m.repo.list_items(m.principal,m.scope)[0]
    assert pending['scope_type']=='SCOPED' and pending['status']=='pending'
    r=MemoryRetriever(m.repo,m.principal)
    assert r.recall('language',m.scope)[0]['id']==g
    m.repo.transition(m.principal,m.scope,pending['id'],'active')
    assert r.recall('language',m.scope)[0]['id']==pending['id']
    assert r.recall('language',B)[0]['id']==g
    assert rows(data,'SELECT purpose,reservation_state FROM model_calls WHERE id=(SELECT budget_reservation_id FROM memory_extraction_jobs WHERE id=:id)',id=job)==[dict(purpose='memory_extraction',reservation_state='unknown')]
    with pytest.raises(MemoryDenied):m.service.extract_once(job)
    assert len(m.p.calls)==1

@pytest.mark.parametrize('mode',['quick','smart'])
def test_normal_quick_smart_union_evidence_and_revoke(data,memory,monkeypatch,mode):
    from backend.tests.test_p55b_context_integration import CostModel
    from backend.tests.test_p5_router_generation import GOOD
    from backend.app.application.langchain_agent import LangChainAgentAdapter
    from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
    m=memory;service,life,chain,calls,*_=build(data,monkeypatch)
    other=Scope.from_ids([other_kb(data)])
    m.repo.save(m.principal,other,'fact','contract','FOREIGN-CONTRACT-987654')
    g=save_global(m);r=MemoryRetriever(m.repo,m.principal,token_budget=1000)
    windows={role:replace(window(30000),provider=b.provider,model=b.model) for role,b in chain.generation_roles.items()}
    manager=ContextManager(data.store,PostgresContextStore(data.engine),windows,memory_retriever=r)
    if mode=='quick':chain.context_manager=manager
    else:
        manager.windows={'local_chat':window(30000)}
        class ObservedModel(CostModel):
            observed: list = []
            def _generate(self,messages,*a,**kw):
                self.observed.append(str(messages))
                return super()._generate(messages,*a,**kw)
        model=ObservedModel(final_answer=GOOD)
        service.smart_agent=LangChainAgentAdapter(model,context_manager=manager)
        service.agent_trace_store=PostgresAgentRepository(data.engine)
    result=service.answer(data.conversation,'A 的成本是多少？',mode,request_id=str(uuid.uuid4()))
    assert result.error_code is None and result.citations==('E1',)
    if mode=='quick':
        prompt=calls[-1]['messages'][0]['content']
        assert 'GLOBAL-CHINESE' in prompt and 'FOREIGN-CONTRACT' not in prompt
        assert 'GLOBAL-CHINESE [E1]' not in prompt
        assert rows(data,'SELECT role FROM rag_model_attempts WHERE run_id=:id',id=result.run_id)==[{'role':'chat_cheap'}]
    else:
        assert model.observed and all('GLOBAL-CHINESE' in x and 'FOREIGN-CONTRACT' not in x for x in model.observed)
        assert all('GLOBAL-CHINESE [E1]' not in x for x in model.observed)
    assert 'GLOBAL-CHINESE' not in str(rows(data,'SELECT quote FROM answer_evidence WHERE run_id=:id',id=result.run_id))
    m.repo.settings(m.principal,{'read_enabled':False});assert r.recall('language',m.scope)==[]
    m.repo.settings(m.principal,{'read_enabled':True})
    m.repo.transition(m.principal,GLOBAL,g,'deleted',scope_type='GLOBAL')
    assert r.recall('language',m.scope)==[]
    if mode=='quick':
        with pytest.raises(ContextDenied):r.validate_frozen(prompt,m.scope)
    else:
        current=[{'role':'user','content':'CURRENT'}]
        assert manager.smart_messages(conversation=data.conversation['id'],scope=m.scope,run=result.run_id,
            role='local_chat',system='SYSTEM',current=current,tools=[],output_tokens=100)[-1]==current[0]
        assert 'GLOBAL-CHINESE' not in str(manager.fit_live([{'name':'long_term_memory','role':'user','content':'old'},*current],
            conversation=data.conversation['id'],scope=m.scope,run=result.run_id,role='local_chat',system='SYSTEM',tools=[],output_tokens=100))

def test_query_understand_union_cannot_add_memory_fact_or_citation(data,memory,monkeypatch):
    m=memory;service,life,*_=build(data,monkeypatch);previous=answer(service,data)
    g=save_global(m);s=m.repo.save(m.principal,m.scope,'interest','tools','Retrieval interest')
    q,p=query_service(data,life,m);claim=claims(data,life)
    with life.executing(claim,data.conversation['id']):
        u=understand(q,data,claim.run_id)
        assert {x[0] for x in u.memory_identities}=={g,s}
        assert u.rewrite_query=='A 的成本是多少？' and u.history_run_ids==(previous.run_id,)
        assert '[E1]' not in p.calls[0][0][-1]['content']
        assert len(p.calls)==1 and understand(q,data,claim.run_id).reason=='QUERY_ATTEMPT_ALREADY_CONSUMED'
        assert rows(data,'SELECT purpose,reservation_state FROM model_calls WHERE run_id=:id',id=claim.run_id)==[dict(purpose='query_understand',reservation_state='unknown')]
        data.store.complete_run(claim.run_id,'failed','SIMULATED_QUERY_TEST_ONLY')

def test_checkpoint_excludes_global_and_revoke_reloads(data,memory,monkeypatch):
    from backend.tests.test_p55b_context_integration import configured,long_history,Execution,messages
    m=memory;g=save_global(m,'GLOBAL-CHECKPOINT-SENTINEL')
    service,life,chain,manager,store,p,gate=configured(data,monkeypatch,size=3000)
    manager.memory_retriever=MemoryRetriever(m.repo,m.principal,token_budget=1000)
    long_history(data,service)
    with Execution(life,data) as run:
        assembled=messages(manager,data,run)
        assert 'GLOBAL-CHECKPOINT-SENTINEL' in str(assembled)
        assert len(p.calls)==1 and 'GLOBAL-CHECKPOINT-SENTINEL' not in str(p.calls)
        assert 'GLOBAL-CHECKPOINT-SENTINEL' not in str(rows(data,'SELECT summary FROM context_checkpoints WHERE run_id=:id',id=run))
        m.repo.transition(m.principal,GLOBAL,g,'deleted',scope_type='GLOBAL')
        assert 'GLOBAL-CHECKPOINT-SENTINEL' not in str(messages(manager,data,run)) and len(p.calls)==1

def test_global_redis_expiry_fresh_process_and_restart_seed(data,memory,monkeypatch):
    import subprocess,sys
    m=memory;service,life,*_=build(data,monkeypatch);result=answer(service,data)
    g=save_global(m);B=other_kb(data)
    key=life.streams._key(data.conversation['id'],result.run_id)
    assert life.streams.client.exists(key);time.sleep(1.2);assert not life.streams.client.exists(key)
    assert MemoryRetriever(PostgresMemoryRepository(data.engine,m.principal),m.principal).recall('language',Scope.from_ids([B]))[0]['id']==g
    script=Path(__file__).resolve().parents[2]/'scripts/verify_p55b2_memory_isolated.py'
    proc=subprocess.run([sys.executable,'-B','-X','utf8',str(script),'--memory-recovery-worker',m.principal,B],capture_output=True,text=True,timeout=30)
    assert proc.returncode==0
    read=json.loads(proc.stdout.strip().splitlines()[-1]);assert read['ids']==[g] and read['commercial_calls']==0
    # Immutable seed for separately authorized test-only cluster restart proof.
    out=Path(__file__).resolve().parents[2]/'var/reports/p55b21-global-scoped-r1'
    name='restart-seed-'+g+'.json'
    with (out/name).open('x') as f:json.dump({'principal':m.principal,'kb':B,'ids':[g]},f)

def test_real_constraints_identity_and_old_scoped_default(data,memory):
    m=memory;item=m.repo.save(m.principal,m.scope,'preference','legacy-default','old default')
    assert rows(data,'SELECT scope_type FROM long_term_memory_items WHERE id=:id',id=item)==[{'scope_type':'SCOPED'}]
    assert rows(data,'SELECT version_num FROM alembic_version')==[{'version_num':'0021_global_scoped_memory'}]
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with data.engine.begin() as c:c.execute(text("UPDATE long_term_memory_items SET scope_type='GLOBAL' WHERE id=:id"),dict(id=item))
    assert rows(data,'SELECT scope_type FROM long_term_memory_items WHERE id=:id',id=item)==[{'scope_type':'SCOPED'}]

def test_corrupt_global_hash_rejected_and_identity_cannot_be_moved(data,memory):
    m=memory;g=save_global(m);r=MemoryRetriever(m.repo,m.principal)
    with pytest.raises(MemoryDenied):m.repo.save(m.principal,m.scope,'preference','language','x',target=g)
    with data.engine.begin() as c:c.execute(text("UPDATE long_term_memory_items SET content_hash=:hash WHERE id=:id"),dict(hash='0'*64,id=g))
    with pytest.raises(MemoryDenied,match='INTEGRITY_INVALID'):r.recall('language',m.scope)
