"""REAL isolated PG/Redis, SIMULATED models and protocol token units."""
import copy
import uuid
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from backend.tests.test_p55a_lifecycle_integration import data, build, answer, rows, connect
from backend.tests.test_p55b_context_window import Provider, window
from backend.app.application.context_window import ContextManager, SimulatedCompactor, ContextDenied, identity
from backend.app.adapters.postgres.context_checkpoint import PostgresContextStore
from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.budget import PostgresBudgetGate
from backend.app.ports.run_lifecycle import current_execution, execution_owner
from backend.app.domain.scope import Scope
from backend.tests.test_langchain_agent import ScriptedChatModel
from backend.tests.test_p5_router_generation import GOOD


class CostModel(ScriptedChatModel):
    def _generate(self,*args,**kwargs):
        result=super()._generate(*args,**kwargs)
        for call in result.generations[0].message.tool_calls:
            call['args']['query']='A 的成本是多少？'
        return result


def configured(data, monkeypatch, size=20000, result=None):
    service,life,chain,*_=build(data,monkeypatch)
    store=PostgresContextStore(data.engine)
    provider=Provider(result)
    gate=PostgresBudgetGate(data.engine,1000000000)
    windows={'chat_cheap':window(size),'chat_expensive':window(size*2,'SIMULATED-expensive')}
    manager=ContextManager(data.store,store,windows,
        compactor=SimulatedCompactor(provider,gate,reserve_microunits=7,output_tokens=32))
    return service,life,chain,manager,store,provider,gate


class Execution:
    def __init__(self,life,data):
        self.life,self.data=life,data
        self.claim=life.repository.claim_run(data.conversation['id'],[data.kb],[],'A 的成本是多少？','smart',str(uuid.uuid4()))
    def __enter__(self):
        self.token=current_execution.set((self.claim,self.life,Event()))
        self.owner=execution_owner.set((self.claim.run_id,self.claim.owner))
        return self.claim.run_id
    def __exit__(self,*exc):
        self.life.repository.fail_run(self.claim.run_id,self.claim.owner)
        current_execution.reset(self.token);execution_owner.reset(self.owner)


def long_history(data,service,n=8):
    ids=[answer(service,data).run_id for _ in range(n)]
    # Task-owned SIMULATED completed conversations; repeat already supported
    # assertions to exercise history size. No old project/user data is edited.
    with data.engine.begin() as c:
        for run in ids:
            c.execute(text("UPDATE conversation_messages SET content=:answer WHERE run_id=:run AND role='assistant'"),
                # Outbound history now removes old citation labels. 24 repeats
                # retain the original trigger: SIMULATED full 3312 > 3000,
                # compaction 2806 <= 3000, without changing any model window.
                dict(run=run,answer=(GOOD+'\n')*24))
    return ids


def messages(m,data,run):
    return m.smart_messages(conversation=data.conversation['id'],scope=Scope.from_ids([data.kb]),run=run,
        role='chat_cheap',system='SYSTEM',current=[{'role':'user','content':'CURRENT'}],tools=[],output_tokens=100)


def test_quick_real_committed_pairs_order_scope_and_quote_identity(data,monkeypatch):
    service,life,chain,m,*_=configured(data,monkeypatch)
    ids=[answer(service,data).run_id for _ in range(6)]
    with Execution(life,data) as run:
        before=rows(data,'SELECT run_id,label,quote,quote_sha256,version_id,locator FROM answer_evidence WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY run_id,label',ids=ids)
        turns=m.history(data.conversation['id'],Scope.from_ids([data.kb]),run,5)
        assert [t['run_id'] for t in turns]==ids[-5:]
        assert all(t['q0']=='A 的成本是多少？' and t['answer']==GOOD and t['citations']==['E1'] for t in turns)
        assert m.history(str(uuid.uuid4()),Scope.from_ids([data.kb]),run,5)==[]
        assert m.history(data.conversation['id'],Scope.from_ids([str(uuid.uuid4())]),run,5)==[]
        assert m.history(data.conversation['id'],Scope.from_ids([data.kb],[data.doc]),run,5)==[]
        assert before==rows(data,'SELECT run_id,label,quote,quote_sha256,version_id,locator FROM answer_evidence WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY run_id,label',ids=ids)


