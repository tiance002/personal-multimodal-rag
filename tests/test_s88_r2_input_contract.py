"""Synthetic input/Gold separation and identity checks; no service calls."""
from copy import deepcopy
import json

import pytest

from scripts.s88_r2_input_contract import (
    freeze_contract, inference_inputs, longbench_source_identity, project_jsonl_inputs, project_task_input,
    scoring_protocol, text_sha256, verify_task_record,
)


def bundle():
    task = project_task_input('longbench-zh', {'qid': 'task', 'question': 'Which date?',
        'paired_source_doc_id': 'input-doc', 'answers': ['SECRET'], 'qrels': {'gold-doc': 1}})
    task['input_proof'] = {'kind': 'DATASET_TASK_INPUT'}
    return {'expected_count': 1, 'historical': {'cheap': '40/55', 'union': '41/55'},
        'original_inputs': [{'ordinal': 0, 'case_id': 'case', 'dataset': 'longbench-zh',
            'qid': 'task', 'question': 'Which date?', 'q0_sha256': text_sha256('Which date?'),
            'scope': {'owner_id': 'owner', 'knowledge_base_ids': ['kb'], 'document_ids': []},
            'gold_sha256_canonical': 'g' * 64, 'original_input_proof': {'kind': 'FROZEN_REQUEST'}}],
        'task_inputs': [task], 'document_bindings': [{'dataset': 'longbench-zh',
            'input_source_id': 'input-doc', 'knowledge_base_id': 'kb', 'document_id': 'doc',
            'source_version': 'v1', 'original_source_sha256': 'a' * 64,
            'current_enabled': True, 'source_hash_matches_original_upload': True,
            'mapping_basis': 'EXACT_SOURCE_ID_AND_ORIGINAL_UPLOAD_SHA',
            'identity_proofs': [{'kind': 'ORIGINAL_SOURCE_BYTES'}]}]}


def test_doc_scope_suffices_and_preserves_question_gold_and_original():
    value = bundle()
    saved = deepcopy(value)
    result = freeze_contract(value, verify_sources=False)
    row = result['cases'][0]
    assert row['old_question'] == row['new_question'] == 'Which date?'
    assert row['new_scope']['document_ids'] == ['doc']
    assert row['task_definition_changed_from_A_whole_kb'] is True
    assert row['gold_sha256_canonical'] == 'g' * 64
    assert value == saved


def test_gold_answer_qrels_and_support_cannot_change_input_projection():
    raw = {'qid': 'q', 'question': 'Question?', 'paired_source_doc_id': 'input',
           'answers': ['A'], 'qrels': {'gold1': 1}, 'supporting_facts': ['gold2'], 'evidence_pages': [9]}
    first = project_task_input('longbench-zh', raw)
    raw.update(answers=['B'], qrels={'gold3': 1}, supporting_facts=['gold4'], evidence_pages=[99])
    assert project_task_input('longbench-zh', raw) == first
    assert 'gold1' not in json.dumps(first)


def test_missing_task_doc_id_cannot_fall_back_to_gold_or_qid():
    with pytest.raises(KeyError):
        project_task_input('longbench-zh', {'qid': 'q', 'question': 'Question?',
            'gold': {'paired_source_doc_id': 'secret'}, 'qrels': {'q': 1}})


def test_longbench_context_binding_uses_original_input_and_ignores_answers():
    task = project_task_input('longbench-zh', {'qid': 'subset:id', 'question': 'Q?',
                                            'paired_source_doc_id': 'subset:id'})
    raw = {'dataset': 'subset', '_id': 'id', 'input': 'Q?', 'context': 'Input context',
           'answers': ['SECRET']}
    corpus = {'doc_id': 'subset:id', 'title': 'Source', 'text': 'Input context'}
    proof = longbench_source_identity(task, raw, corpus)
    raw['answers'] = ['CHANGED']
    assert longbench_source_identity(task, raw, corpus) == proof
    corpus['text'] = 'Gold-chosen context'
    with pytest.raises(ValueError, match='CONTEXT_IDENTITY'):
        longbench_source_identity(task, raw, corpus)


def test_mml_uses_original_row_doc_id_only():
    task = project_task_input('mmlongbench-doc', {'qid': 'q', 'original_row': {
        'question': 'Q?', 'doc_id': 'input.pdf', 'answer': 'secret', 'evidence_pages': [4]}})
    assert task['input_source_id'] == 'input.pdf'
    assert 'secret' not in json.dumps(task)


