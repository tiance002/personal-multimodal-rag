"""Saved real responses replayed locally; no providers, databases or live runs."""
from pathlib import Path
from dataclasses import replace
import copy,hashlib,json
import pytest
from backend.app.application.quick_chain import LangChainQuickChain,QuickSettings
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.retrieval import RetrievalItem,RetrievalResult
from backend.app.application.query_router import QueryRouter
from backend.app.application.quality import QualityGate
from backend.app.application.structured_evidence import claim_amount
from backend.app.domain.models import ChunkRecord,RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query

CASES=json.loads(Path(__file__).with_name('structured_real_candidate_spacing.json').read_text(encoding='utf-8'))['cases']
KB='c5cf94a1-ee90-4147-acb6-116af14fc552'

def chunk(case):
    return ChunkRecord(case['selected_source_chunk_id'],KB,case['document_id'],case['version_id'],case['source_quote'],case['source_locator'],content_sha256=case['source_sha256'])

class SavedAnswer:
    provider_kind='local'
    def __init__(self,answer):self.saved=answer;self.calls=0
    def answer(self,prompt,timeout):
        self.calls+=1
        assert self.calls==1 and '[E1]' in prompt
        return self.saved

class FrozenRetriever:
    top_k=5
    def __init__(self,case,records):self.case=case;self.records=records
    def retrieve(self,scope,question,query_plan=None):
        assert scope.knowledge_base_ids==(KB,) and scope.document_ids==(self.case['document_id'],)
        return RetrievalResult(query_plan or normalize_query(question),[RetrievalItem(c,RankedHit(chunk_id=c.chunk_id,rank=i)) for i,c in enumerate(self.records,1)],('vector',))

def invoke(case,answer=None,records=None):
    stub=SavedAnswer(case['candidate_NOT_ACCEPTED'] if answer is None else answer)
    chain=LangChainQuickChain(KnowledgeGateway(FrozenRetriever(case,records or [chunk(case)])),answer_gateway=stub)
    result=chain.invoke(case['question'],Scope((KB,),(case['document_id'],)),settings=QuickSettings(),run_id='offline-saved-'+case['format'])
    return result,stub

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['format'])
def test_saved_real_response_passes_complete_generation_validation_and_citation(case):
    result,stub=invoke(case)
    assert stub.calls==1 and result.error_code is None
    assert result.answer==case['candidate_NOT_ACCEPTED'] and result.citations==('E1',)
    assert not result.trace.reason_codes and len(result.evidence)==1
    snapshot=result.evidence[0]
    assert (snapshot.chunk_id,snapshot.version_id,snapshot.quote,snapshot.locator,snapshot.quote_sha256)==(case['selected_source_chunk_id'],case['version_id'],case['source_quote'],case['source_locator'],case['source_sha256'])
    assert case['live_result']=='FAIL_UNSUPPORTED_ANSWER'

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['format'])
@pytest.mark.parametrize('old,new',[
    ('杉桥门店','其他门店'),('杉桥门店','其他杉桥门店'),('杉桥门店','杉 桥门店'),
    ('2026-09','2026-10'),('2026-09','2026-099'),('2026-09','2026 -09'),
    ('杉桥门店 2026','杉桥门店\n2026'),('312','298'),('千元','万元'),('千元',''),
    ('营业额','成本'),('[E1]','[E999]'),
])
def test_complete_postgeneration_path_rejects_wrong_identity_value_unit_or_label(case,old,new):
    result,stub=invoke(case,case['candidate_NOT_ACCEPTED'].replace(old,new))
    assert stub.calls==1
    assert result.error_code in {'UNSUPPORTED_ANSWER','INVALID_CITATION'}
    assert result.answer=='' and result.citations==() and result.evidence==()

@pytest.mark.parametrize('separator',['',' ','\t','  '])
def test_shared_prose_and_amount_identity_matcher(separator):
    case=CASES[0];target=QueryRouter().plan(case['question']).targets[0]
    sentence=f'杉桥门店{separator}2026-09 的营业额为 312 千元'
    assert QualityGate.supporting_fact(sentence,target)==sentence
    assert claim_amount(sentence,target) is not None

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['format'])
def test_spaced_conflicting_prose_still_blocks_structured_source(case):
    good=chunk(case)
    bad=replace(good,chunk_id='conflicting-prose',content='杉桥门店 2026-09 的营业额为 999 千元',locator={'kind':'text'})
    result,stub=invoke(case,records=[good,bad])
    assert result.error_code=='INSUFFICIENT_EVIDENCE' and stub.calls==0 and not result.evidence

@pytest.mark.parametrize('kind',['pdf','docx'])
@pytest.mark.parametrize('state',['UNKNOWN','partial'])
def test_unsupported_header_metadata_cannot_bypass_gate(kind,state):
    case=next(c for c in CASES if c['format']=='html');c=chunk(case)
    locator=copy.deepcopy(c.locator);locator['source_format']=kind
    if state=='UNKNOWN':locator['header_detection']='UNKNOWN'
    else:locator['parse_status']='partial'
    result,stub=invoke(case,records=[replace(c,locator=locator)])
    assert result.error_code=='INSUFFICIENT_EVIDENCE' and stub.calls==0 and not result.evidence
