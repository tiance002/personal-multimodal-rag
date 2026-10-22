"""Frozen boundary replay is not a fresh model quality measurement."""
import asyncio
import json

import pytest

from copy import deepcopy

from scripts.s8_6_4_replay import FIXTURE, replay, _readback_rows

READBACK = FIXTURE.parents[3] / 'docs/evaluation/evidence/s8_6_4/mml0434_neighbor_readback/current-source.stdout.json'

CASES = json.loads(FIXTURE.read_bytes())['cases']


@pytest.mark.parametrize('case',CASES,ids=lambda c:c['case_key'])
def test_saved_bge_and_search_replay_keeps_exact_originals_without_source_window(case):
    record = asyncio.run(replay(case))
    assert record['selected_sources'] and record['source_unit_calls'] == record['new_provider_calls'] == 0
    assert all(t.get('reason') != 'SOURCE_UNIT_BUDGET_EXCEEDED' for t in record['evidence_trace'])
    if case['category'] == 'rerank_low':
        assert not record['rerank_selected_ids']
        assert any(t.get('reason') == 'RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'
                   for t in record['decisions'])
    assert record['semantic_support'] == 'NOT_CHECKED' and record['model_answer_quality'] == 'NOT_RUN'


def test_mml0434_floorless_top1_expands_from_separate_verified_current_readback():
    case = next(c for c in CASES if c['case_key'] == 'mml_0434')
    snapshot = json.loads(READBACK.read_bytes())
    record = asyncio.run(replay(case, readback=snapshot, rerank_top1_floorless=True))
    anchor = '10ff171b-a054-4cbc-9ab4-4c52b8c3022f'
    previous = 'fa5675e3-e769-4f3a-8cd0-267b3d35a386'
    following = '084c5bee-f7be-4718-9c0b-510e61cb401a'
    assert len(case['source_search_results']) == len(case['raw_rerank_trace']['input_ids']) == 30
    assert record['rerank_selected_ids'] == [anchor]
    assert record['rerank_raw_trace']['response'][0] == {'index': 3, 'score': 0.022454602643847466}
    assert record['rerank_raw_trace']['response'] == case['raw_rerank_trace']['results']
    assert record['rerank_selected_scores'][anchor] == 0.1174195190541927
    fallback = asyncio.run(replay(case, readback=snapshot, rerank_top1_floorless=False))
    assert fallback['rerank_selected_ids'] == []
    assert fallback['rerank_raw_trace']['response'] == case['raw_rerank_trace']['results']
    assert fallback['anchor_exact_neighbor_text_in_final'] is True
    assert [s['id'] for s in record['selected_sources']] == [anchor, previous, following]
    assert [(s['start'], s['end'], s['characters']) for s in record['selected_sources']] == [
        (9077, 9210, 133), (9040, 9075, 35), (9212, 9281, 69)]
    assert all(s['source_version'] == '10bce0ac-2fa8-46c1-8fc4-067d575d7370'
               for s in record['selected_sources'])
    assert record['cat_vs_dog_exact_text_in_final'] is True
    assert record['anchor_exact_neighbor_text_in_final'] is True
    assert record['new_provider_calls'] == record['source_unit_calls'] == 0
    assert {s['id'] for s in record['prepared_source_identity_map']} == {anchor, previous, following}
    assert all(s['handle'].startswith('c') for s in record['prepared_source_identity_map'])


@pytest.mark.parametrize(('mutation', 'error'), [
    ('duplicate', 'CURRENT_SOURCE_READBACK_DUPLICATE_OR_UNEXPECTED_ID'),
    ('scope', 'CURRENT_SOURCE_READBACK_ROW_SCOPE_OR_VERSION_INVALID'),
    ('version', 'CURRENT_SOURCE_READBACK_ROW_SCOPE_OR_VERSION_INVALID'),
])
def test_current_readback_rejects_duplicate_or_conflicting_scope_version(mutation, error):
    case = next(c for c in CASES if c['case_key'] == 'mml_0434')
    snapshot = json.loads(READBACK.read_bytes())
    search = next(r for r in case['source_search_results'] if r['id'] == '10ff171b-a054-4cbc-9ab4-4c52b8c3022f')
    from scripts.s8_6_4_replay import result
    anchor = result(search)
    changed = deepcopy(snapshot)
    if mutation == 'duplicate':
        changed['chunks'].append(deepcopy(changed['chunks'][0]))
    elif mutation == 'scope':
        changed['chunks'][0]['knowledge_base_id'] = 'out-of-scope'
    else:
        changed['chunks'][0]['metadata']['source_version'] = 'stale-version'
    with pytest.raises(ValueError, match=error):
        _readback_rows(changed, case['scope'], anchor)
