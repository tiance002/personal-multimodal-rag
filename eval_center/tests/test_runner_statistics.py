import pytest

from eval_center.source_metrics import source_case_metrics


def sample():
    return {'gold_sources':{'g':{'length':10,'grade':1}},
        'index_intersections':{'a':{},'b':{'g':[[0,10]]}},
        'rankings':{'keyword':['a'],'vector':['a'],'fused':['a'],'context':['a','b']},
        'coverage_threshold':.8,'context_mode':'sent',
        'retrieval_attempts':[{'keyword':['a'],'vector':['a'],'fused':['a']},
                              {'keyword':['b'],'vector':['b'],'fused':['b']}],
        'dedup':{'total_units':2,'duplicate_units':0,'tokens_before':20,'tokens_after':20,
                 'reviewed_merges':0,'false_merges':0,'tokenizer':'cl100k_base'}}


def test_actual_retry_merged_context_is_not_truncated_to_initial_top_k():
    result=source_case_metrics(sample(),{'top_k':1,'candidate_k':1})
    assert result['fused.source_recall_at_k']==0
    assert result['context.source_recall_at_k']==0
    assert result['context_recall']==1
    assert result['context.selected_count']==2
    assert result['attempt_1.fused.recall_at_k']==1
    assert result['context_sent_to_model']==1
    assert result['context_token_savings_estimated']==0
    assert result['false_merge_rate'] is None


def test_unconfirmed_or_prepared_context_is_not_claimed_as_sent():
    data=sample()
    data['context_mode']='prepared'
    assert source_case_metrics(data,{'top_k':1,'candidate_k':1})['context_sent_to_model'] is None
    data['context_mode']='unconfirmed'
    assert source_case_metrics(data,{'top_k':1,'candidate_k':1})['context_sent_to_model'] is None


def test_private_dedup_fields_and_unbounded_retry_candidates_rejected():
    data=sample()
    data['dedup']['source_text']='PRIVATE'
    with pytest.raises(ValueError): source_case_metrics(data,{'top_k':1,'candidate_k':1})
    data=sample()
    data['retrieval_attempts'][0]['keyword']=['a','b']
    with pytest.raises(ValueError): source_case_metrics(data,{'top_k':1,'candidate_k':1})
