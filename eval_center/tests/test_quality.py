from eval_center.quality import answer_statistics, quality_metrics, duplicate_statistics


def test_required_alternatives_and_citation_readbacks_are_deterministic():
    stats=answer_statistics(answer='Budget is 1200 credits [E1]. Owner Maya [E9].',
        answer_points=[['1200 credits','1,200 credits'],['Maya','玛雅'],['March 18']],
        citation_readbacks={'E1':True,'E9':False},answerable=True,refused=False)
    metrics=quality_metrics(stats)
    assert metrics['answer_point_coverage']==2/3
    assert metrics['citation_readability']==.5
    assert metrics['refusal_accuracy'] is None
    assert metrics['faithfulness'] is None
    assert stats['judge_status']=='NOT_EVALUATED'


def test_unavailable_generation_never_gets_zero_or_fake_score():
    stats=answer_statistics(answer=None,answer_points=[['fact']],citation_readbacks={},
                            answerable=True,refused=None)
    assert all(value is None for value in quality_metrics(stats).values())
    stats=answer_statistics(answer='insufficient evidence',answer_points=[],citation_readbacks={},
                            answerable=False,refused=True)
    assert quality_metrics(stats)['refusal_accuracy']==1


def test_dedup_never_merges_different_years_amounts_or_source_versions():
    rows=[{'text':'Budget 2024 is 1000.','document_id':'d','source_version':'v1'},
          {'text':'Budget 2024 is 1000.','document_id':'d','source_version':'v1'},
          {'text':'Budget 2025 is 1000.','document_id':'d','source_version':'v1'},
          {'text':'Budget 2024 is 1200.','document_id':'d','source_version':'v1'},
          {'text':'Budget 2024 is 1000.','document_id':'d','source_version':'v2'}]
    stats=duplicate_statistics(rows,token_counter=lambda text:len(text.split()))
    assert stats['removed_indices']==[1]
    assert stats['exact_duplicate_rate']==1/5
    assert stats['false_merge_rate'] is None
    assert stats['protected_fact_conflicts']==0
    assert stats['context_token_savings']['availability']=='estimated'
    assert stats['context_token_savings']['saved']==4
    assert stats['tokenizer_method']=='caller_supplied'


def test_missing_tokenizer_is_unavailable_never_character_tokens():
    stats=duplicate_statistics([{'text':'很长的一段话','document_id':'d','source_version':'v'}])
    assert stats['context_token_savings']=={'availability':'unavailable','before':None,'after':None,'saved':None}
