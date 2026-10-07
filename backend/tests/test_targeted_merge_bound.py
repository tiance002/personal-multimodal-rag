import pytest
from backend.app.application.knowledge_gateway import KnowledgeGateway, EvidenceService
from backend.app.application.retrieval import RetrievalResult, RetrievalItem
from backend.app.domain.models import ChunkRecord, RankedHit

def result(ids):
    items=[RetrievalItem(ChunkRecord(i,'kb','doc','v','synthetic',{}),RankedHit(chunk_id=i,rank=n)) for n,i in enumerate(ids,1)]
    return RetrievalResult(EvidenceService().plan('synthetic').retrieval_plan,items,('keyword',))

def test_second_pass_merge_enforces_explicit_cap_without_mutating_inputs():
    first=result(['a','a','b','c','d'])
    second=result(['b','e','f','g'])
    plan=EvidenceService().plan('synthetic')
    merged=KnowledgeGateway._merge(plan,first,second,max_items=4)
    assert [i.chunk.chunk_id for i in merged.items]==['a','b','c','d']
    assert len(first.items)==5 and len(second.items)==4

def test_second_pass_merge_preserves_targeted_new_evidence_within_two_pass_cap():
    merged=KnowledgeGateway._merge(EvidenceService().plan('synthetic'),result(['a']),result(['a','b']),max_items=2)
    assert [i.chunk.chunk_id for i in merged.items]==['a','b']
    with pytest.raises(ValueError):
        KnowledgeGateway._merge(EvidenceService().plan('synthetic'),result(['a']),result(['b']),max_items=0)
