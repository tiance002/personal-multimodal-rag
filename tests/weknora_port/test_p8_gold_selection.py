"""P8 selection boundaries: exact objects, deterministic selection, isolated Gold."""
import io
import json
import zipfile

import pytest

from eval_center.public_data import PublicCase
from scripts.p8_gold_selection import (archive_ref, choose_cases, reject_gold_fields,
                                       sha, verify_hotpot_original, verify_selection)


def case(qid, qrels, **kwargs):
    return PublicCase(qid, 'question', qrels, 'development', **kwargs)


def test_exact_package_object_rejects_hash_change_and_traversal():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr('objects/ok', b'original')
    with zipfile.ZipFile(stream) as z:
        ref = {'path': 'objects/ok', 'sha256': sha(b'original'), 'bytes': 8}
        assert archive_ref(z, ref) == b'original'
        with pytest.raises(ValueError, match='IDENTITY_MISMATCH'):
            archive_ref(z, {**ref, 'sha256': sha(b'changed')})
        with pytest.raises(ValueError, match='UNSAFE'):
            archive_ref(z, {**ref, 'path': '../objects/ok'})


def test_qrels_selection_skips_incomplete_positive_mapping_and_keeps_distractors():
    rows = [case('z', {'positive': 1}), case('b', {'missing': 1}),
            case('a', {'positive': 1, 'judged-zero': 0})]
    selected, ids = choose_cases('scifact', rows, {'positive', 'd1', 'd2', 'judged-zero'})
    assert [c.qid for c in selected] == ['a', 'z']
    assert {'positive', 'd1', 'd2'} <= ids
    with pytest.raises(ValueError, match='TWO_MAPPABLE'):
        choose_cases('miracl-zh', rows[:2], {'positive', 'd1', 'd2'})


def test_paired_context_must_exist_and_hotpot_keeps_all_ten_contexts():
    rows = [case('multifieldqa_zh:a', {'m': 1}, paired_source_doc_id='m'),
            case('dureader:a', {'d': 1}, paired_source_doc_id='d')]
    with pytest.raises(ValueError, match='OUTSIDE_PACKAGE'):
        choose_cases('longbench-zh', rows, {'m'})
    ids = [str(i) for i in range(10)]
    selected, sources = choose_cases('hotpotqa', [case('h', {'0': 1, '1': 1})], set(ids), {'h': ids})
    assert len(selected) == 1 and sources == set(ids)


@pytest.mark.parametrize('key', ['answers', 'qrels', 'supporting_facts', 'evidence_pages'])
def test_gold_payload_never_enters_prepared_source(key):
    with pytest.raises(ValueError, match='GOLD_FIELD'):
        reject_gold_fields({'blocks': [{'metadata': {key: 'sentinel'}}]})
    reject_gold_fields({'markdown_content': 'The word answers can naturally occur in original text.'})


def test_model_visible_case_has_no_gold_keys():
    selection = {'schema': 'p8-original-question-selection/v1', 'selection_rules': {}, 'datasets': [
        {'name': 'scifact', 'version': 'v', 'files': [], 'cases': [{'qid': '1', 'question': 'q', 'answers': ['gold']}]}]}
    with pytest.raises(ValueError, match='GOLD_FIELD_IN_SELECTION'):
        verify_selection(selection)


def test_original_supporting_fact_annotations_cannot_silently_change():
    source_id = 'hotpotqa-context:' + sha(('title\0' + json.dumps(['sentence'], separators=(',', ':'))).encode())
    original = {'question': 'question', 'answer': 'answer',
                'context': {'title': ['title'], 'sentences': [['sentence']]},
                'supporting_facts': {'title': ['title'], 'sent_id': [0]}}
    current = case('h', {source_id: 1}, answers=('answer',),
                   evaluation_metadata={'supporting_facts': [
                       {'document_id': source_id, 'sentence_index_zero_based': 0}]})
    verify_hotpot_original(current, original, [source_id])
    original['supporting_facts']['sent_id'] = [1]
    with pytest.raises(ValueError, match='SUPPORTING_FACT_GOLD_CHANGED'):
        verify_hotpot_original(current, original, [source_id])
