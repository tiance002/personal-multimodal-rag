"""SIMULATED offline sources, including strict row/citation counterexamples."""
from dataclasses import replace
from decimal import Decimal
import copy,hashlib,json
import pytest
from backend.app.application.query_router import QueryRouter
from backend.app.application.quality import QualityGate
from backend.app.application.answer_validation import AnswerValidator
from backend.app.application.citations import CitationService,InMemoryCitationStore
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.retrieval import RetrievalItem,RetrievalResult
from backend.app.domain.models import ChunkRecord,RankedHit
from backend.app.domain.text_normalization import normalize_query

QUESTION='松林门店2025-02的收入是多少？'
def record(kind='html',subject='松林门店',period='2025-02',attribute='收入',unit='元',value='1230',row=2,doc='document',version='version',identifier='chunk'):
 headers=['门店','统计月份',f'{attribute}（{unit}）'];values=[subject,period,value]
 if kind=='tsv':
  text='合成月度资料\n'+'\t'.join(headers)+'\n'+'\t'.join(values)+'\n'
  locator={'kind':'text','parse_status':'complete','quote':text,'cells':[],'start':0,'end':len(text)}
 else:
  native=kind=='html';cells=[]
  coord=lambda r,c:f'R{r}C{c}' if native else chr(64+c)+str(r)
  for r,vals in [(1,headers),(row,values)]:
   for c,display in enumerate(vals,1):
    raw=int(display) if r!=1 and c==3 and display.isdigit() else display
    cells.append({'row':r,'column':c,'coordinate':coord(r,c),'display':display,'value':raw,'value_type':'number' if type(raw) is int else 'text','column_headers':[headers[c-1]],'column_header':r==1 if native else None,'row_span':1,'column_span':1,'cache_status':'not_applicable','formula':None,'number_format':'General'})
  cell_range=coord(row,1)+':'+coord(row,3);policy='html-source-policy-v1' if native else 'inferred_first_multi_text_row'
  if native:
   text=f'Document format: html; table: synthetic-table; row: {row}; range: {cell_range}; headers: {policy}\n'+' | '.join(f'{coord(row,c)} [{headers[c-1]}]={json.dumps(v,ensure_ascii=False)}; span=1x1' for c,v in enumerate(values,1))+'\n'
  else:
   text=f'Sheet: Facts; table: synthetic-table; range: {cell_range}\n'+' | '.join(f'{coord(row,c)} [{headers[c-1]}]={v}; format=General' for c,v in enumerate(values,1))+'\n'
  locator={'kind':'table','source_format':'html' if native else None,'sheet':None if native else 'Facts','table_id':'synthetic-table','cell_range':cell_range,'header_rows':[1],'header_detection':policy,'cells':cells,'quote':text,'parse_status':'complete'}
 return ChunkRecord(identifier,'kb',doc,version,text,locator,content_sha256=hashlib.sha256(text.encode()).hexdigest())
def decision(*chunks,question=QUESTION):return QualityGate().evaluate_chunks(list(chunks),QueryRouter().plan(question))
def snapshots(*chunks):
 service=CitationService(InMemoryCitationStore())
 for c in chunks:service.freeze('run',c)
 return tuple(service.snapshots.values())
def validate(answer,*chunks,question=QUESTION):return AnswerValidator().validate(answer,snapshots(*chunks),QueryRouter().plan(question))

def test_independent_month_subject_and_field_preserve_q0_and_ascii_model_id():
 p=QueryRouter().plan(QUESTION);t=p.targets[0]
 assert p.original_query==QUESTION and (t.subject,t.period,t.attribute,t.unit)==('松林门店','2025-02','收入',None)
 assert t.literal_subject=='松林门店2025-02'
 t=QueryRouter().plan('Model-2025-02的成本是多少？').targets[0]
 assert t.subject=='Model-2025-02' and t.period is None

@pytest.mark.parametrize('kind',['html','xlsx','tsv'])
def test_valid_explicit_row_and_unit(kind):
 c=record(kind);assert decision(c).accepted
 assert validate('松林门店2025-02的收入为1230元 [E1]',c) is None

@pytest.mark.parametrize('change',[{'subject':'其他门店'},{'period':'2025-03'},{'attribute':'成本'},{'unit':'千克'}])
@pytest.mark.parametrize('kind',['html','xlsx','tsv'])
def test_wrong_entity_month_field_or_incompatible_unit_rejected(kind,change):assert not decision(record(kind,**change)).accepted

@pytest.mark.parametrize('kind',['html','xlsx','tsv'])
def test_question_unit_is_explicit_and_must_match_source(kind):
 q='松林门店2025-02的收入（千元）是多少？'
 assert not decision(record(kind),question=q).accepted
 assert decision(record(kind,unit='千元'),question=q).accepted

@pytest.mark.parametrize('answer',[
 '其他门店2025-02的收入为1230元 [E1]',
 '松林门店2025-03的收入为1230元 [E1]',
 '松林门店2025-02的成本为1230元 [E1]',
 '松林门店2025-02的收入为1230万元 [E1]',
 '松林门店2025-02的收入为999元 [E1]',
 '松林门店2025-02的收入为1230 [E1]',
 '松林门店2025-02的收入为1230元 [E1]；其他门店2025-02的收入为1230元 [E1]',
 '松林门店2025-02的收入为1230元和999元 [E1]',
])
def test_answer_claim_exact_row_number_unit_and_entity(answer):assert validate(answer,record())=='UNSUPPORTED_ANSWER'

@pytest.mark.parametrize('kind',['html','xlsx','tsv'])
def test_conflicting_values_refuse(kind):
 a=record(kind,identifier='a');b=record(kind,value='999',row=3,identifier='b')
 assert not decision(a,b).accepted
 assert validate('松林门店2025-02的收入为1230元 [E1]',a,b)=='UNSUPPORTED_ANSWER'