def test_hotpot_always_keeps_full_kb_and_does_not_use_support_documents():
    value = bundle()
    value['original_inputs'][0].update(dataset='hotpotqa', qid='q', question='Q?', q0_sha256=text_sha256('Q?'))
    task = project_task_input('hotpotqa', {'id': 'q', 'question': 'Q?',
        'context': [['support', []]], 'supporting_facts': [['support', 1]], 'answer': 'secret'})
    task['input_proof'] = {'kind': 'DATASET_TASK_INPUT'}
    value['task_inputs'] = [task]
    row = freeze_contract(value, verify_sources=False)['cases'][0]
    assert row['new_scope'] == row['old_scope']
    assert row['source_identity'] is None
    assert row['task_input']['input_source_id'] is None


@pytest.mark.parametrize('mutate,error', [
    (lambda v: v['document_bindings'][0].update(mapping_basis='GOLD_ANSWER'), 'BLOCKED_UNPROVEN'),
    (lambda v: v['document_bindings'].append(deepcopy(v['document_bindings'][0])), 'BLOCKED_AMBIGUOUS'),
    (lambda v: v['document_bindings'].clear(), 'BLOCKED_MISSING_DOCUMENT'),
    (lambda v: v['document_bindings'][0].update(current_enabled=False), 'BLOCKED_UNPROVEN'),
])
def test_unproven_missing_ambiguous_disabled_binding_is_blocked(mutate, error):
    value = bundle()
    mutate(value)
    row = freeze_contract(value, verify_sources=False)['cases'][0]
    assert row['status'].startswith(error)
    assert row['new_scope'] == row['old_scope']


def test_question_mismatch_and_changed_denominator_are_rejected():
    value = bundle()
    value['task_inputs'][0]['question'] = 'Changed?'
    with pytest.raises(ValueError, match='QUESTION_IDENTITY'):
        freeze_contract(value, verify_sources=False)
    value = bundle()
    value['expected_count'] = 55
    with pytest.raises(ValueError, match='DENOMINATOR'):
        freeze_contract(value, verify_sources=False)


def test_identical_source_copy_preserves_original_kb_and_cross_kb_ambiguity_blocks():
    value = bundle()
    other = deepcopy(value['document_bindings'][0])
    other.update(knowledge_base_id='other-kb', document_id='other-doc')
    value['document_bindings'].append(other)
    row = freeze_contract(value, verify_sources=False)['cases'][0]
    assert row['new_scope']['knowledge_base_ids'] == ['kb']
    assert row['new_scope']['document_ids'] == ['doc']
    value['original_inputs'][0]['scope']['knowledge_base_ids'] = ['wrong-kb']
    row = freeze_contract(value, verify_sources=False)['cases'][0]
    assert row['status'] == 'BLOCKED_AMBIGUOUS_DOCUMENT_IDENTITY'


def test_verified_projection_rejects_field_spoofing_and_source_tampering(tmp_path):
    source = tmp_path / 'input.jsonl'
    source.write_text(json.dumps({'qid': 'q', 'question': 'Q?', 'paired_source_doc_id': 'input',
                                 'answers': ['secret']}) + '\n', encoding='utf-8')
    record = project_jsonl_inputs('longbench-zh', source)[0]
    verify_task_record(record)
    record['input_source_id'] = 'gold-doc'
    with pytest.raises(ValueError, match='PROJECTION_MISMATCH'):
        verify_task_record(record)
    record = project_jsonl_inputs('longbench-zh', source)[0]
    source.write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='FILE_HASH_MISMATCH'):
        verify_task_record(record)


def test_protocol_keeps_historical_labels_and_counts_separate():
    protocol = scoring_protocol()
    assert 'IMMUTABLE' in protocol['historical_labels']
    assert '19' in protocol['ordinal4_wording_review']['prospective_rule']
    assert 'withdrawal' in protocol['dimensions']['contradictions']
    assert 'not a factual-answer rescue' in protocol['dimensions']['clarification']


def test_inference_input_allowlist_excludes_gold_labels_and_forbids_blocked_contract():
    contract = freeze_contract(bundle(), verify_sources=False)
    inference = inference_inputs(contract)
    assert set(inference['cases'][0]) == {
        'ordinal', 'case_id', 'dataset', 'qid', 'question', 'q0_sha256', 'owner_id',
        'knowledge_base_ids', 'document_ids'}
    assert inference['gold_included'] is False
    assert 'g' * 64 not in json.dumps(inference)
    contract['status'] = 'PARTIALLY_FROZEN_BLOCKED'
    with pytest.raises(ValueError, match='NOT_FULLY_FROZEN'):
        inference_inputs(contract)
