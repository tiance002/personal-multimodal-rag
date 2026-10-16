"""Offline final evidence projection; no database, network or model calls."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import Evidence
from backend.weknora_port.models.types import CallContext, TrustedScope, ProviderError
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing, RetrievalScopeError
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.runtime import ApplicationRuntime


CTX = CallContext('source-test', TrustedScope('owner', ('kb', 'other-kb'), ('doc', 'other-doc')))


def row(chunk_id, content='original text', **over):
    values = dict(knowledge_id='doc', knowledge_base_id='kb', start_at=0, end_at=len(content),
                  chunk_metadata={'source_version': 'v1'})
    values.update(over)
    return SearchResult(chunk_id, content, values.pop('score', .8), **values)


def state(rows, top_k=30):
    return PipelineState(query='question', merge_results=rows, rerank_top_k=top_k,
        targets=(SearchTarget('kb'), SearchTarget('other-kb')))


class Repository:
    def __init__(self, rows):
        self.rows = {r.id: r for r in rows}
        self.calls = []

    async def get_chunks(self, ids, context):
        self.calls.append(tuple(ids))
        return {key: self.rows[key] for key in ids if key in self.rows}


def application(rows, overrides=None):
    app = ApplicationRuntime.__new__(ApplicationRuntime)
    app.principal = object()
    sources = {r.id: dict(chunk_id=r.id, document_id=r.knowledge_id,
        knowledge_base_id=r.knowledge_base_id, source_version=r.chunk_metadata.get('source_version'),
        chunk_type=r.chunk_type, start=r.start_at, end=r.end_at, text=r.content) for r in rows}
    for key, value in (overrides or {}).items():
        sources[key].update(value)
    calls = []

    async def read(principal, chunk_id):
        assert principal is app.principal
        calls.append(chunk_id)
        return sources[chunk_id]
    app.documents = SimpleNamespace(read_source_chunk=read)
    return app, sources, calls


def test_summary_is_excluded_without_mapping_to_first_parent():
    summary = row('summary', 'generated claim', chunk_type='summary', parent_chunk_id='first')
    app, _, calls = application([])
    repo = Repository([])
    selected = state([summary])
    with pytest.raises(SearchNothing):
        asyncio.run(app._project_source_evidence(selected, CTX, repo))
    assert selected.merge_results == [] and calls == [] and repo.calls == []


def test_merged_text_expands_to_original_ids_offsets_and_readable_source_text():
    first = row('first', 'original first')
    second = row('second', 'original second', chunk_index=1, start_at=20, end_at=35)
    merged = replace(first, content='merged body must disappear', end_at=99, score=.95,
                     sub_chunk_ids=['second', 'first'])
    app, sources, calls = application([first, second], {'first': {'text': 'edited source first'}})
    repo = Repository([first, second])
    selected = state([merged], top_k=1)

    async def run():
        await app._project_source_evidence(selected, CTX, repo)
        return await into_chat_message(selected, CTX)
    finalized = asyncio.run(run())
    assert finalized.result_ids == ('first', 'second')
    assert repo.calls == [('first', 'second')] and calls == ['first', 'second']
    assert 'merged body must disappear' not in finalized.rendered_contexts
    for result in selected.merge_results:
        source = sources[result.id]
        assert (result.content, result.start_at, result.end_at, result.chunk_metadata['source_version']) == (
            source['text'], source['start'], source['end'], source['source_version'])
        assert result.score == .95 and result.sub_chunk_ids == []
        evidence = Evidence.from_result(result)
        assert evidence.id == result.id and evidence.content == source['text']


@pytest.mark.parametrize('change', [
    {'knowledge_id': 'other-doc'}, {'knowledge_base_id': 'other-kb'},
    {'chunk_metadata': {'source_version': 'v2'}}, {'chunk_metadata': {}},
])
def test_raw_cannot_cross_merged_host_domain_even_when_query_scope_allows_it(change):
    first = row('first')
    wrong = row('second', **change)
    app, _, calls = application([first, wrong])
    selected = state([replace(first, sub_chunk_ids=['second'])])
    with pytest.raises(RetrievalScopeError):
        asyncio.run(app._project_source_evidence(selected, CTX, Repository([first, wrong])))
    assert calls == []


@pytest.mark.parametrize('change', [{'document_id': 'other-doc'}, {'knowledge_base_id': 'other-kb'},
                                   {'source_version': 'v2'}, {'chunk_type': 'summary'}, {'end': 999}])
def test_readable_source_must_match_loaded_raw_identity_and_offsets(change):
    first = row('first')
    app, _, _ = application([first], {'first': change})
    with pytest.raises(RetrievalScopeError):
        asyncio.run(app._project_source_evidence(state([first]), CTX, Repository([first])))


@pytest.mark.parametrize('missing', ['raw', 'version'])
def test_missing_original_or_version_never_falls_back_to_merged_text(missing):
    first = row('first', chunk_metadata={} if missing == 'version' else {'source_version': 'v1'})
    app, _, _ = application([first])
    with pytest.raises(ProviderError, match='SOURCE_EVIDENCE_UNAVAILABLE'):
        asyncio.run(app._project_source_evidence(state([first]), CTX,
            Repository([] if missing == 'raw' else [first])))


def test_production_hook_removes_summary_before_top_k_and_finalizes_projected_state(monkeypatch):
    import backend.weknora_port.retrieval.pipeline as module
    first = row('first', 'readable original')
    summary = row('summary', 'generated claim', chunk_type='summary', score=1.0)
    app, _, _ = application([first])
    repo = Repository([first])
    selected = state([], top_k=1)
    selected.enable_rewrite = selected.enable_expansion = False

    async def noop(*args, **kwargs):
        pass
    async def merged(state, *args):
        state.merge_results = [summary, first]
    for name in ('understand_query', 'search', 'rerank'):
        monkeypatch.setattr(module, name, noop)
    monkeypatch.setattr(module, 'merge', merged)
    async def project(state, context):
        await app._project_source_evidence(state, context, repo)
    pipeline = RetrievalPipeline(repo, object(), final_evidence_projection=project)
    finalized = asyncio.run(pipeline.run(selected, CTX))
    assert finalized.result_ids == ('first',)
    assert 'readable original' in finalized.rendered_contexts
    assert 'generated claim' not in finalized.rendered_contexts
