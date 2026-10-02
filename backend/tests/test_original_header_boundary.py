"""Original synthetic source replay; unknown headers never become row evidence."""
from pathlib import Path
from dataclasses import replace
import copy,hashlib,json,os
import pytest
from backend.app.adapters.parsers import PdfParser,DocxParser
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkRecord,RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query
from backend.app.application.query_router import QueryRouter
from backend.app.application.quality import QualityGate
from backend.app.application.quick_chain import LangChainQuickChain,QuickSettings
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.retrieval import RetrievalItem,RetrievalResult
from backend.app.application.structured_evidence import row_facts
from backend.app.application.follow_up import resolve_follow_up
from backend.app.application.answer_hardening import detect_intents

QUESTION='杉桥门店2026-09的营业额是多少？'
FIXTURES=Path(__file__).parent/'fixtures/offline_header_boundary'

@pytest.fixture(scope='module',params=['pdf','docx'])
def original(request):
    kind=request.param;path=FIXTURES/f'original.{kind}'
    origins=json.loads((FIXTURES/'origins.json').read_text(encoding='utf-8'))
    expected=next(o for o in origins if o['format']==kind)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected['sha256']
    if kind=='pdf':parsed=PdfParser(tessdata=FIXTURES/'absent').parse(path,'offline-document','offline-version')
    else:
        runtime=os.getenv('RAG_NATIVE_TEST_PYTHON')
        if not runtime:pytest.skip('Explicit offline native runtime required')
        parsed=DocxParser(python=Path(runtime)).parse(path,'offline-document','offline-version')
    chunks=[ChunkRecord(f'{kind}-{i}','offline-kb',parsed.document_id,parsed.version_id,c.content,json.loads(c.source_locator.model_dump_json()),content_sha256=hashlib.sha256(c.content.encode()).hexdigest()) for i,c in enumerate(chunk_document(parsed,max_chars=1800))]
    yield kind,parsed,chunks
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected['sha256']

class NeverGenerate:
    provider_kind='local'
    calls=0
    def answer(self,*args,**kwargs):
        self.calls+=1
        raise AssertionError('Unknown headers must refuse before generation')

class FrozenRetrieval:
    top_k=5
    def __init__(self,chunks):self.chunks=chunks
    def retrieve(self,scope,question,query_plan=None):
        assert scope.knowledge_base_ids==('offline-kb',) and scope.document_ids==('offline-document',)
        return RetrievalResult(query_plan or normalize_query(question),[RetrievalItem(c,RankedHit(chunk_id=c.chunk_id,rank=i)) for i,c in enumerate(self.chunks,1)],('frozen',))

def assert_refusal(chunks):
    gateway=NeverGenerate();chain=LangChainQuickChain(KnowledgeGateway(FrozenRetrieval(chunks)),answer_gateway=gateway)
    answer=chain.invoke(QUESTION,Scope(('offline-kb',),('offline-document',)),settings=QuickSettings(),run_id='offline-header-refusal')
    assert answer.error_code=='INSUFFICIENT_EVIDENCE' and not answer.answer and not answer.evidence and gateway.calls==0

def test_actual_original_source_preserves_header_text_but_refuses(original):
    kind,parsed,chunks=original
    assert parsed.parse_status=='partial' and len(chunks)==(4 if kind=='pdf' else 5)
    assert len(parsed.tables)==1
    assert all(s in '\n'.join(c.display for t in parsed.tables for c in t.cells) for s in ['门店','统计月份','营业额（千元）','2026-09','312'])
    assert all(c.column_header is None for t in parsed.tables for c in t.cells)
    target=QueryRouter().plan(QUESTION).targets[0]
    assert all(not row_facts(c.content,c.locator,target,c.content_sha256) for c in chunks)
    assert_refusal(chunks)

@pytest.mark.parametrize('mutation',['wrong_column','wrong_row','cross_table','missing_unit','complete_unknown'])
def test_original_source_counterexamples_never_promote_unknown_header(original,mutation):
    _,_,chunks=original;changed=[]
    for chunk in chunks:
        loc=copy.deepcopy(chunk.locator);content=chunk.content
        if loc.get('kind')=='table':
            if mutation=='wrong_column' and loc.get('cells'):
                loc['cells'][-1]['column']=99;loc['cells'][-1]['coordinate']='R2C99'
            elif mutation=='wrong_row' and loc.get('cells'):
                loc['cells'][-1]['row']=99;loc['cells'][-1]['coordinate']='R99C3'
            elif mutation=='cross_table':loc['table_id']='foreign-'+chunk.chunk_id;loc['header_rows']=[99]
            elif mutation=='missing_unit':
                content=content.replace('（千元）','')
                for cell in loc.get('cells',[]):
                    for key in ['value','display']:
                        if isinstance(cell.get(key),str):cell[key]=cell[key].replace('（千元）','')
                    cell['column_headers']=[h.replace('（千元）','') for h in cell.get('column_headers',[])]
            elif mutation=='complete_unknown':loc['parse_status']='complete'
            loc['quote']=content
        changed.append(replace(chunk,content=content,locator=loc,content_sha256=hashlib.sha256(content.encode()).hexdigest()))
    assert_refusal(changed)

def test_followup_current_multiple_intents_are_not_replaced_by_old_background():
    q0='刚才的缓存如何关闭？同时日志如何备份？'
    question,used=resolve_follow_up(q0,'旧问题：备份是什么？')
    assert question==q0 and not used
    assert detect_intents(question).intents==('刚才的缓存如何关闭','日志如何备份')