def test_quick_real_normal_entry_uses_shared_history_not_retrieval_evidence(data,monkeypatch):
    service,life,chain,m,*_=configured(data,monkeypatch)
    first=answer(service,data)
    chain.context_manager=m
    # Exact production-role identities, synthetic capacity/counting only.
    from dataclasses import replace
    m.windows={role:replace(m.windows[role],provider=binding.provider,model=binding.model) for role,binding in chain.generation_roles.items()}
    result=answer(service,data)
    assert result.error_code is None and result.citations==('E1',)
    assert rows(data,'SELECT count(*) AS n FROM answer_evidence WHERE run_id=:id',id=result.run_id)==[{'n':1}]
    assert first.run_id!=result.run_id


def test_checkpoint_real_persistence_restart_time_version_boundary_no_repeat(data,monkeypatch):
    service,life,chain,m,store,p,gate=configured(data,monkeypatch,size=3000)
    ids=long_history(data,service)
    with Execution(life,data) as run:
        result=messages(m,data,run)
        assert len(p.calls)==1
        cp=rows(data,'SELECT * FROM context_checkpoints WHERE run_id=:id',id=run)[0]
        assert cp['kind']=='SESSION' and len(cp['covered'])==6
        assert [x['run_id'] for x in cp['covered']]==ids[:-2]
        fresh=ContextManager(data.store,PostgresContextStore(data.engine),m.windows,compactor=m.compactor)
        assert messages(fresh,data,run)==result and len(p.calls)==1
        attempt=rows(data,'SELECT * FROM context_compaction_attempts WHERE run_id=:id',id=run)[0]
        assert attempt['purpose']=='context_compaction' and attempt['state']=='COMPLETED'
        assert rows(data,'SELECT reservation_state,purpose FROM model_calls WHERE id=:id',id=attempt['budget_reservation_id'])==[{'reservation_state':'unknown','purpose':'context_compaction'}]
        assert rows(data,'SELECT count(*) AS n FROM rag_model_attempts WHERE run_id=:id',id=run)==[{'n':0}]
    # A new logical Run and a newly constructed PG store recover the independent
    # committed compaction even when its producer's answer later failed.
    with Execution(life,data) as next_run:
        fresh=ContextManager(data.store,PostgresContextStore(data.engine),m.windows,compactor=m.compactor)
        assert messages(fresh,data,next_run)==result and len(p.calls)==1


@pytest.mark.parametrize('result',[{'text':'summary','finish_reason':'length'},TimeoutError('not logged')])
def test_compaction_real_unknown_and_duplicate_do_not_send_again(data,monkeypatch,result):
    service,life,chain,m,store,p,gate=configured(data,monkeypatch,size=3000,result=result)
    long_history(data,service)
    with Execution(life,data) as run:
        with pytest.raises(ContextDenied):messages(m,data,run)
        with pytest.raises(ContextDenied,match='ALREADY_CONSUMED'):messages(m,data,run)
        assert len(p.calls)==1
        assert rows(data,'SELECT state FROM context_compaction_attempts WHERE run_id=:id',id=run)==[{'state':'UNKNOWN'}]
        assert rows(data,'SELECT reservation_state FROM model_calls WHERE run_id=:id',id=run)==[{'reservation_state':'unknown'}]
        assert rows(data,'SELECT * FROM context_checkpoints WHERE run_id=:id',id=run)==[]


def test_checkpoint_real_storage_exception_preserves_attempt_budget_and_originals(data,monkeypatch):
    service,life,chain,m,store,p,gate=configured(data,monkeypatch,size=3000)
    ids=long_history(data,service)
    before=rows(data,'SELECT id,content,run_id FROM conversation_messages WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY id',ids=ids)
    monkeypatch.setattr(store,'save_checkpoint',lambda *a: (_ for _ in ()).throw(OSError('private')))
    with Execution(life,data) as run:
        with pytest.raises(ContextDenied,match='INTERNAL_ERROR'):messages(m,data,run)
        assert rows(data,'SELECT state FROM context_compaction_attempts WHERE run_id=:id',id=run)==[{'state':'COMPLETED'}]
        assert rows(data,'SELECT reservation_state FROM model_calls WHERE run_id=:id',id=run)==[{'reservation_state':'unknown'}]
        assert before==rows(data,'SELECT id,content,run_id FROM conversation_messages WHERE run_id=ANY(CAST(:ids AS uuid[])) ORDER BY id',ids=ids)


