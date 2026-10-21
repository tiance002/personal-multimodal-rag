"""Literal prefix search clues never establish an entity or event venue."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge, sentence_table, fact_windows
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.types import SearchResult
from test_bridge_rescue import Models, Repository, row, CTX


OBJECT = 'The Arbor Poetry Advanced Seminar'
PREFIX = 'The Arbor Poetry'
QUESTION = 'Where is the seminar Alice Bennett directed in 2011–2014 located?'
FIXTURE = Path(__file__).parent / 'fixtures' / 's8_4_bridge' / 'inputs.json'


def make_case(*, object=OBJECT, candidates=None, source_suffix=''):
    quote = f'Alice Bennett directed {object}{source_suffix} from 2011–2014.'
    main = row('first', quote, score=.8)
    candidates = candidates if candidates is not None else [
        row('hint', f'{PREFIX} is located in Harbor City, Canada.', document='place')]
    selected = next(s for s in sentence_table(fact_windows(main), {main.id:main}).values()
                    if s.role == 'main')
    extraction = dict(status='FIRST_HOP_CANDIDATE', sentence_id=selected.sentence_id,
        subject='Alice Bennett', subject_resolution='EXPLICIT', predicate='directed',
        object=object, time='2011–2014', target_attribute='location',
        bridge_search_phrases=[object])
    originals = [main, *candidates]
    state = PipelineState(query=QUESTION, rewrite_query=QUESTION,
        targets=(SearchTarget('kb'),), enable_rewrite=False, enable_expansion=False,
        bridge_rescue_mode='cheap_bridge_v1', bridge_cheap_model_id='cheap', rerank_model_id='bge',
        search_results=deepcopy(originals), rerank_results=[deepcopy(main)],
        merge_results=[deepcopy(main)], rerank_threshold=.3, evidence_trace=[
            {'stage':'rerank_raw', 'input_ids':[r.id for r in originals],
             'response':[{'index':i, 'score':.95 if i == 0 else .02}
                         for i in range(len(originals))]},
            {'stage':'rerank_filter_mmr', 'accepted_ids':[main.id], 'mmr_ids':[main.id]}])
    return state, Repository(originals), Models(json.dumps(extraction))


def run_case(state, repository, models, context=CTX):
    return asyncio.run(rescue_bridge(state, context, repository, models))


def assert_institution_hint(state, repository, models, hint_id, full_object):
    assert len(models.chat_calls) == len(models.rerank_calls) == 1
    assert models.rerank_calls[0][0] == f'Where is {full_object} located?'
    assert [r.id for r in state.rescue_evidence] == [hint_id]
    rescued = state.rescue_evidence[0]
    assert rescued.content == repository.rows[hint_id].content
    assert rescued.score == repository.rows[hint_id].score
    details = rescued.metadata['bridge_rescue']
    assert details['object'] == full_object
    assert details['matched_phrase'] != full_object
    assert details['entity_bridge_verified'] is False
    assert details['final_answer_supported'] is False
    assert details['relation_status'] == 'INSTITUTION_LOCATION_ONLY_EVENT_VENUE_UNVERIFIED'
    clue_trace = next(t for t in state.bridge_trace if t.get('decision') == 'LITERAL_PREFIX_SEARCH_CLUES')
    assert clue_trace['object'] == full_object
    assert clue_trace['provenance'] == 'DETERMINISTIC_LITERAL_PREFIX'
    assert clue_trace['entity_aliases_verified'] is False
    assert details['matched_phrase'] in clue_trace['search_clues']
    assert 1 <= len(clue_trace['search_clues']) <= 3


def test_full_object_only_extraction_can_use_existing_literal_institution_prefix():
    state, repository, models = make_case()
    before = deepcopy((state.search_results, state.rerank_results, state.merge_results))
    run_case(state, repository, models)
    assert_institution_hint(state, repository, models, 'hint', OBJECT)
    assert before == (state.search_results, state.rerank_results, state.merge_results)
    finalized = asyncio.run(into_chat_message(state, CTX))
    assert finalized.result_ids == ('first', 'hint')
    assert 'supports="institution_location_only"' in finalized.rendered_contexts
    assert 'seminar_venue="unverified"' in finalized.rendered_contexts
    assert 'final_answer_supported="false"' in finalized.rendered_contexts


def test_saved_a1_full_object_only_response_reaches_existing_frozen_second_hop():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135'
    saved = next(c for c in json.loads(raw)['cases'] if c['case_id'] == 'frost-A')
    originals = [SearchResult(**r) for r in saved['source_rows']]
    state = PipelineState(query=saved['query'], rewrite_query=saved['rewrite_query'],
        targets=tuple(SearchTarget(kb) for kb in saved['knowledge_base_ids']),
        enable_rewrite=False, enable_expansion=False, bridge_rescue_mode='cheap_bridge_v1',
        bridge_cheap_model_id='cheap', rerank_model_id='bge',
        search_results=[SearchResult(**r) if r else None for r in saved['search_results']],
        rerank_results=[SearchResult(**r) for r in saved['rerank_results']],
        merge_results=[SearchResult(**r) for r in saved['merge_results']],
        evidence_trace=deepcopy(saved['evidence_trace']))
    # The actual A1 response selected only the complete object as its clue.
    extraction = dict(status='FIRST_HOP_CANDIDATE', sentence_id='s-c9a81754f7673886cbb035bf',
        subject='She', subject_resolution='UNVERIFIED', predicate='served as the Director of',
        object='The Frost Place Advanced Seminar', time='2007–2010', target_attribute='location',
        bridge_search_phrases=['The Frost Place Advanced Seminar'])
    models, repository = Models(json.dumps(extraction)), Repository(originals)
    context = CallContext('saved-a1-prefix-replay', TrustedScope('owner', tuple(saved['knowledge_base_ids'])))
    before = deepcopy((state.search_results, state.rerank_results, state.merge_results))
    run_case(state, repository, models, context)
    assert len(state.search_results) == 30
    assert_institution_hint(state, repository, models, '31417c7c-7abf-4723-956a-74bf7ee14683',
                            extraction['object'])
    assert before == (state.search_results, state.rerank_results, state.merge_results)
    finalized = asyncio.run(into_chat_message(state, context))
    assert finalized.result_ids[-1] == '31417c7c-7abf-4723-956a-74bf7ee14683'
    assert 'supports="institution_location_only"' in finalized.rendered_contexts
    assert 'final_answer_supported="false"' in finalized.rendered_contexts


@pytest.mark.parametrize('content', [
    'The Arbor Poetry Annex is located in Harbor City, Canada.',
    'The Arbor Poetry Advanced Annex is located in Harbor City, Canada.',
    'The headquarters of The Arbor Poetry is located in Harbor City, Canada.',
    'The Arbor Poetry Foundation said the seminar is located in Harbor City, Canada.',
    'The Arbor Poetry may be located in Harbor City, Canada.',
])
def test_prefix_collision_or_non_direct_location_never_reaches_bge(content):
    state, repository, models = make_case(candidates=[row('collision', content, document='place')])
    run_case(state, repository, models)
    assert len(models.chat_calls) == 1 and not models.rerank_calls
    assert not state.rescue_evidence


def test_prefix_with_only_one_nonarticle_word_is_not_a_useful_clue():
    state, repository, models = make_case(object='The Advanced Seminar', candidates=[
        row('generic', 'The Advanced is located in Harbor City, Canada.', document='place')])
    run_case(state, repository, models)
    assert not models.rerank_calls and not state.rescue_evidence


def test_prefix_fallback_does_not_turn_object_suffix_into_verified_first_hop():
    state, repository, models = make_case(source_suffix=' Annex')
    run_case(state, repository, models)
    assert not models.rerank_calls and not state.rescue_evidence
    assert not any(t.get('decision') == 'LITERAL_PREFIX_SEARCH_CLUES' for t in state.bridge_trace)
    assert any(t.get('reason') == 'RELATION_UNVERIFIED' for t in state.bridge_trace)


def test_conflicting_direct_prefix_locations_stop_before_bge():
    state, repository, models = make_case(candidates=[
        row('north', f'{PREFIX} is located in Harbor City, Canada.', document='north'),
        row('south', f'{PREFIX} is located in Ocean City, Canada.', document='south')])
    run_case(state, repository, models)
    assert not models.rerank_calls and not state.rescue_evidence
    assert any(t.get('reason') == 'AMBIGUOUS_SAME_NAME_LOCATION' for t in state.bridge_trace)


def test_prefix_fallback_preserves_eight_candidate_bound():
    candidates = [row(f'hint-{i}', f'{PREFIX} is located in Harbor City, Canada.', document=f'place-{i}')
                  for i in range(9)]
    state, repository, models = make_case(candidates=candidates)
    run_case(state, repository, models)
    assert not models.rerank_calls and not state.rescue_evidence
    assert any(t.get('reason') == 'CANDIDATE_COUNT' and t.get('count') == 9 for t in state.bridge_trace)


def test_prefix_fallback_never_adds_an_initially_accepted_source():
    state, repository, models = make_case()
    state.evidence_trace[-1]['accepted_ids'].append('hint')
    run_case(state, repository, models)
    assert not models.rerank_calls and not state.rescue_evidence


def test_existing_exact_object_pool_is_not_broadened_by_prefix_fallback():
    exact = row('exact', f'{OBJECT} is located in Harbor City, Canada.', document='exact')
    prefix = row('prefix', f'{PREFIX} is located in Ocean City, Canada.', document='prefix')
    state, repository, models = make_case(candidates=[exact, prefix])
    run_case(state, repository, models)
    assert models.rerank_calls[0][1] == (exact.content,)
    assert [r.id for r in state.rescue_evidence] == ['exact']
    assert state.rescue_evidence[0].metadata['bridge_rescue']['entity_bridge_verified'] is True
    assert not any(t.get('decision') == 'LITERAL_PREFIX_SEARCH_CLUES' for t in state.bridge_trace)
