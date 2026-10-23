"""Synthetic persisted image evidence across projection and strategy handoff."""
import asyncio
from copy import deepcopy

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, RetrievalScopeError
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.source_evidence import project_source_evidence
from backend.weknora_port.retrieval.strategy import plan_query
from backend.weknora_port.retrieval.strategy_executor import (
    RoundResult, execute_plan, apply_strategy_result, fit_context_budget, _fingerprint,
)


def image(kind='image_caption'):
    return SearchResult('image', 'Synthetic diagram description.', .9,
        knowledge_id='doc', knowledge_base_id='kb', chunk_type=kind,
        parent_chunk_id='parent', start_at=0, end_at=0,
        metadata={'source_version': 'v1'},
        chunk_metadata={'source_version': 'v1', 'asset_sha256': 'a' * 64},
        image_info='[{"url":"figure.png","caption":"Synthetic diagram description."}]')


@pytest.mark.parametrize('kind', ['image_caption', 'image_ocr'])
def test_projected_derived_evidence_survives_strategy_and_both_context_encoders(kind):
    async def run():
        row = image(kind)
        state = PipelineState(query='Describe the diagram.', rewrite_query='Describe the diagram.',
            targets=(SearchTarget('kb'),), source_evidence_mode='bounded_source_v1',
            retrieval_strategy_mode='query_structure_v1')
        context = CallContext('request', TrustedScope('owner', ('kb',), ('doc',)))
        class Repository:
            async def get_chunks(self, ids, ctx, *, current=False):
                assert current and tuple(ids) == ('image',)
                return {'image': deepcopy(row)}
        async def retrieve(sub, child):
            sub.merge_results = [deepcopy(row)]
            await project_source_evidence(sub, child, Repository())
            return RoundResult(sub)
        result = await execute_plan(state, context, plan_query(state.query, state.query, 'DIRECT'), retrieve)
        apply_strategy_result(state, result)
        await fit_context_budget(state, context)
        selected = state.merge_results[0]
        assert selected.content == row.content and selected.chunk_type == kind
        assert (selected.start_at, selected.end_at) == (0, 0)
        assert selected.parent_chunk_id == row.parent_chunk_id
        assert selected.image_info == row.image_info
        assert selected.chunk_metadata == row.chunk_metadata
        assert state.strategy_coverage['sq-1']['semantic_support'] == 'NOT_CHECKED'
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'is_enabled': False}, {'content_revision': 1}, {'content_rewritten': True},
    {'chunk_metadata': {'source_version': 'stale'}}, {'start_at': -1},
    {'end_at': -1}, {'start_at': True}, {'content': ''}, {'chunk_type': 'summary'},
    {'metadata': {'source_version': 'v1'}},
])
def test_derived_fingerprint_keeps_immutable_source_guards(change):
    row = image()
    row.metadata['source_selection'] = 'MATERIALIZED_SELECTED_SOURCE'
    for key, value in change.items():
        setattr(row, key, value)
    with pytest.raises(RetrievalScopeError):
        _fingerprint(row)


@pytest.mark.parametrize('kind', ['text', 'parent_text', 'faq'])
def test_original_types_still_require_exact_character_locators(kind):
    row = image(kind)
    with pytest.raises(RetrievalScopeError):
        _fingerprint(row)


@pytest.mark.parametrize('field,value', [
    ('parent_chunk_id', 'other-parent'), ('image_info', '[]'),
    ('chunk_metadata', {'source_version': 'v1', 'asset_sha256': 'b' * 64}),
])
def test_same_derived_id_changed_provenance_has_different_fingerprint(field, value):
    row = image()
    row.metadata['source_selection'] = 'MATERIALIZED_SELECTED_SOURCE'
    other = deepcopy(row)
    setattr(other, field, value)
    assert _fingerprint(row) != _fingerprint(other)
    async def run():
        state = PipelineState(query='Compare A and B in terms of latency',
            targets=(SearchTarget('kb'),), source_evidence_mode='bounded_source_v1')
        context = CallContext('request', TrustedScope('owner', ('kb',), ('doc',)))
        calls = []
        async def retrieve(sub, child):
            sub.merge_results = [deepcopy(row if not calls else other)]
            calls.append(child.request_id)
            return RoundResult(sub)
        with pytest.raises(RetrievalScopeError):
            await execute_plan(state, context, plan_query(state.query, state.query, 'SIMPLE_COMPARE'), retrieve)
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'knowledge_base_id': 'outside'}, {'knowledge_id': 'outside'},
    {'metadata': {'source_version': 'stale'}},
])
def test_projection_rejects_derived_row_outside_current_source_domain(change):
    async def run():
        hit = image()
        persisted = deepcopy(hit)
        for key, value in change.items():
            setattr(persisted, key, value)
        state = PipelineState(query='Describe the diagram.', targets=(SearchTarget('kb'),),
            source_evidence_mode='bounded_source_v1', merge_results=[hit])
        context = CallContext('request', TrustedScope('owner', ('kb',), ('doc',)))
        class Repository:
            async def get_chunks(self, ids, ctx, *, current=False):
                assert current
                return {persisted.id: persisted}
        with pytest.raises(RetrievalScopeError):
            await project_source_evidence(state, context, Repository())
    asyncio.run(run())
