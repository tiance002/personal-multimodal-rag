"""Offline source replay and fake persistence, no model/DB/server."""
import copy,hashlib,json,os
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
from backend.app.application.answer_service import AnswerService
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.tests.test_original_header_boundary import original,FrozenRetrieval,NeverGenerate,QUESTION
from backend.tests.test_clarification_persistence import LinkedStore
from backend.app.application.retrieval import RetrievalItem,RetrievalResult
from backend.app.domain.models import RankedHit
from backend.app.domain.text_normalization import normalize_query

class ServiceRetrieval(FrozenRetrieval):
    def retrieve(self,scope,question,query_plan=None):
        assert set(scope.knowledge_base_ids)=={'offline-kb'} and set(scope.document_ids)=={'offline-document'}
        return RetrievalResult(query_plan or normalize_query(question),[RetrievalItem(c,RankedHit(chunk_id=c.chunk_id,rank=i)) for i,c in enumerate(self.chunks,1)],('SIMULATED',))

def ask(chunks,question=QUESTION,cancel=False):
    store=LinkedStore(cancel_commit=cancel); model=NeverGenerate()
    service=AnswerService(knowledge_gateway=KnowledgeGateway(ServiceRetrieval(chunks)),runs=store,answer_gateway=model)
    outcome=service.answer({'id':'conv','knowledge_base_scope':['offline-kb'],'document_scope':['offline-document']},question)
    return outcome,store,model

def assert_hint(outcome,format):
    assert outcome.error_code=='INSUFFICIENT_EVIDENCE' and not outcome.citations
    hint=outcome.trace.get('evidence_hint')
    assert hint and hint['kind']=='evidence_hint' and hint['reason_code']=='TABLE_HEADER_UNCONFIRMED'
    assert hint['source_formats']==[format] and outcome.answer==hint['text']
    assert 'XLSX' in outcome.answer and '不保证' in outcome.answer and '表头' in outcome.answer

def test_original_pdf_docx_preserve_strict_refusal_and_explain_xlsx_option(original):
    format,_,chunks=original;outcome,store,model=ask(chunks)
    assert_hint(outcome,format);assert model.calls==0
    assert store.completed==[(outcome.run_id,'failed','INSUFFICIENT_EVIDENCE')]
    assert store.links==[('user',outcome.run_id)]
    metadata=next(payload for _,event,payload in store.events if event=='run.metrics')
    assert metadata['presentation']==outcome.trace['evidence_hint']
    assert not metadata['citations'] and not store.evidence
    history=PostgresKnowledgeRepository._message_row({'role':'user','content':QUESTION,'run_id':outcome.run_id,'citations':[],'presentation':metadata['presentation']})
    assert history['presentation']==metadata['presentation']
    if os.getenv('RAG_OFFLINE_HINT_ARTIFACTS'):
        dest=Path(os.environ['RAG_OFFLINE_HINT_ARTIFACTS'])
        (dest/(format+'-mock-payload.json')).write_text(json.dumps({'response':{'run_id':outcome.run_id,'answer':outcome.answer,'error_code':outcome.error_code,'citations':[],'trace':outcome.trace},'history':[history]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

@pytest.mark.parametrize('question',['其他门店2026-09的营业额是多少？','杉桥门店2027-11的营业额是多少？','杉桥门店2026-09的成本是多少？'])
def test_unrelated_month_entity_or_field_does_not_offer_conversion(original,question):
    outcome,store,model=ask(original[2],question)
    assert outcome.error_code=='INSUFFICIENT_EVIDENCE' and not outcome.trace.get('evidence_hint') and 'XLSX' not in outcome.answer
    assert model.calls==0 and all('presentation' not in p for _,_,p in store.events)

@pytest.mark.parametrize('mutation',['other-format','other-parser-error','cross-document','cross-version','inconsistent-cell','retired'])
def test_format_only_or_invalid_source_metadata_never_triggers_hint(original,mutation):
    _,_,chunks=original;altered=[]
    for chunk in chunks:
        loc=copy.deepcopy(chunk.locator)
        is_header=loc.get('kind')=='table' and all(c.get('row')==1 for c in loc.get('cells',[]))
        if mutation=='other-format':loc['source_format']='html'
        if mutation=='other-parser-error':loc['parse_warnings']=['OCR_LANGUAGE_UNAVAILABLE']
        if mutation=='inconsistent-cell' and loc.get('cells'):loc['cells'][0]['coordinate']='R999C99'
        altered.append(replace(chunk,locator=loc,document_id='foreign' if mutation=='cross-document' and is_header else chunk.document_id,version_id='old' if mutation=='cross-version' and is_header else chunk.version_id,is_current=False if mutation=='retired' else chunk.is_current))
    outcome,_,model=ask(altered)
    assert outcome.error_code=='INSUFFICIENT_EVIDENCE' and not outcome.trace.get('evidence_hint') and 'XLSX' not in outcome.answer
    assert model.calls==0

def test_cancel_does_not_publish_or_commit_table_hint(original):
    outcome,store,_=ask(original[2],cancel=True)
    assert outcome.error_code=='CANCELLED' and not outcome.answer and not store.completed
    assert all(role!='assistant' for role,_ in store.links)

def test_history_hint_read_is_bound_to_terminal_error_scope_and_no_citations():
    recorded=[]
    class Result:
        def mappings(self):return iter([])
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,parameters):recorded.append(str(query));return Result()
    repo=object.__new__(PostgresKnowledgeRepository);repo.engine=SimpleNamespace(connect=lambda:Connection())
    assert repo.list_messages('conv')==[]
    sql=recorded[0]
    for term in ["r.error_code='INSUFFICIENT_EVIDENCE'","TABLE_HEADER_UNCONFIRMED","r.status='failed'",'r.completed_at IS NOT NULL','r.knowledge_base_scope=c.knowledge_base_scope','r.document_scope=c.document_scope',"e.payload->'citations'='[]'::jsonb"]:assert term in sql
    assert 'INSERT' not in sql and 'UPDATE' not in sql

@pytest.mark.parametrize('change',['wrong-reason','wrong-format','empty-formats','duplicate-formats','citation','assistant','no-run','empty-text','wrong-kind'])
def test_invalid_persisted_header_hint_is_not_presented(change):
    hint={'kind':'evidence_hint','reason_code':'TABLE_HEADER_UNCONFIRMED','source_formats':['pdf'],'text':'请将相关表格另存为 XLSX；不保证答对。'}
    message={'role':'user','run_id':'run','content':QUESTION,'citations':[],'presentation':hint}
    if change=='wrong-reason':hint['reason_code']='OTHER'
    if change=='wrong-format':hint['source_formats']=['html']
    if change=='empty-formats':hint['source_formats']=[]
    if change=='duplicate-formats':hint['source_formats']=['pdf','pdf']
    if change=='citation':message['citations']=['E1']
    if change=='assistant':message['role']='assistant'
    if change=='no-run':message['run_id']=None
    if change=='empty-text':hint['text']=' '
    if change=='wrong-kind':hint['kind']='error'
    assert 'presentation' not in PostgresKnowledgeRepository._message_row(message)
