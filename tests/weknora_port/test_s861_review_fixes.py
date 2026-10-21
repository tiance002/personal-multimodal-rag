"""Precise S8.6.1 regressions, entirely offline with graph/model seams mocked."""
import asyncio

import pytest

from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchNothing
from backend.weknora_port.retrieval.strategy import plan_query
from test_pipeline import CTX, Model, Registry, Repo, state


def test_off_graph_path_without_admissible_original_raises_native_search_nothing(monkeypatch):
    import backend.weknora_port.graph.retrieval as graph

    graph_calls = []
    class EmptyGraphRepo(Repo):
        tenant_id = 1
        async def get_graph_batches(self, context):
            graph_calls.append(('read', context.scope))
            return ()

    def find_paths(query, batches, context, **kwargs):
        graph_calls.append(('find', query))
        return ('mock-scoped-path-without-retainable-source',)

    async def fuse_paths(current, context, repository, paths):
        graph_calls.append(('fuse', tuple(paths)))
        assert current.merge_results == []
        return 0

    monkeypatch.setattr(graph, 'find_paths', find_paths)
    monkeypatch.setattr(graph, 'fuse_paths', fuse_paths)
    current = state(graph_enabled=True)
    assert current.retrieval_strategy_mode == 'off'
    repository, model = EmptyGraphRepo(), Model([])
    registry = Registry(model)
    with pytest.raises(SearchNothing, match='SEARCH_NOTHING'):
        asyncio.run(RetrievalPipeline(repository, registry).run(current, CTX))
    assert [call[0] for call in graph_calls] == ['read', 'find', 'fuse']
    assert current.merge_results == [] and len(repository.calls) == 1
    assert model.calls == [] and registry.lookups == []
    assert PipelineState().graph_enabled is False


def test_english_comparison_uses_neutral_template_without_changing_source_spans_or_q0():
    question = 'Compare PostgreSQL and Redis in terms of persistence and concurrency.'
    plan = plan_query(question, question, 'SIMPLE_COMPARE')
    assert plan.q0 == plan.effective_query == question
    assert [sub.query_text for sub in plan.subqueries] == [
        'What is known about PostgreSQL regarding persistence and concurrency?',
        'What is known about Redis regarding persistence and concurrency?',
    ]
    assert [question[slice(*sub.object_span)] for sub in plan.subqueries] == ['PostgreSQL', 'Redis']
    assert all(question[slice(*sub.dimension_span)] == 'persistence and concurrency' for sub in plan.subqueries)
    assert [sub.covers for sub in plan.subqueries] == [
        ('PostgreSQL', 'persistence and concurrency'), ('Redis', 'persistence and concurrency')]
