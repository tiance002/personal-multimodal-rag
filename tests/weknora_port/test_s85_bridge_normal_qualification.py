"""Existing normal institution facts retain explicit bridge limitations."""
import asyncio
from copy import deepcopy
import json

import pytest

from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge, sentence_table, fact_windows
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.types import context_results
from test_bridge_rescue import CTX, row
from test_s85_bridge_prefix_clues import make_case, OBJECT, PREFIX

MAIN_ID = '30ca6459-b928-4397-98fb-9bf986ff3d01'
HINT_ID = '30ca6459-b928-4397-98fb-9bf986ff3d02'
MAIN_DOC = '30ca6459-b928-4397-98fb-9bf986ff3d03'
HINT_DOC = '30ca6459-b928-4397-98fb-9bf986ff3d04'


def normal_case(origin):
    state, repository, models = make_case(candidates=[
        row(HINT_ID, f'{PREFIX} is located in Harbor City, Canada.', document=HINT_DOC)])
    # Use production-shaped source identities through the real generation
    # encoder; toy IDs such as "doc"/"hint" collide with its XML words.
    for main in (state.search_results[0], state.rerank_results[0], state.merge_results[0],
                 repository.rows['first']):
        main.id, main.knowledge_id = MAIN_ID, MAIN_DOC
    repository.rows = {r.id:r for r in repository.rows.values()}
    state.evidence_trace[0]['input_ids'][0] = MAIN_ID
    state.evidence_trace[-1]['accepted_ids'][0] = MAIN_ID
    state.evidence_trace[-1]['mmr_ids'][0] = MAIN_ID
    output = json.loads(models.content)
    output['sentence_id'] = next(s.sentence_id for s in sentence_table(
        fact_windows(state.search_results[0]), {MAIN_ID:state.search_results[0]}).values()
        if s.role == 'main')
    hint = deepcopy(state.search_results[1])
    state.rerank_results.append(deepcopy(hint))
    state.merge_results.append(deepcopy(hint))
    state.evidence_trace[-1]['accepted_ids'].append(hint.id)
    if origin == 'model-prefix':
        output['bridge_search_phrases'].append(PREFIX)
    models.content = json.dumps(output)
    return state, repository, models


def assert_unverified_qualification(text):
    assert 'role="bridge_location_hint"' in text
    assert 'supports="institution_location_only"' in text
    assert 'seminar_venue="unverified"' in text
    assert 'final_answer_supported="false"' in text
    assert 'role="bridge_supplement"' not in text


@pytest.mark.parametrize('origin', ['program-prefix', 'model-prefix'])
def test_normal_institution_fact_is_qualified_in_both_final_encoders(origin):
    state, repository, models = normal_case(origin)
    before = deepcopy((state.search_results, state.rerank_results, state.merge_results))
    asyncio.run(rescue_bridge(state, CTX, repository, models))
    assert len(models.chat_calls) == 1 and not models.rerank_calls
    assert not state.rescue_evidence
    assert before == (state.search_results, state.rerank_results, state.merge_results)
    decisions = [t.get('decision') for t in state.bridge_trace]
    assert 'NORMAL_INSTITUTION_LOCATION_PRESENT' in decisions
    assert 'ENTITY_BRIDGE_UNVERIFIED' in decisions
    assert 'NORMAL_EVIDENCE_ALREADY_COMPLETE' not in decisions

    views = context_results(state)
    assert [(r.id, r.content, r.score) for r in views] == [
        (r.id, r.content, r.score) for r in state.merge_results]
    qualified = next(r for r in views if r.id == HINT_ID)
    original = next(r for r in state.merge_results if r.id == HINT_ID)
    assert qualified is not original
    assert 'bridge_normal_qualification' in qualified.metadata
    assert 'bridge_normal_qualification' not in original.metadata
    assert 'bridge_rescue' not in qualified.metadata
    details = qualified.metadata['bridge_normal_qualification']
    assert details['source_chunk_id'] == HINT_ID
    assert details['object'] == OBJECT and details['matched_phrase'] == PREFIX
    assert details['entity_bridge_verified'] is False
    assert details['final_answer_supported'] is False

    finalized = asyncio.run(into_chat_message(state, CTX))
    assert finalized.result_ids == (MAIN_ID, HINT_ID)
    assert_unverified_qualification(finalized.rendered_contexts)
    assert original.content in finalized.rendered_contexts
    request = GenerationRequest(run_id='run', session_id='session', message_id='message',
        q0=state.query, finalized=finalized, evidence=tuple(Evidence.from_result(r) for r in views))
    prepared = prepare_generation(request)
    assert_unverified_qualification(prepared.messages[-1].content)
    final_trace = next(t for t in reversed(state.bridge_trace)
                       if t.get('decision') == 'FINAL_ANSWER_UNVERIFIED')
    assert final_trace['evidence_chain_supported'] is False
    assert final_trace['generated_answer_verified'] is False
    assert before == (state.search_results, state.rerank_results, state.merge_results)