@pytest.mark.parametrize('change',['wrong_row','wrong_coordinate','mismatched_headers','formula','partial','unknown','forged_content','missing_header','missing_identity_hash'])
def test_metadata_and_source_relation_reject(change):
 c=record();loc=copy.deepcopy(c.locator)
 if change=='wrong_row':loc['cells'][-1]['row']=3
 elif change=='wrong_coordinate':loc['cells'][-1]['coordinate']='R2C2'
 elif change=='mismatched_headers':loc['cells'][-1]['column_headers']=['成本（元）']
 elif change=='formula':loc['cells'][-1].update(formula='=SUM(A1)',cache_status='present')
 elif change=='partial':loc['parse_status']='partial'
 elif change=='unknown':loc['header_detection']='UNKNOWN'
 elif change=='forged_content':loc['cells'][-1].update(value=999,display='999')
 elif change=='missing_header':loc['cells']=loc['cells'][3:]
 elif change=='missing_identity_hash':c=replace(c,content_sha256=None)
 assert not decision(replace(c,locator=loc)).accepted

@pytest.mark.parametrize('foreign',['document','version','knowledge_base'])
def test_never_joins_header_and_data_from_different_sources(foreign):
 c=record();head=copy.deepcopy(c.locator);data=copy.deepcopy(c.locator)
 head['cells']=head['cells'][:3];data['cells']=data['cells'][3:]
 a=replace(c,chunk_id='header',locator=head)
 b=replace(c,chunk_id='row',knowledge_base_id='other' if foreign=='knowledge_base' else c.knowledge_base_id,document_id='other' if foreign=='document' else c.document_id,version_id='other' if foreign=='version' else c.version_id,locator=data)
 assert not decision(a,b).accepted

def test_plain_words_without_structure_and_cross_chunk_prose_do_not_support():
 a=ChunkRecord('a','kb','d','v','松林门店 2025-02',{'kind':'text'})
 b=ChunkRecord('b','kb','d','v','收入1230元',{'kind':'text'})
 assert not decision(a,b).accepted
 assert not decision(ChunkRecord('c','kb','d','v','门店 统计月份 收入（元） 松林门店 2025-02 1230',{'kind':'text'})).accepted

@pytest.mark.parametrize('content',[
 '门店\t统计月份\t收入（元）\n松林门店\t2025-02\n',
 '门店\t统计月份\t收入（元）\n松林门店\t2025-02\t1230\n正文\n松林门店\t2025-02\t999\n',
 '门店\t统计月份\t收入（元）\n松林门店\t2025-02\t=SUM(A1)\n',
 '门店\t统计月份\t收入（元）\n松林门店\t2025-02\t1230\n松林门店\t2025-02\t999\n',
])
def test_incomplete_or_conflicting_tsv_never_becomes_prose_fallback(content):
 loc={'kind':'text','parse_status':'complete','quote':content,'cells':[]}
 c=ChunkRecord('c','kb','d','v',content,loc,content_sha256=hashlib.sha256(content.encode()).hexdigest())
 assert not decision(c).accepted

def test_original_prose_gate_keeps_old_behavior():
 c=ChunkRecord('c','kb','d','v','松林门店2025-02的收入为1230元。',{'kind':'text'})
 assert decision(c).accepted
 assert validate('松林门店2025-02的收入为1230元 [E1]',c) is None
 assert not decision(replace(c,content='松林门店2025-03的收入为1230元。')).accepted

def test_conflicting_prose_cannot_override_structured_row():
 table=record()
 prose=ChunkRecord('prose','kb','other','v','松林门店2025-02的收入为999元。',{'kind':'text'})
 assert not decision(table,prose).accepted
 vague=replace(prose,content='松林门店2025-02的收入为999。')
 assert not decision(table,vague).accepted

def test_unknown_table_header_cannot_bypass_answer_validator_as_plain_prose():
 c=record();loc=copy.deepcopy(c.locator);loc['header_detection']='UNKNOWN'
 text=c.content+'松林门店2025-02的收入为1230元。'
 loc['quote']=text
 c=replace(c,content=text,locator=loc,content_sha256=hashlib.sha256(text.encode()).hexdigest())
 assert not decision(c).accepted
 assert validate('松林门店2025-02的收入为1230元 [E1]',c)=='UNSUPPORTED_ANSWER'

def test_stale_chunk_does_not_provide_current_row_support():
 assert not decision(replace(record(),is_current=False)).accepted

def test_context_and_citations_keep_only_actual_supporting_row_and_original_quote():
 good=record(identifier='good');wrong=record(period='2025-03',row=3,identifier='wrong')
 items=[RetrievalItem(wrong,RankedHit(chunk_id='wrong',rank=1)),RetrievalItem(good,RankedHit(chunk_id='good',rank=2))]
 service=EvidenceService();plan=service.plan(QUESTION)
 bundle=service.bundle(plan,RetrievalResult(normalize_query(QUESTION),items,('vector',)))
 assert bundle.decision.accepted and [i.chunk.chunk_id for i in bundle.selected]==['good']
 citation=CitationService(InMemoryCitationStore());bundle=service.with_context(bundle,'run',citation)
 snapshot=bundle.snapshots[0]
 assert snapshot.quote==good.content and snapshot.locator==good.locator and snapshot.chunk_id=='good' and snapshot.version_id==good.version_id
 assert citation.resolve('run','E1').quote==good.content
 assert validate('松林门店2025-02的收入为1230元 [E2]',wrong,good) is None
 assert validate('松林门店2025-02的收入为1230元 [E1]',wrong,good)=='UNSUPPORTED_ANSWER'
