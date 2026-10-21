"""S8.5 bounded original-source identity proof contracts.

No model, repository, extraction JSON, sentence-ID implementation, or network
is involved here.  The caller supplies scoped originals and the exact selected
source span.  Production implementation is intentionally deferred to Stage 3.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from backend.weknora_port.retrieval.bridge_identity import prove_identity
from backend.weknora_port.retrieval.search import RetrievalScopeError
from backend.weknora_port.retrieval.types import SearchResult


QUESTION = 'Where is the seminar Alice Bennett directed in 2011–2014 located?'
FULL_NAME = 'Alice Bennett'
BIOGRAPHY = 'Alice Bennett is an American poet.'
RESET = 'Bennett was co-editor of "Annual Review".'
TARGET = 'She directed the Arbor Poetry Seminar from 2011–2014.'
CONTINUATION = ('She was judge for a literary award. She grew up in Harbor City. '
                'She earned her degree from Eastern College. '
                'She has taught at Harbor University.')
FIXTURE = Path(__file__).parent / 'fixtures' / 's8_4_bridge' / 'inputs.json'
FROZEN_SHA = 'e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135'
SOURCE_ERRORS = (ValueError, PermissionError, RetrievalScopeError)


def chain(texts, *, overlap=0):
    """Build actual reciprocal source links, never an artificial text join."""
    rows = []
    offset = 0
    for index, text in enumerate(texts):
        if index:
            offset -= overlap
        rows.append(SearchResult(
            id=f'original-{index}', content=text, score=.8,
            knowledge_id='biography', knowledge_base_id='kb', chunk_type='text',
            chunk_index=index, start_at=offset, end_at=offset + len(text),
            pre_chunk_id=f'original-{index - 1}' if index else '',
            next_chunk_id=f'original-{index + 1}' if index + 1 < len(texts) else '',
            metadata={'source_version': 'v1', 'tenant_id': 7, 'owner_id': 'owner'},
            chunk_metadata={'source_version': 'v1'}))
        offset += len(text)
    return rows


def proof(rows, *, selected=TARGET, normal=None, question=QUESTION):
    selected_row = rows[-1]
    start = selected_row.content.index(selected)
    return prove_identity(question, rows, selected_chunk_id=selected_row.id,
                          quote_start=start, quote_end=start + len(selected),
                          normal_results=rows if normal is None else normal)


def assert_original_quotes(result, rows):
    sources = {row.id: row for row in rows}
    assert result.anchor_quotes
    for anchor in result.anchor_quotes:
        source = sources[anchor.source_chunk_id]
        assert type(anchor.quote_start) is int and type(anchor.quote_end) is int
        assert 0 <= anchor.quote_start < anchor.quote_end <= len(source.content)
        assert source.content[anchor.quote_start:anchor.quote_end] == anchor.quote


def test_explicit_full_name_relation_needs_no_predecessor():
    target = 'Alice Bennett directed the Arbor Poetry Seminar from 2011–2014.'
    rows = chain([target])
    result = proof(rows, selected=target)
    assert result.verified and result.resolved_name == FULL_NAME
    assert result.final_context_identity_supported
    assert_original_quotes(result, rows)


def test_original_full_name_and_surname_reset_establish_pronoun_identity():
    rows = chain([BIOGRAPHY, RESET + ' ' + CONTINUATION, TARGET])
    before = deepcopy(rows)
    result = proof(rows)
    assert result.verified and result.resolved_name == FULL_NAME
    assert result.final_context_identity_supported
    assert_original_quotes(result, rows)
    assert any(BIOGRAPHY in anchor.quote for anchor in result.anchor_quotes)
    assert any(RESET in anchor.quote for anchor in result.anchor_quotes)
    assert rows == before


def test_explicit_surname_reset_can_reestablish_target_after_other_people():
    rows = chain([BIOGRAPHY,
                  'Film-maker Jay Rosen made a film narrated by George Kline.',
                  RESET + ' ' + CONTINUATION, TARGET])
    result = proof(rows)
    assert result.verified and result.resolved_name == FULL_NAME
    assert result.final_context_identity_supported
    assert_original_quotes(result, rows)


@pytest.mark.parametrize('case_id,context_supported', [('frost-A', True), ('frost-B', False)])
def test_frozen_frost_identity_and_final_context_support_are_separate(case_id, context_supported):
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FROZEN_SHA
    saved = next(case for case in json.loads(raw)['cases'] if case['case_id'] == case_id)
    sources = {row['id']: SearchResult(**row) for row in saved['source_rows']}
    current = sources['ec07a1fd-8a8e-4f23-a7d6-65d385d2343b']
    rows = [current]
    for _ in range(3):
        rows.append(sources[rows[-1].pre_chunk_id])
    rows.reverse()
    selected = current.content[155:311]
    normal = [SearchResult(**row) for row in saved['merge_results']]
    before = deepcopy((rows, normal))
    result = proof(rows, selected=selected, normal=normal, question=saved['query'])
    assert result.verified and result.resolved_name == 'Jeanne Marie Beaumont'
    assert result.final_context_identity_supported is context_supported
    assert_original_quotes(result, rows)
    assert any(anchor.source_chunk_id == rows[0].id for anchor in result.anchor_quotes)
    assert any(anchor.source_chunk_id == rows[2].id for anchor in result.anchor_quotes)
    assert (rows, normal) == before


@pytest.mark.parametrize('intervening', [
    'Alice Jones was judge for a literary award.',
    'She hired the poet Alice Jones for the workshop.',
    'She worked with her colleague Alice Jones.',
    'She served a program founded by Alice Jones.',
    'She, whose colleague Alice Jones led a workshop, earned a degree.',
    'Her sister was Alice Jones.',
    'She said, "Alice Jones directed that workshop."',
    'The technician was appointed as director.',
    'An unidentified woman led another seminar.',
    'I taught the poetry workshop.',
    'We directed the poetry workshop.',
    'You directed the poetry workshop.',
    'He directed another workshop.',
])
def test_person_switch_or_unsupported_discourse_after_reset_is_unverified(intervening):
    rows = chain([BIOGRAPHY, RESET, intervening + ' ' + TARGET])
    result = proof(rows)
    assert not result.verified
    assert not result.resolved_name
    assert not result.final_context_identity_supported


@pytest.mark.parametrize('bad_reset', [
    'Her colleague Bennett was co-editor of "Annual Review".',
    'Bennett\'s mother was co-editor of "Annual Review".',
    'Joanna Bennett was co-editor of "Annual Review".',
    'The review founded by Bennett was widely published.',
])
def test_embedded_possessive_or_other_person_surname_is_not_a_reset(bad_reset):
    rows = chain([BIOGRAPHY, 'Jay Rosen made a film.', bad_reset, TARGET])
    assert not proof(rows).verified


def test_same_surname_second_person_makes_surname_reset_ambiguous():
    rows = chain([BIOGRAPHY + ' Clare Bennett is another poet.', RESET, TARGET])
    assert not proof(rows).verified


def test_question_name_never_supplies_missing_source_anchor():
    rows = chain([RESET, CONTINUATION, TARGET])
    result = proof(rows)
    assert not result.verified and not result.resolved_name
    assert not result.final_context_identity_supported


def test_source_anchor_must_match_question_target_as_whole_name():
    rows = chain([BIOGRAPHY, RESET, TARGET])
    assert not proof(rows, question=QUESTION.replace(FULL_NAME, 'Joanna Bennett')).verified


@pytest.mark.parametrize('nonhuman', [
    'Alice Bennett is an educational center.',
    'Alice Bennett was a conference venue.',
    'Alice Bennett was the title of a book.',
])
def test_full_name_like_nonhuman_subject_is_not_a_person_anchor(nonhuman):
    rows = chain([nonhuman, RESET, TARGET])
    assert not proof(rows).verified


def test_missing_predecessor_step_is_rejected():
    rows = chain([BIOGRAPHY, 'Her work has appeared widely.', RESET, TARGET])
    with pytest.raises(SOURCE_ERRORS):
        proof([rows[0], rows[2], rows[3]])


def test_more_than_three_predecessors_is_rejected_before_resolution():
    rows = chain([BIOGRAPHY, 'Her work has appeared widely.',
                  'Her work has received awards.', RESET, TARGET])
    with pytest.raises(SOURCE_ERRORS):
        proof(rows)


@pytest.mark.parametrize('change', [
    lambda rows: setattr(rows[1], 'next_chunk_id', 'wrong-next'),
    lambda rows: setattr(rows[-1], 'pre_chunk_id', 'wrong-previous'),
    lambda rows: setattr(rows[-1], 'chunk_index', 9),
    lambda rows: setattr(rows[1], 'id', rows[0].id),
    lambda rows: setattr(rows[1], 'knowledge_id', 'other-document'),
    lambda rows: setattr(rows[1], 'knowledge_base_id', 'other-kb'),
    lambda rows: rows[1].metadata.update(source_version='other-version'),
    lambda rows: rows[1].chunk_metadata.update(source_version='other-version'),
    lambda rows: rows[1].metadata.update(tenant_id=8),
    lambda rows: rows[1].metadata.update(owner_id='other-owner'),
    lambda rows: rows[1].metadata.pop('owner_id'),
    lambda rows: setattr(rows[1], 'content_rewritten', True),
    lambda rows: setattr(rows[1], 'content_revision', 1),
    lambda rows: setattr(rows[1], 'is_enabled', False),
    lambda rows: setattr(rows[1], 'end_at', rows[1].end_at + 1),
    lambda rows: setattr(rows[1], 'chunk_type', 'summary'),
])
def test_original_chain_scope_identity_and_locator_failures_reject(change):
    rows = chain([BIOGRAPHY, RESET + ' ' + CONTINUATION, TARGET])
    change(rows)
    with pytest.raises(SOURCE_ERRORS):
        proof(rows)


def test_optional_owner_and_tenant_can_be_absent_from_every_original():
    rows = chain([BIOGRAPHY, RESET + ' ' + CONTINUATION, TARGET])
    for row in rows:
        row.metadata.pop('tenant_id')
        row.metadata.pop('owner_id')
    assert proof(rows).verified


def test_original_indices_are_nonnegative_even_when_sequence_is_adjacent():
    rows = chain([BIOGRAPHY, RESET, TARGET])
    for row in rows:
        row.chunk_index -= len(rows)
    with pytest.raises(SOURCE_ERRORS):
        proof(rows)


def test_exact_overlap_is_validated_without_changing_original_offsets():
    overlap = 'She earned her degree from Eastern College.'
    rows = chain([BIOGRAPHY, RESET + ' ' + overlap, overlap + ' ' + TARGET])
    rows[-1].start_at -= len(overlap)
    rows[-1].end_at -= len(overlap)
    result = proof(rows)
    assert result.verified and result.final_context_identity_supported
    assert_original_quotes(result, rows)


def test_claimed_overlap_with_different_original_text_is_rejected():
    overlap = 'She earned her degree from Eastern College.'
    rows = chain([BIOGRAPHY, RESET + ' ' + overlap, 'Different text overlaps here. ' + TARGET])
    rows[-1].start_at -= len(overlap)
    rows[-1].end_at -= len(overlap)
    with pytest.raises(SOURCE_ERRORS):
        proof(rows)


@pytest.mark.parametrize('start,end', [(True, 12), (-1, 12), (0, 9999), (10, 2)])
def test_selected_source_span_requires_exact_unicode_integer_bounds(start, end):
    rows = chain([BIOGRAPHY, RESET, TARGET])
    with pytest.raises(SOURCE_ERRORS):
        prove_identity(QUESTION, rows, selected_chunk_id=rows[-1].id,
                       quote_start=start, quote_end=end, normal_results=rows)


def test_selected_source_must_be_last_in_verified_chain():
    rows = chain([BIOGRAPHY, RESET, TARGET])
    with pytest.raises(SOURCE_ERRORS):
        prove_identity(QUESTION, rows, selected_chunk_id=rows[0].id,
                       quote_start=0, quote_end=len(BIOGRAPHY), normal_results=rows)


def test_identity_proof_alone_does_not_make_missing_final_anchor_supported():
    rows = chain([BIOGRAPHY, RESET + ' ' + CONTINUATION, TARGET])
    result = proof(rows, normal=rows[1:])
    assert result.verified and result.resolved_name == FULL_NAME
    assert not result.final_context_identity_supported


@pytest.mark.parametrize('forgery', ['ancestry-only', 'unrelated-document', 'wrong-version',
                                    'text-only', 'wrong-tenant', 'wrong-owner'])
def test_final_context_requires_original_quote_and_source_provenance(forgery):
    rows = chain([BIOGRAPHY, RESET + ' ' + CONTINUATION, TARGET])
    impostor = deepcopy(rows[0])
    if forgery == 'ancestry-only':
        impostor.id = 'merged'
        impostor.sub_chunk_ids = [rows[0].id]
        impostor.content = 'The anchor was removed from this final context.'
    elif forgery == 'unrelated-document':
        impostor.knowledge_id = 'unrelated-document'
    elif forgery == 'wrong-version':
        impostor.metadata['source_version'] = 'other-version'
    elif forgery == 'wrong-tenant':
        impostor.metadata['tenant_id'] = 8
    elif forgery == 'wrong-owner':
        impostor.metadata['owner_id'] = 'other-owner'
    else:
        impostor.id = 'unbound-text'
    result = proof(rows, normal=[impostor, *rows[1:]])
    assert result.verified
    assert not result.final_context_identity_supported


def test_final_context_requires_selected_relation_quote_as_well_as_identity_anchors():
    rows = chain([BIOGRAPHY, RESET + ' ' + CONTINUATION, TARGET])
    result = proof(rows, normal=rows[:-1])
    assert result.verified
    assert not result.final_context_identity_supported
