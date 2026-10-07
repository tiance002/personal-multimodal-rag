import json
from dataclasses import replace
from backend.app.application.knowledge_gateway import KnowledgeGateway, EvidenceService
from backend.app.application.retrieval import RetrievalResult, RetrievalItem
from backend.app.application.run_metrics import collect_metrics
from backend.app.domain.models import ChunkRecord, RankedHit

def result(ids, config=None, score=1):
    hits=tuple(RankedHit(chunk_id=i,rank=n,raw_score=score+n,sources=('keyword',)) for n,i in enumerate(ids,1))
    return RetrievalResult(EvidenceService().plan('private synthetic query').retrieval_plan,
        [RetrievalItem(ChunkRecord(h.chunk_id,'kb','doc','v','private synthetic evidence',{}),h) for h in hits],
        ('keyword',),candidate_rankings={'keyword':hits},fused_ranking=hits,
        effective_config=config or {},latency_ms=2,stage_latency_ms={'fusion_ms':1})

def merge(first,second,cap=3):
    return KnowledgeGateway._merge(EvidenceService().plan('private synthetic query'),first,second,max_items=cap)

def test_cross_pass_hits_keep_both_rankings_and_first_selected_identity():
    first=result(['a','b'],{'top_k':2},1)
    second=result(['b','c'],{'top_k':2},50)
    combined=merge(first,second)
    p=combined.merge_provenance
    assert [x['chunk_id'] for x in p['merged_order']]==['a','b','c']
    assert p['merged_order'][1]['selected_from_pass']==1
    assert p['passes'][0]['fused_ranking'][1]['raw_score']==3
    assert p['passes'][1]['fused_ranking'][0]['raw_score']==51
    assert {x['pass_index'] for x in p['candidate_origins']['b']}=={1,2}
    assert p['configuration_consistent'] is True
    assert p['passes'][0]['configuration_fingerprint']==p['passes'][1]['configuration_fingerprint']
    assert combined.items[1] is first.items[1]
    assert combined.fused_ranking==()  # stable merge is not a new fused ranking
    assert combined.effective_config=={}  # no invented aggregate configuration
    first.effective_config['top_k']=99
    assert p['passes'][0]['effective_config']=={'top_k':2}

def test_target_only_and_capped_candidates_remain_traceable_after_dedup():
    combined=merge(result(['a','a']),result(['b','c']),2)
    p=combined.merge_provenance
    assert [x['chunk_id'] for x in p['merged_order']]==['a','b']
    assert p['merged_order'][1]['selected_from_pass']==2
    assert len([x for x in p['candidate_origins']['a'] if x['ranking']=='returned_items'])==2
    assert 'c' in p['candidate_origins']
    assert 'c' not in {x['chunk_id'] for x in p['merged_order']}

def test_empty_and_missing_metadata_are_not_fabricated():
    empty=replace(result([]),candidate_rankings={},fused_ranking=(),latency_ms=None,stage_latency_ms={})
    p=merge(empty,result(['b'])).merge_provenance
    assert len(p['passes'])==2
    assert p['passes'][0]['returned_items']==[]
    assert p['passes'][0]['configuration_fingerprint'] is None
    assert p['passes'][0]['metadata_availability']['candidate_rankings']=='NOT_AVAILABLE'
    assert p['configuration_consistent'] is None

def test_conflicting_configurations_remain_distinct():
    p=merge(result(['a'],{'top_k':2,'rrf_k':30}),result(['b'],{'top_k':2,'rrf_k':60})).merge_provenance
    assert p['configuration_consistent'] is False
    assert p['passes'][0]['configuration_fingerprint']!=p['passes'][1]['configuration_fingerprint']

def test_metrics_preserve_raw_calls_and_merge_without_double_counting_or_text():
    first,second=result(['a','b'],{'top_k':2}),result(['b','c'],{'top_k':2})
    with collect_metrics('synthetic') as metrics:
        metrics.record_retrieval(first)
        metrics.record_retrieval(second)
        metrics.record_retrieval_merge(merge(first,second).merge_provenance)
        row=metrics.snapshot(citations=(),error=None)
    assert len(row['retrieval_calls'])==2 and len(row['retrieval_merges'])==1
    assert row['keyword_candidate_count']==4
    assert row['retrieval_latency_ms']==4
    assert row['retrieval_calls'][0]['pass_metadata']['candidate_rankings']['keyword'][1]['chunk_id']=='b'
    assert [p['retrieval_call_index'] for p in row['retrieval_merges'][0]['passes']]==[1,2]
    assert 'private synthetic' not in json.dumps(row)

def test_config_fingerprint_is_canonical_across_key_order():
    p=merge(result(['a'],{'rrf_k':30,'top_k':2}),result(['b'],{'top_k':2,'rrf_k':30})).merge_provenance
    assert p['configuration_consistent'] is True

def test_gateway_records_merge_and_links_actual_calls_after_an_earlier_empty_pass():
    from backend.tests.test_quick_chain_quality import _chain, RecordingAnswerModel
    from backend.app.domain.scope import Scope
    chain=_chain(['A\u65b9\u6848\u6210\u672c1000\u5143\u3002','B\u65b9\u6848\u6210\u672c1200\u5143\u3002'],
                 RecordingAnswerModel('A\u65b9\u6848\u6210\u672c1000\u5143 [E1]\u3002B\u65b9\u6848\u6210\u672c1200\u5143 [E2]'),top_k=1)
    with collect_metrics('synthetic') as metrics:
        metrics.record_retrieval(result([]))
        chain.invoke('A \u548c B \u4e24\u79cd\u65b9\u6848\u5404\u81ea\u7684\u6210\u672c\u662f\u591a\u5c11\uff1f',Scope.from_ids(['kb']))
        row=metrics.snapshot(citations=(),error=None)
    assert len(row['retrieval_calls'])==3
    assert len(row['retrieval_merges'])==1
    assert [p['retrieval_call_index'] for p in row['retrieval_merges'][0]['passes']]==[2,3]

def test_both_empty_passes_preserve_two_distinct_empty_results():
    p=merge(result([]),result([])).merge_provenance
    assert p['merged_order']==[] and p['candidate_origins']=={}
    assert [r['pass_index'] for r in p['passes']]==[1,2]

def test_empty_scope_has_actual_query_timing_and_unavailable_unexecuted_stages():
    from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
    from backend.app.domain.scope import Scope
    r=HybridRetriever(InMemoryRetrievalRepository()).retrieve(Scope.from_ids([]),'synthetic')
    assert r.stage_latency_ms['query_processing_ms']>=0
    assert all(r.stage_latency_ms[k] is None for k in ('keyword_retrieval_ms','vector_retrieval_ms','embedding_ms','fusion_ms','ranking_ms','diversity_ms'))
