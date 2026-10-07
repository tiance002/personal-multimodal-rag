"""SIMULATED clarification history: fake stores/SQL only, no DB/model/network."""
from types import SimpleNamespace
import pytest
from backend.app.application.answer_service import AnswerService
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.tests.test_answer_service import RecordingRunStore
from backend.tests.test_bounded_history import row, snapshot

class LinkedStore(RecordingRunStore):
    def __init__(self, cancel_commit=False):
        super().__init__(); self.links=[]; self.cancel_commit=cancel_commit
    def append_message(self, conversation_id, role, content, run_id=None):
        self.links.append((role,run_id)); super().append_message(conversation_id,role,content)
    def cancel_run(self, run_id): return False
    def finalize_answer(self, **kwargs):
        if self.cancel_commit: return False
        self.complete_run(kwargs['run_id'],'failed' if kwargs['error_code'] else 'completed',kwargs['error_code'])
        return True

def service(store):
    return AnswerService(retriever=HybridRetriever(InMemoryRetrievalRepository()), runs=store)

def test_explicit_clarification_links_user_and_saves_original_prompt_without_assistant():
    store=LinkedStore()
    result=service(store).answer({'id':'conv','knowledge_base_scope':['kb'],'document_scope':[]},'它何时巡检？')
    assert result.trace['execution_mode']=='clarification'
    assert store.links==[('user',result.run_id)]
    metadata=next(payload for _,kind,payload in store.events if kind=='run.metrics')
    assert metadata['presentation']=={'kind':'clarification','text':result.answer,'clarification_required':True}
    assert store.completed==[(result.run_id,'failed','NO_CANDIDATES')]
    assert not metadata['model_calls'] and result.citations==()

def test_plain_no_candidates_is_not_saved_as_clarification():
    store=LinkedStore(); service(store).answer({'id':'conv','knowledge_base_scope':['kb'],'document_scope':[]},'独立问题')
    assert all('presentation' not in payload for _,_,payload in store.events)

def test_cancel_race_does_not_complete_or_invent_an_assistant():
    store=LinkedStore(cancel_commit=True)
    result=service(store).answer({'id':'conv','knowledge_base_scope':['kb'],'document_scope':[]},'它何时巡检？')
    assert result.error_code=='CANCELLED' and store.completed==[]
    assert all(role!='assistant' for role,_ in store.links)

def test_failed_clarification_is_excluded_from_factual_history():
    failed=row('clarify','它何时巡检？',status='failed',error='NO_CANDIDATES',event=False)
    failed['presentation']={'kind':'clarification','text':'补充对象','clarification_required':True}
    result=snapshot([failed,row('factual','M27',age=3)])
    assert [turn['run_id'] for turn in result['turns']]==['factual']

def test_read_side_joins_exact_run_terminal_and_current_scope_no_backfill():
    statements=[]
    class Result:
        def mappings(self): return iter([])
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,statement,parameters): statements.append((str(statement),parameters)); return Result()
    store=object.__new__(PostgresKnowledgeRepository)
    store.engine=SimpleNamespace(connect=lambda:Connection())
    assert store.list_messages('conv')==[]
    sql,args=statements[0]
    for required in ['r.id=m.run_id','r.conversation_id=m.conversation_id',"r.status='failed'", "r.error_code='NO_CANDIDATES'",'r.knowledge_base_scope=c.knowledge_base_scope','r.document_scope=c.document_scope',"e.event_type='run.metrics'"]:
        assert required in sql
    assert args=={'id':'conv'} and 'INSERT' not in sql and 'q0=' not in sql
    assert "m.role='assistant' AND e.event_type='answer.completed'" in sql

def valid_row():
    return {'role':'user','run_id':'run','content':'question','citations':[],
            'presentation':{'kind':'clarification','text':'请补充对象。','clarification_required':True}}

def test_projection_preserves_prompt_and_exact_user_identity():
    result=PostgresKnowledgeRepository._message_row(valid_row())
    assert result['presentation']['text']=='请补充对象。' and result['role']=='user' and result['citations']==[]

@pytest.mark.parametrize('change',['assistant','no-run','ordinary-kind','missing-flag','false-flag','empty','nonstring','citation','malformed'])
def test_projection_rejects_incomplete_or_nonclarification_metadata(change):
    item=valid_row()
    if change=='assistant': item['role']='assistant'
    if change=='no-run': item['run_id']=None
    if change=='ordinary-kind': item['presentation']['kind']='error'
    if change=='missing-flag': del item['presentation']['clarification_required']
    if change=='false-flag': item['presentation']['clarification_required']=False
    if change=='empty': item['presentation']['text']='  '
    if change=='nonstring': item['presentation']['text']=12
    if change=='citation': item['citations']=['E1']
    if change=='malformed': item['presentation']='text'
    assert 'presentation' not in PostgresKnowledgeRepository._message_row(item)

def test_legacy_null_run_message_is_not_backfilled():
    result=PostgresKnowledgeRepository._message_row({'role':'user','content':'old','run_id':None})
    assert 'presentation' not in result and result['citations']==[]
