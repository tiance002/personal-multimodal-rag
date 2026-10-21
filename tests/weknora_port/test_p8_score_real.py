"""Small counterexamples for the offline evidence scorer; no runtime/model I/O."""
import json
from pathlib import Path
import pytest
from scripts.p8_score_real import (DEFAULT_RUN, MODELS, evaluate, evaluate_case, identity, long_answer, plain_answer, page_text_diagnostic,
                                  stage_rankings, validate_reference, http_latency, receipt_facts, classify_stage_execution)


def write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value), encoding='utf-8')


def valid_reference():
    ref = {'id': 'chunk', 'knowledge_id': 'doc', 'knowledge_base_id': 'kb', 'content': 'raw quote',
           'chunk_metadata': {'source_version': 'v1'}}
    chunk = {'chunk_id': 'chunk', 'document_id': 'doc', 'knowledge_base_id': 'kb',
             'text': 'raw quote', 'source_version': 'v1'}
    args = {'kb': 'kb', 'bindings': {'doc': 'original-source'},
            'source_records': {'doc': {'source_id': 'original-source', 'document_id': 'doc',
                'sha256': 'sha', 'expected_sha256': 'sha', 'selection_sha256': 'sha', 'matches': True}},
            'document_rows': {'doc': {'parse_status': 'completed', 'knowledge_base_id': 'kb', 'metadata': {'source_version': 'v1'}}}}
    return ref, chunk, args


@pytest.mark.parametrize('defect', ['cross_scope', 'source_identity', 'quote', 'version'])
def test_citation_requires_scope_source_quote_and_version(defect):
    ref, chunk, args = valid_reference()
    assert validate_reference(ref, chunk, **args)['valid']
    if defect == 'cross_scope': chunk['knowledge_base_id'] = 'other-kb'
    elif defect == 'source_identity': args['source_records']['doc']['source_id'] = 'other-source'
    elif defect == 'quote': ref['content'] = 'model paraphrase'
    elif defect == 'version': chunk['source_version'] = 'old-version'
    assert not validate_reference(ref, chunk, **args)['valid']


def test_missing_results_keep_all_nine_in_denominator_and_no_fake_gold_scores(tmp_path):
    write(tmp_path, 'selection.json', {'datasets': [{'name': 'scifact', 'files': [],
        'cases': [{'qid': str(i), 'question': 'original question'} for i in range(9)]}]})
    write(tmp_path, 'gold-selected.json', {'datasets': [{'name': 'scifact', 'cases': [
        {'qid': str(i), 'question': 'original question', 'qrels': {'source': 1}, 'answers': [], 'split': 'development'}
        for i in range(9)]}]})
    result = evaluate(tmp_path)
    assert result['denominator'] == result['selection_denominator'] == 9
    assert result['wiring_counts'] == {'NOT_RUN': 9}
    for row in result['cases']:
        assert row['gold_quality']['answer']['status'] == 'NOT_APPLICABLE_NO_ANSWER_GOLD'
        assert row['gold_quality']['answer']['metrics'] is None
        assert row['stage_rankings']['search']['metrics'] is None


def test_each_stage_projects_distinct_original_sources_at_first_rank():
    result = stage_rankings({'rankings': {'search': [('a', 'kb', 'd1'), ('b', 'kb', 'd1'), ('c', 'kb', 'd2')],
        'rerank': [('c', 'kb', 'd2'), ('a', 'kb', 'd1')], 'context': [('c', 'kb', 'd2')]}},
        kb='kb', bindings={'d1': 'source1', 'd2': 'source2'}, qrels={'source2': 1})
    assert result['search']['ranked_source_ids'] == ['source1', 'source2']
    assert result['search']['metrics']['mrr_at_k'] == .5
    assert result['rerank']['metrics']['mrr_at_k'] == 1
    assert result['context']['metrics']['recall_at_k'] == 1
    assert result['search']['k'] == 10


