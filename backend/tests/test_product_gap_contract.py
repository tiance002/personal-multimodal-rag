"""SIMULATED scoped retrieval; real local parser, no providers/database."""
import copy,hashlib,json,os,re,zipfile
from pathlib import Path
from dataclasses import replace
import pytest
import xml.etree.ElementTree as ET
from backend.app.adapters.parsers import DocxParser
from backend.app.application.answer_service import AnswerService
from backend.app.application.follow_up import resolve_question
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain,QuickSettings
from backend.app.application.query_router import QueryRouter
from backend.app.application.structured_evidence import row_facts
from backend.app.application.retrieval import RetrievalItem,RetrievalResult
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkRecord,RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query
from backend.tests.test_bounded_history import HistoryStore,row

class FrozenRetriever:
    top_k=5
    def __init__(self,chunks): self.chunks=chunks;self.questions=[]
    def retrieve(self,scope,question,query_plan=None):
        self.questions.append((scope,question))
        return RetrievalResult(query_plan or normalize_query(question),[RetrievalItem(c,RankedHit(chunk_id=c.chunk_id,rank=i)) for i,c in enumerate(self.chunks,1)],('SIMULATED',))

@pytest.mark.parametrize('subject,prefix',[('检索练习','这种方法'),('间隔练习','该方法'),('主动回忆','这个方法')])
def test_default_runtime_bounded_method_retrieves_fresh_evidence_keeps_q0(subject,prefix):
    q0=prefix+'如何应用？'; runs=HistoryStore();runs.rows.append(row('old',subject+'是什么？'))
    text=subject+'通过学习后主动回忆来应用。'
    chunk=ChunkRecord('fresh','kb','doc','v2',text,{'start':0})
    retriever=FrozenRetriever([chunk])
    outcome=AnswerService(knowledge_gateway=KnowledgeGateway(retriever),runs=runs).answer({'id':'conv-1','knowledge_base_scope':['kb'],'document_scope':[]},q0)
    assert outcome.error_code is None
    assert runs.rows[-1]['q0']==q0 and outcome.trace['query_original']==q0
    assert outcome.trace['query_resolved']==subject+'如何应用？'
    assert retriever.questions[-1][1]==subject+'如何应用？'
    assert outcome.trace['source_completed_run_ids']==['old']
    assert outcome.trace['resolver_call_count']==0 and outcome.trace['follow_up_context_used']
    assert outcome.citations==('E1',) and runs.evidence[-1][0]==outcome.run_id

@pytest.mark.parametrize('previous,scope',[
    ('检索练习是什么？日志如何备份？',['kb']),
    ('检索练习是什么？'+'x'*1500,['kb']),
    ('检索练习是什么？',['different-kb']),
    ('缓存和备份是什么？',['kb']),
])
def test_ambiguous_unbounded_or_foreign_scope_history_does_not_execute(previous,scope):
    runs=HistoryStore();runs.rows.append(row('old',previous,kb=scope));retriever=FrozenRetriever([])
    outcome=AnswerService(knowledge_gateway=KnowledgeGateway(retriever),runs=runs).answer({'id':'conv-1','knowledge_base_scope':['kb'],'document_scope':[]},'这种方法如何应用？')
    assert outcome.error_code=='NO_CANDIDATES' and outcome.trace['clarification_required']
    assert not retriever.questions and not outcome.citations

def test_explicit_current_cache_question_is_not_blocked_by_unrelated_history():
    q0='刚才的缓存如何关闭？'
    assert not resolve_question(q0,'备份是什么？日志怎么保存？').clarification_required

FIXTURE=Path(__file__).parent/'fixtures/offline_header_boundary/original.docx'
if not FIXTURE.exists():
    FIXTURE=Path(r'C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\backend\tests\fixtures\offline_header_boundary\original.docx')

