import pytest

from eval_center.source_metrics import source_case_metrics, build_source_statistics
from eval_center.gold import SourceSpan, GoldEvidence
from backend.app.domain.models import NormalizedDocument
from backend.app.domain.chunking import chunk_document


def stats(gold,slots):
    return {'gold_sources':gold,'index_intersections':{slot['item_id']:slot['intervals'] for slot in slots},
            'rankings':{stage:[slot['item_id'] for slot in slots] for stage in ('keyword','vector','fused','context')},
            'coverage_threshold':.8}


CONFIG={'top_k':5,'candidate_k':32}


def test_split_gold_and_overlap_use_union_source_credit_distinct_from_chunk_ranks():
    data=stats({'g':{'length':10,'grade':1}},[
        {'item_id':'a','intervals':{'g':[[0,4]]}},
        {'item_id':'b','intervals':{'g':[[4,10]]}},
        {'item_id':'c','intervals':{'g':[[2,8]]}}])
    result=source_case_metrics(data,CONFIG)
    assert result['fused.recall_at_k']==1
    assert result['fused.precision_at_k']==3/5
    assert result['fused.mrr_at_k']==1
    assert result['fused.source_recall_at_k']==1
    assert result['context_recall']==1
    assert result['evidence_coverage']==1


def test_one_chunk_many_gold_has_one_chunk_rank_slot_but_all_source_coverage():
    data=stats({'g1':{'length':10,'grade':1},'g2':{'length':10,'grade':1}},[
        {'item_id':'a','intervals':{'g1':[[0,10]],'g2':[[0,10]]}}])
    result=source_case_metrics(data,CONFIG)
    assert result['fused.recall_at_k']==1  # conventional chunk qrels
    assert result['fused.precision_at_k']==1/5
    assert result['fused.source_recall_at_k']==1
    assert result['context_recall']==1
    assert result['fused.ndcg_at_k']<=1


def test_chunk_and_source_units_both_cover_multi_evidence():
    data=stats({'g1':{'length':10,'grade':1},'g2':{'length':10,'grade':1}},[
        {'item_id':'a','intervals':{'g1':[[0,10]],'g2':[[0,10]]}},
        {'item_id':'b','intervals':{'g1':[[0,10]]}}])
    result=source_case_metrics(data,CONFIG)
    assert result['fused.recall_at_k']==1
    assert result['fused.precision_at_k']==.4
    assert result['fused.map_at_k']==1
    assert result['fused.source_recall_at_k']==1


def test_no_answer_excluded_and_duplicate_item_does_not_get_second_credit():
    empty=source_case_metrics(stats({},[{'item_id':'a','intervals':{}}]),CONFIG)
    assert empty['fused.recall_at_k'] is None
    assert empty['context_recall'] is None
    data=stats({'g1':{'length':10,'grade':1},'g2':{'length':10,'grade':1}},[
        {'item_id':'a','intervals':{'g1':[[0,10]],'g2':[[0,10]]}},
        {'item_id':'a','intervals':{'g1':[[0,10]],'g2':[[0,10]]}}])
    result=source_case_metrics(data,CONFIG)
    assert result['fused.recall_at_k']==1
    assert result['fused.precision_at_k']==.2
    assert result['fused.source_recall_at_k']==1


def test_unknown_gold_or_out_of_bounds_cannot_create_coverage():
    data=stats({'g':{'length':10,'grade':1}},[{'item_id':'a','intervals':{'g':[[0,11]]}}])
    with pytest.raises(ValueError): source_case_metrics(data,CONFIG)
    data['index_intersections']['a']={'unknown':[[0,1]]}
    with pytest.raises(ValueError): source_case_metrics(data,CONFIG)


def test_same_source_gold_survives_actual_production_chunking_variants():
    content='a'*1100
    document=NormalizedDocument(document_id='doc',version_id='v',title='source',media_type='text/plain',
        markdown_content=content,content_sha256='a'*64,parser_version='text/v1')
    gold=[GoldEvidence('stable_gold',SourceSpan('doc','a'*64,350,800))]
    results=[]
    for size,overlap in [(400,40),(700,70)]:
        chunks=chunk_document(document,max_chars=size,overlap=overlap)
        spans={f'chunk-{size}-{number}':SourceSpan('doc','a'*64,chunk.start,chunk.end)
               for number,chunk in enumerate(chunks)}
        rankings={stage:list(spans) for stage in ('keyword','vector','fused','context')}
        data=build_source_statistics(gold,spans,rankings)
        result=source_case_metrics(data,CONFIG)
        assert result['context_recall']==1
        assert result['evidence_coverage']==1
        assert list(data['gold_sources'])==['stable_gold']
        results.append(len(spans))
    assert results[0]!=results[1]