def test_rank_mapping_refuses_cross_scope():
    result = stage_rankings({'rankings': {'search': [('a', 'other-kb', 'd1')]}},
        kb='kb', bindings={'d1': 'source1'}, qrels={'source1': 1})
    assert result['search']['status'] == 'BLOCKED'
    assert result['search']['metrics'] is None


@pytest.mark.parametrize('rerank_received', [False, True])
def test_empty_rerank_without_real_execution_is_not_a_zero_quality_score(rerank_received):
    ranks = stage_rankings({'rankings': {'search': [('a', 'kb', 'doc')], 'rerank': [],
        'context': [('a', 'kb', 'doc')]}}, kb='kb', bindings={'doc': 'source'}, qrels={'source': 1})
    result = classify_stage_execution(ranks, {'checks': {'embedding': False, 'rerank': rerank_received}})
    assert result['rerank']['ranked_source_ids'] == []
    assert result['rerank']['original_chunk_count'] == 0
    if rerank_received:
        assert result['rerank']['status'] == 'COMPUTED'
        assert result['rerank']['metrics']['recall_at_k'] == 0
    else:
        assert result['rerank']['status'] == 'NOT_RUN_NO_REAL_RERANK_EXECUTION'
        assert result['rerank']['metrics'] is None
    assert result['search']['status'] == 'DIRECT_DOCUMENT_LOAD_DIAGNOSTIC'
    assert result['search']['metrics']['recall_at_k'] == 1
    assert result['search']['metric_interpretation'] == 'SOURCE_IDENTITY_DIAGNOSTIC_NOT_VECTOR_RETRIEVAL_QUALITY'


def test_plain_answer_only_strips_system_citation_markup():
    assert plain_answer('**answer** <kb doc="x" chunk_id="c" />') == '**answer**'


def test_latency_uses_explicit_client_attempt_timestamps_without_server_id(tmp_path):
    assert http_latency(tmp_path, 'dataset', 'qid')['seconds'] is None
    write(tmp_path, 'attempts.json', {'query-' + identity('dataset', 'qid'):
        {'at': '2026-10-16T05:00:00.000000+00:00', 'finished_at': '2026-10-16T05:00:02.250000+00:00'}})
    assert http_latency(tmp_path, 'dataset', 'qid')['seconds'] == 2.25


def test_token_totals_keep_unknown_holds_separate_from_settled_actuals():
    rows = [{'request_id': 'server/embedding', 'capability': 'embedding', 'state': 'settled',
             'actual_total': 17, 'held_tokens': 17, 'outcome': 'RESPONSE_RECEIVED'},
            {'request_id': 'server/rerank', 'capability': 'rerank', 'state': 'unknown',
             'actual_total': None, 'held_tokens': 230, 'outcome': 'RESPONSE_RECEIVED'},
            {'request_id': 'server/answer/1/cheap', 'capability': 'chat', 'state': 'reserved',
             'actual_total': None, 'held_tokens': 500, 'outcome': None}]
    result = receipt_facts(rows, ['server'])
    assert result['token_totals']['settled_actual_total']['tokens'] == 17
    assert result['token_totals']['unknown_held_tokens']['tokens'] == 230
    assert result['token_totals']['reserved_held_tokens']['tokens'] == 500
    assert result['physical_counts']['ledger_call_rows'] == 3
    assert result['physical_counts']['physical_attempts_at_least'] == 2
    assert result['usage_unknown_is_free'] is False
    assert receipt_facts([], ['server'])['token_totals']['settled_actual_total']['tokens'] is None