@pytest.fixture(scope='module')
def declared(tmp_path_factory):
    source=tmp_path_factory.mktemp('declared')/'declared.docx'
    with zipfile.ZipFile(FIXTURE) as z,zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as dest:
        for name in z.namelist():
            data=z.read(name)
            if name=='word/document.xml':
                data=data.replace(b'<w:tr>',b'<w:tr><w:trPr><w:tblHeader/></w:trPr>',1)
                data=data.replace('杉桥门店'.encode(),'紫湾门店'.encode()).replace(b'2026-09',b'2027-04').replace(b'312',b'741')
            dest.writestr(name,data)
    document=DocxParser(python=Path(os.environ['RAG_NATIVE_TEST_PYTHON'])).parse(source,'doc','v2')
    chunks=[ChunkRecord('new-'+str(i),'kb','doc','v2',c.content,json.loads(c.source_locator.model_dump_json()),content_sha256=c.content_sha256) for i,c in enumerate(chunk_document(document,max_chars=1800))]
    return document,chunks

def test_source_declared_docx_header_qualifies_complete_and_preserves_provenance(declared):
    document,chunks=declared
    assert document.parse_status=='complete' and not document.parse_warnings
    table=document.tables[0]
    assert table.header_detection=='docx-declared-simple-header-v1' and table.header_rows==(1,)
    assert all(c.column_header is (c.row==1) for c in table.cells)
    target=QueryRouter().plan('紫湾门店2027-04的营业额是多少？').targets[0]
    supported=[c for c in chunks if row_facts(c.content,c.locator,target,c.content_sha256)]
    assert len(supported)==1
    cells=supported[0].locator['cells'];assert len(cells)==6
    assert all(c['native_locator']['origin']['table_index']==0 for c in cells)
    assert [c['coordinate'] for c in cells]==['R1C1','R1C2','R1C3','R2C1','R2C2','R2C3']

class OfflineAnswer:
    provider_kind='local'
    def __init__(self,answer):self.text=answer;self.calls=0
    def answer(self,prompt,timeout):self.calls+=1;return self.text

def invoke(chunks,question='紫湾门店2027-04的营业额是多少？',answer='紫湾门店 2027-04 的营业额为 741 千元 [E1]。'):
    model=OfflineAnswer(answer)
    result=LangChainQuickChain(KnowledgeGateway(FrozenRetriever(chunks)),answer_gateway=model).invoke(question,Scope(('kb',),('doc',)),settings=QuickSettings(),run_id='offline-declared')
    return result,model

def test_declared_docx_complete_pipeline_accepts_correct_row_unit_and_original_citation(declared):
    _,chunks=declared;result,model=invoke(chunks)
    assert result.error_code is None and result.citations==('E1',) and model.calls==1
    snapshot=result.evidence[0]
    assert snapshot.locator['source_format']=='docx' and snapshot.locator['header_rows']==[1]
    assert snapshot.quote_sha256==hashlib.sha256(snapshot.quote.encode()).hexdigest()
    assert 'R2C3' in snapshot.quote and '741' in snapshot.quote

