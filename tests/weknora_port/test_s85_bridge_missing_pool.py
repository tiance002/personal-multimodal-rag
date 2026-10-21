"""A missing hop ends this bounded branch; no hidden second Hybrid Search."""
from test_bridge_rescue import case, run


def test_verified_bridge_without_initial_pool_source_stops_before_rerank():
    state, repository, models = case()
    state.search_results = state.search_results[:1]
    state.evidence_trace = [
        {'stage':'rerank_raw', 'input_ids':['first'], 'response':[{'index':0,'score':.95}]},
        {'stage':'rerank_filter_mmr', 'accepted_ids':['first'], 'mmr_ids':['first']}]
    async def forbidden_search(*args, **kwargs):
        raise AssertionError('Stage5 Hybrid Search is outside this task')
    repository.hybrid_search = forbidden_search
    models.embedding = forbidden_search
    run(state, repository, models)
    assert len(models.chat_calls) == 1 and not models.rerank_calls and not state.rescue_evidence
    assert state.bridge_trace[-1]['reason'] == 'SECOND_HOP_SOURCE_NOT_IN_POOL'
    assert any(item.get('decision') == 'FIRST_HOP_VERIFIED' for item in state.bridge_trace)
    assert any(item.get('decision') == 'BRIDGE_QUERY_VERIFIED' for item in state.bridge_trace)