def test_institution_fact_in_retained_first_hop_body_is_also_qualified():
    state, repository, models = make_case(candidates=[])
    location = f'\n{PREFIX} is located in Harbor City, Canada.'
    for main in (state.search_results[0], state.rerank_results[0], state.merge_results[0],
                 repository.rows['first']):
        main.content += location
        main.end_at += len(location)
    output = json.loads(models.content)
    output['bridge_search_phrases'].append(PREFIX)
    models.content = json.dumps(output)
    before = deepcopy((state.search_results, state.rerank_results, state.merge_results))
    asyncio.run(rescue_bridge(state, CTX, repository, models))
    assert not models.rerank_calls and not state.rescue_evidence
    assert before == (state.search_results, state.rerank_results, state.merge_results)
    finalized = asyncio.run(into_chat_message(state, CTX))
    assert finalized.result_ids == ('first',)
    assert_unverified_qualification(finalized.rendered_contexts)


def test_exact_object_normal_location_keeps_existing_complete_path():
    exact = row('exact', f'{OBJECT} is located in Harbor City, Canada.', document='exact')
    state, repository, models = make_case(candidates=[exact])
    state.rerank_results.append(deepcopy(exact))
    state.merge_results.append(deepcopy(exact))
    state.evidence_trace[-1]['accepted_ids'].append(exact.id)
    before = deepcopy((state.search_results, state.rerank_results, state.merge_results))
    asyncio.run(rescue_bridge(state, CTX, repository, models))
    assert not models.rerank_calls and not state.rescue_evidence
    assert any(t.get('decision') == 'NORMAL_EVIDENCE_ALREADY_COMPLETE' for t in state.bridge_trace)
    assert not any(t.get('decision') == 'NORMAL_INSTITUTION_LOCATION_PRESENT' for t in state.bridge_trace)
    assert not getattr(state, 'bridge_normal_qualifications', {})
    assert before == (state.search_results, state.rerank_results, state.merge_results)


@pytest.mark.parametrize('change', ['lost-quote', 'wrong-doc', 'wrong-version', 'unbound-id'])
def test_qualification_copy_requires_surviving_original_quote_and_provenance(change):
    state, repository, models = normal_case('model-prefix')
    asyncio.run(rescue_bridge(state, CTX, repository, models))
    assert getattr(state, 'bridge_normal_qualifications', {})
    target = next(r for r in state.merge_results if r.id == HINT_ID)
    if change == 'lost-quote':
        target.content = 'The location sentence did not survive this view.'
    elif change == 'wrong-doc':
        target.knowledge_id = 'other-doc'
    elif change == 'wrong-version':
        target.metadata['source_version'] = 'other-version'
    else:
        target.id = 'unrelated-id'
    before = deepcopy(state.merge_results)
    views = context_results(state)
    assert not any('bridge_normal_qualification' in r.metadata for r in views)
    assert state.merge_results == before


def test_normal_qualification_records_are_request_local():
    first, repository, models = normal_case('model-prefix')
    asyncio.run(rescue_bridge(first, CTX, repository, models))
    second, _, _ = normal_case('model-prefix')
    assert getattr(first, 'bridge_normal_qualifications', {})
    assert not getattr(second, 'bridge_normal_qualifications', {})
    assert all('bridge_normal_qualification' not in r.metadata for r in context_results(second))
