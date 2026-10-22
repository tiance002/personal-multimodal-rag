"""Frozen boundary replay is not a fresh model quality measurement."""
import asyncio
import json

import pytest

from scripts.s8_6_4_replay import FIXTURE, replay

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