def test_compaction_real_atomic_claim_concurrent_one_owner(data,monkeypatch):
    service,life,chain,m,store,p,gate=configured(data,monkeypatch)
    scope=Scope.from_ids([data.kb])
    with Execution(life,data) as run:
        claim=current_execution.get();attempt=identity({'unique':run})
        def worker(_):
            token=current_execution.set(claim)
            try:
                try:store.claim_compaction(attempt,run,data.conversation['id'],scope,m.window('chat_cheap'),[{'run_id':'old'}],'SESSION');return 'created'
                except ContextDenied:return 'denied'
            finally:current_execution.reset(token)
        with ThreadPoolExecutor(max_workers=3) as pool: outcomes=list(pool.map(worker,range(3)))
        assert outcomes.count('created')==1 and outcomes.count('denied')==2
        assert rows(data,'SELECT count(*) AS n FROM context_compaction_attempts WHERE run_id=:id',id=run)==[{'n':1}]


def test_smart_normal_entry_real_protocol_and_history_rebuild(data,monkeypatch):
    service,life,chain,m,store,p,gate=configured(data,monkeypatch,size=30000)
    service.smart_agent=LangChainAgentAdapter(CostModel(final_answer=GOOD),context_manager=m,context_role='chat_cheap')
    service.agent_trace_store=PostgresAgentRepository(data.engine)
    first=service.answer(data.conversation,'A 的成本是多少？','smart',request_id=str(uuid.uuid4()))
    assert first.error_code is None
    protocol=rows(data,'SELECT messages,sha256 FROM context_run_protocol WHERE run_id=:id',id=first.run_id)[0]
    assert [x['role'] for x in protocol['messages']]==['assistant','tool']
    assert protocol['sha256']==identity(protocol['messages'])
    service.smart_agent=LangChainAgentAdapter(CostModel(final_answer=GOOD),context_manager=m,context_role='chat_cheap')
    second=service.answer(data.conversation,'A 的成本是多少？','smart',request_id=str(uuid.uuid4()))
    assert second.error_code is None and second.citations==('E1',)
    assert rows(data,'SELECT status FROM agent_runs WHERE id=:id',id=second.run_id)==[{'status':'completed'}]


def test_smart_rag_unhandled_final_commit_exception_closes_only_own_agent(data,monkeypatch):
    service,life,chain,m,store,p,gate=configured(data,monkeypatch,size=30000)
    service.smart_agent=LangChainAgentAdapter(CostModel(final_answer=GOOD),context_manager=m,context_role='chat_cheap')
    service.agent_trace_store=PostgresAgentRepository(data.engine)
    identity_run=str(uuid.uuid4())
    monkeypatch.setattr(data.store,'finalize_answer',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('private')))
    with pytest.raises(RuntimeError):service.answer(data.conversation,'A 的成本是多少？','smart',request_id=identity_run)
    assert rows(data,'SELECT status,error_code FROM rag_runs WHERE id=:id',id=identity_run)==[{'status':'failed','error_code':'RUN_EXECUTION_FAILED'}]
    assert rows(data,'SELECT status,error_code FROM agent_runs WHERE id=:id',id=identity_run)==[{'status':'failed','error_code':'RUN_EXECUTION_FAILED'}]
    assert rows(data,'SELECT state FROM rag_run_leases WHERE run_id=:id',id=identity_run)==[{'state':'FAILED'}]


def test_protocol_real_append_survives_compaction_and_conflict_rejected(data,monkeypatch):
    from backend.tests.test_p55b_context_window import pair
    service,life,chain,m,store,*_=configured(data,monkeypatch)
    with Execution(life,data) as run:
        store.save_protocol(run,pair('a'));store.save_protocol(run,pair('b'));store.save_protocol(run,pair('b'))
        assert rows(data,'SELECT messages FROM context_run_protocol WHERE run_id=:id',id=run)==[{'messages':pair('a')+pair('b')}]
        with pytest.raises(ContextDenied,match='ID_CONFLICT'):store.save_protocol(run,pair('b','different'))
