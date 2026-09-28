from eval_center.quality import answer_statistics, quality_metrics, duplicate_statistics, explicit_refusal


def test_literal_model_abstentions_are_not_lost_without_a_business_error_code():
    for answer in ('根据提供的资料，无法找到私人号码。','根据资料，无法回答银行卡余额。',
                   '提供的资料中没有给出自动批准日期。','Cannot find the phone number in the evidence.'):
        assert explicit_refusal(answer) is True
    assert explicit_refusal('The phone number is 123456.') is False
    assert explicit_refusal('The date is September 30.\n\nNo information about the owner.') is False
    assert explicit_refusal(None) is None
    assert explicit_refusal('证据不足。',error_code='LOW_COVERAGE') is True


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
