from scripts import weknora_port_p19_score as score


def test_budget_reason_prefix_keeps_actual_p9_target_and_excludes_old_attempts():
    assert score.is_budget_target({'reason': 'NOT_RUN_BUDGET_RESERVATION_HEADROOM', 'attempts': []})
    assert not score.is_budget_target({'reason': 'NOT_RUN_BUDGET_RESERVATION_HEADROOM', 'attempts': [{}]})
    assert not score.is_budget_target({'reason': 'CAPABILITY_LIMIT', 'attempts': []})


def test_stage_recall_projects_distinct_sources_and_keeps_all_positive_qrels():
    result = score.p8.stage_rankings({'rankings': {'search': [['c1', 'kb', 'a'], ['c2', 'kb', 'a']],
                                                  'rerank': [], 'context': []}},
                                    kb='kb', bindings={'a': 'source-a'}, qrels={'source-a': 1, 'source-b': 1})
    assert result['search']['metrics']['recall_at_k'] == .5
    assert result['search']['distinct_document_count'] == 1
    assert result['context']['metrics']['recall_at_k'] == 0


def test_cross_kb_ranking_is_blocked_instead_of_scored():
    result = score.p8.stage_rankings({'rankings': {'search': [['c', 'foreign', 'a']]}},
                                    kb='kb', bindings={'a': 'source-a'}, qrels={'source-a': 1})
    assert result['search']['status'] == 'BLOCKED'
    assert result['search']['metrics'] is None


def test_hotpot_full_answer_zero_em_does_not_imply_fact_error(tmp_path):
    gold = {'qid': 'x', 'answers': ['Paris']}
    answer = score.answer_metric('hotpotqa', gold, 'Paris is the answer.', True, tmp_path, tmp_path)
    assert answer['metrics']['hotpot_answer_em'] == 0
    assert answer['metrics']['hotpot_answer_token_f1'] < 1
    diagnostic = score.format_diagnostic('hotpotqa', gold, 'Paris is the answer.', answer['metrics'])
    assert diagnostic['status'] == 'EXACT_GOLD_STRING_PRESENT_WITH_EXTRA_TEXT'
    assert diagnostic['fact_error'] == 'UNJUDGED'


def test_missing_answer_not_imputed_zero_and_no_gold_not_applicable(tmp_path):
    gold = {'qid': 'x', 'answers': ['Paris']}
    assert score.answer_metric('hotpotqa', gold, None, False, tmp_path, tmp_path)['metrics'] is None
    assert score.answer_metric('scifact', {'answers': []}, 'refusal', True, tmp_path, tmp_path)['status'].startswith('NOT_APPLICABLE')
    refusal = score.answer_metric('hotpotqa', gold, 'Insufficient evidence.', True, tmp_path, tmp_path)
    assert refusal['included_in_answer_metric_denominator'] is True
    assert refusal['metrics']['hotpot_answer_em'] == 0


def test_aggregation_never_combines_task_denominators_or_missing_results():
    rows = [{'dataset': 'longbench-zh', 'completed_assistant': True, 'answer': {'status': 'COMPUTED', 'metrics': {'rouge_zh_score': .4}}, 'citations': []},
            {'dataset': 'longbench-zh', 'completed_assistant': True, 'answer': {'status': 'COMPUTED', 'metrics': {'qa_f1_zh_score': .2}}, 'citations': []},
            {'dataset': 'longbench-zh', 'completed_assistant': False, 'answer': {'status': 'NOT_RUN', 'metrics': None}, 'citations': []}]
    result = score.aggregate(rows)['longbench-zh']
    assert result['metrics']['answer.rouge_zh_score'] == {'mean': .4, 'evaluated_denominator': 1, 'selected_denominator': 3}
    assert result['citations']['rate'] is None


def test_offsets_require_explicit_values_and_zero_is_valid():
    assert score.p9.citation_offsets({'start_at': 0, 'end_at': 0}, {'start': 0, 'end': 0})['valid']
    assert not score.p9.citation_offsets({}, {'start': 0, 'end': 0})['valid']
