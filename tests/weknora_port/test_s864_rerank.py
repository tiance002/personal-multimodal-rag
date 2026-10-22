"""S8.6.4 offline rerank decisions; synthetic candidates are not relevance proof."""
import asyncio
from copy import deepcopy

import pytest

from backend.weknora_port.models.types import CallCancelled, ProviderError, RerankResult
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.rerank import InvalidRerankResponse, rerank
from backend.weknora_port.retrieval.search import SearchError, SearchNothing
from test_pipeline import CTX, Model, Registry, row, state


def decision(s):
    return next(item for item in reversed(s.evidence_trace) if item.get('reason'))


def test_search_empty_is_distinct_and_never_looks_up_a_model():
    s = state(rerank_model_id='rerank')
    reg = Registry()
    with pytest.raises(SearchNothing):
        asyncio.run(rerank(s, CTX, reg))
    assert decision(s)['reason'] == 'SEARCH_EMPTY'


@pytest.mark.parametrize('serial', [False, True])
@pytest.mark.parametrize('source_mode', ['off', 'bounded_source_v1'])
def test_all_below_keeps_search_scores_and_raw_rerank_for_merge(serial, source_mode):
    async def run():
        s = state(rerank_model_id='rerank', source_evidence_mode=source_mode)
        s._strategy_serial = serial
        s.search_results = [row('low-search', 'First original', .2), row('high-search', 'Second original', .8)]
        original = deepcopy(s.search_results)
        model = Model([[RerankResult(0, .01), RerankResult(1, .03)]])
        await rerank(s, CTX, Registry(model))
        assert s.rerank_results == [] and s.search_results == original
        assert s.strategy_rerank_scores == {'low-search': .01, 'high-search': .03}
        assert s.strategy_rerank_accepted_ids == set()
        assert decision(s)['reason'] == 'RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'
        raw = next(item for item in s.evidence_trace if item['stage'] == 'rerank_raw')
        assert raw['response'] == [{'index': 0, 'score': .01}, {'index': 1, 'score': .03}]
        await merge(s, CTX, None)
        assert [item.id for item in s.merge_results] == ['high-search', 'low-search']
        assert [item.score for item in s.merge_results] == [.8, .2]
        assert all('model_score' not in item.metadata for item in s.merge_results)
    asyncio.run(run())


def test_high_score_remains_normal_rerank_with_original_search_score():
    s = state(rerank_model_id='rerank')
    s.search_results = [row(score=.8)]
    asyncio.run(rerank(s, CTX, Registry(Model([[RerankResult(0, .9)]]))))
    assert decision(s)['reason'] == 'RERANK_ACCEPTED'
    assert s.rerank_results[0].score == pytest.approx(.88)
    assert s.rerank_results[0].metadata['base_score'] == '0.8000'
    assert s.rerank_results[0].metadata['model_score'] == '0.9000'
    assert s.search_results[0].score == .8


def test_unrelated_low_score_can_continue_but_is_not_marked_rerank_accepted():
    s = state(query='Which dataset trains the classifier?', rerank_model_id='rerank')
    unrelated = row(content='This source only describes cooking soup.', score=.7)
    s.search_results = [unrelated]
    asyncio.run(rerank(s, CTX, Registry(Model([[RerankResult(0, .001)]]))))
    assert s.search_results == [unrelated] and not s.rerank_results
    assert decision(s)['reason'] == 'RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'
    assert not s.strategy_rerank_accepted_ids
    assert s.strategy_rerank_scores == {'a': .001}
    assert unrelated.metadata == {}


@pytest.mark.parametrize('serial', [False, True])
def test_model_error_remains_distinct_from_low_scores(serial):
    s = state(rerank_model_id='rerank')
    s._strategy_serial = serial
    s.search_results = [row()]
    operation = rerank(s, CTX, Registry(Model([RuntimeError('synthetic model failure')])))
    if serial:
        with pytest.raises(SearchError, match='RERANK_FAILED'):
            asyncio.run(operation)
    else:
        asyncio.run(operation)
    assert decision(s)['reason'] == 'RERANK_MODEL_ERROR'
    assert s.search_results and not s.rerank_results
    assert not s.strategy_rerank_scores


def test_model_lookup_failure_retains_its_error_contract():
    s = state(rerank_model_id='rerank')
    s.search_results = [row()]
    with pytest.raises(RuntimeError, match='GET_RERANK_MODEL_FAILED'):
        asyncio.run(rerank(s, CTX, Registry()))
    assert decision(s)['reason'] == 'RERANK_MODEL_ERROR'


@pytest.mark.parametrize('score,accepted', [(.149, False), (.15, True), (.3, True)])
def test_existing_floor_and_threshold_semantics_are_preserved(score, accepted):
    s = state(rerank_model_id='rerank')
    s.search_results = [row()]
    asyncio.run(rerank(s, CTX, Registry(Model([[RerankResult(0, score)]]))))
    assert bool(s.rerank_results) is accepted
    assert s.rerank_threshold == .3


@pytest.mark.parametrize('second_score', [.01, .5])
def test_existing_high_threshold_retry_remains_two_calls(second_score):
    s = state(rerank_model_id='rerank', rerank_threshold=.8)
    s._strategy_serial = True
    s.search_results = [row()]
    model = Model([[RerankResult(0, .01)], [RerankResult(0, second_score)]])
    asyncio.run(rerank(s, CTX, Registry(model)))
    assert len(model.calls) == 2 and s.rerank_threshold == .8
    assert model.calls[0][2].request_id == 'r/rerank'
    assert model.calls[1][2].request_id == 'r/rerank/threshold-2'
    assert bool(s.rerank_results) is (second_score == .5)


@pytest.mark.parametrize('error', [CallCancelled(), ProviderError('MODEL_ORIGIN_KB_EGRESS_DENIED'), ProviderError('LOCAL_EXECUTION_UNKNOWN_STOP', sent=True)])
def test_cancel_scope_and_unknown_are_never_converted_to_search_fallback(error):
    s = state(rerank_model_id='rerank')
    s._strategy_serial = True
    s.search_results = [row()]
    with pytest.raises(type(error)) as caught:
        asyncio.run(rerank(s, CTX, Registry(Model([error]))))
    assert caught.value is error


def test_invalid_provider_scores_still_fail_closed():
    s = state(rerank_model_id='rerank')
    s.search_results = [row()]
    with pytest.raises(InvalidRerankResponse):
        asyncio.run(rerank(s, CTX, Registry(Model([[RerankResult(0, float('nan'))]]))))