@pytest.mark.parametrize('mutation',['header-role','header-origin','row-origin','foreign-table','missing-unit','wrong-column','wrong-policy','partial','conflict','header-source','header-version','header-document','chunk-document','chunk-version'])
def test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit(declared,mutation):
    _,chunks=declared;changed=[]
    for chunk in chunks:
        loc=copy.deepcopy(chunk.locator);content=chunk.content
        if loc.get('kind')=='table':
            if mutation=='header-role':loc['cells'][0]['column_header']=None
            if mutation=='header-source':loc['cells'][0]['header_evidence']['source_sha256']='0'*64
            if mutation=='header-version':loc['cells'][0]['header_evidence']['source_version_id']='v1'
            if mutation=='header-document':loc['cells'][0]['header_evidence']['source_document_id']='other-doc'
            if mutation=='header-origin':loc['cells'][0]['native_locator']['origin']['row_index']=99
            if mutation=='row-origin':loc['cells'][-1]['native_locator']['origin']['row_index']=99
            if mutation=='foreign-table':loc['table_id']='docx-table-99';content=content.replace('docx-table-0','docx-table-99')
            if mutation=='wrong-column':loc['cells'][-1]['column']=99
            if mutation=='wrong-policy':loc['header_detection']='claimed-confirmed';content=content.replace('docx-declared-simple-header-v1','claimed-confirmed')
            if mutation=='partial':loc['parse_status']='partial'
            if mutation=='missing-unit':
                content=content.replace('（千元）','')
                for c in loc['cells']:
                    c['column_headers']=[s.replace('（千元）','') for s in c['column_headers']]
                    for key in ['value','display']:
                        if isinstance(c[key],str):c[key]=c[key].replace('（千元）','')
            loc['quote']=content
        changed.append(replace(chunk,content=content,locator=loc,content_sha256=hashlib.sha256(content.encode()).hexdigest(),document_id='other-doc' if mutation=='chunk-document' else chunk.document_id,version_id='v1' if mutation=='chunk-version' else chunk.version_id))
    if mutation=='conflict':
        good=next(c for c in changed if '741' in c.content and c.locator.get('kind')=='table')
        loc=copy.deepcopy(good.locator);content=good.content.replace('741','742')
        for c in loc['cells']:
            if c['coordinate']=='R2C3':c['display']=c['value']='742'
        loc['quote']=content
        changed.append(replace(good,chunk_id='conflict',content=content,locator=loc,content_sha256=hashlib.sha256(content.encode()).hexdigest()))
    result,model=invoke(changed)
    assert result.error_code=='INSUFFICIENT_EVIDENCE' and not result.evidence and model.calls==0

@pytest.mark.parametrize('answer',[
    '紫湾门店 2027-04 的营业额为 741 万元 [E1]。',
    '其他门店 2027-04 的营业额为 741 千元 [E1]。',
    '紫湾门店 2027-05 的营业额为 741 千元 [E1]。',
    '紫湾门店 2027-04 的营业额为 742 千元 [E1]。',
])
def test_declared_docx_validator_rejects_wrong_generated_claim(declared,answer):
    result,model=invoke(declared[1],answer=answer)
    assert result.error_code=='UNSUPPORTED_ANSWER' and not result.evidence and model.calls==1

@pytest.mark.parametrize('case',['no-declaration','false-declaration','non-leading','multiple-header','duplicate-label','merged-header'])
def test_docx_ambiguous_source_declarations_keep_unknown_and_partial(tmp_path,case):
    source=tmp_path/'not-confirmed.docx'
    tag=lambda s:'{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'+s
    with zipfile.ZipFile(FIXTURE) as z,zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as dest:
        for name in z.namelist():
            data=z.read(name)
            if name=='word/document.xml':
                root=ET.fromstring(data);table=root.find('.//'+tag('tbl'));rows=table.findall(tag('tr'))
                def mark(r,false=False):
                    properties=ET.SubElement(r,tag('trPr'));marker=ET.SubElement(properties,tag('tblHeader'))
                    if false:marker.set(tag('val'),'false')
                if case!='no-declaration':mark(rows[1] if case=='non-leading' else rows[0],case=='false-declaration')
                if case=='multiple-header':mark(rows[1])
                if case=='duplicate-label':
                    for t in rows[0].findall('.//'+tag('t')):
                        if t.text=='统计月份':t.text='门店'
                if case=='merged-header':
                    cells=rows[0].findall(tag('tc'));properties=cells[0].find(tag('tcPr'))
                    if properties is None:properties=ET.SubElement(cells[0],tag('tcPr'))
                    span=ET.SubElement(properties,tag('gridSpan'));span.set(tag('val'),'2');rows[0].remove(cells[1])
                data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            dest.writestr(name,data)
    document=DocxParser(python=Path(os.environ['RAG_NATIVE_TEST_PYTHON'])).parse(source,'doc','v2')
    assert document.parse_status=='partial' and 'DOCX_UNMARKED_HEADERS_UNKNOWN' in document.parse_warnings
    assert document.tables[0].header_detection!='docx-declared-simple-header-v1'