def test_omni_page_text_is_literal_and_requires_source_binding():
    gold = {'layout_dets': [{'text': 'body', 'order': 2}, {'text': 'Title', 'order': 1},
                            {'text': 'ignored', 'order': 0, 'ignore': True}]}
    outcomes = {'0': {'ocr_result': 'Title\nbody', 'ocr_text': {'status': 'completed'}}}
    result = page_text_diagnostic({'page_id': 'page'}, gold, outcomes, source_bound=True)
    assert result['metrics']['page_text_normalized_edit_distance'] == 0
    assert result['gold_text'] == result['native_ocr_text'] == 'Title\nbody'
    assert result['official_omnidocbench_score'] == 'NOT_RUN'
    assert page_text_diagnostic({'page_id': 'page'}, gold, outcomes, source_bound=False)['metrics'] is None


@pytest.mark.parametrize('missing_source', [False, True])
def test_client_request_identity_differs_from_server_run_and_all_sources_required(tmp_path, missing_source):
    ref, chunk, args = valid_reference()
    dataset = {'name': 'dataset', 'files': [{'source_id': 'original-source', 'sha256': 'sha'}]}
    if missing_source: dataset['files'].append({'source_id': 'missing-source', 'sha256': 'missing-sha'})
    case = {'qid': 'qid', 'question': 'original question'}
    state = {'datasets': {'dataset': {'kb': 'kb', 'documents': {'original-source': {'id': 'doc', 'status': 'completed'}},
        'queries': {'qid': {'request_id': 'client-request', 'session': 'session', 'question': 'original question'}}}}}
    capture = {'session_id': 'session', 'message_id': 'message', 'run_id': 'server-run', 'question': 'original question',
        'fixed_fallback': None, 'rankings': {'search': [('chunk', 'kb', 'doc')], 'rerank': [('chunk', 'kb', 'doc')],
                                           'context': [('chunk', 'kb', 'doc')]}}
    write(tmp_path, 'sources/' + identity('dataset', 'original-source') + '.json', args['source_records']['doc'])
    prefix = 'queries/' + identity('dataset', 'qid')
    write(tmp_path, prefix + '-messages.json', {'data': [{'role': 'assistant', 'id': 'message', 'request_id': 'server-run',
        'content': 'raw answer', 'is_completed': True, 'knowledge_references': [ref]}]})
    write(tmp_path, prefix + '-chunkreadback.json', [{'data': chunk}])
    documents = args['document_rows']
    documents['doc']['metadata']['sha256'] = 'sha'
    ledger = [{'request_id': 'server-run/' + ('answer/1/cheap' if cap == 'chat' else cap), 'capability': cap,
        'provider': 'siliconflow', 'account_id': 'siliconflow-owner-main', 'model': model,
        'outcome': 'RESPONSE_RECEIVED', 'state': 'settled'} for cap, model in MODELS.items()]
    row = evaluate_case(tmp_path, dataset, case, None, state=state, ledger=ledger, documents=documents, captures=[capture])
    assert row['completed_assistant']
    assert row['real_provider_receipts']['all_received']
    assert row['citations'][0]['valid']
    if missing_source:
        assert row['wiring_status'] == 'FAIL'
        assert 'SELECTED_SOURCES_MISSING_INCOMPLETE_OR_SHA_READBACK_FAILED' in row['wiring_reasons']
    else:
        assert row['wiring_status'] == 'PASS', row['wiring_reasons']


def test_real_hash_bound_official_longbench_smoke_when_available():
    if not (DEFAULT_RUN / 'official-longbench/receipt.json').exists() or not (DEFAULT_RUN / 'eval-deps/jieba').exists():
        pytest.skip('Real official source/installed dependencies not available')
    for task, function in [('multifieldqa_zh', 'qa_f1_zh_score'), ('dureader', 'rouge_zh_score')]:
        result = long_answer(DEFAULT_RUN, task + ':native-smoke', '分辨率优于5m。', ['分辨率优于5m。'])
        assert result['status'] == 'COMPUTED_OFFICIAL_TASK_METRIC_ON_SMALL_SELECTED_SAMPLE', result
        assert result['metrics'][function] > .999
        assert result['binding']['revision'] == 'c5ea10bcd06285223c58dfed76bbc92d22273709'
