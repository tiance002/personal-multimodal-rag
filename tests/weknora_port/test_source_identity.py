"""S8.6.3 offline regression: real ECS40 composites must not borrow anchor IDs."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, RetrievalScopeError
from backend.weknora_port.retrieval.merge import sequential, expand_neighbors
from backend.weknora_port.retrieval.source_evidence import project_source_evidence, MODE
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.generation.context import Evidence, prepare_generation, escape_text
from backend.weknora_port.generation.types import GenerationRequest
from backend.weknora_port.generation.coordinator import GenerationCoordinator

FROZEN = json.loads((Path(__file__).parents[1] / 'fixtures/s8_6_3/ecs40_citation_identity.json').read_text(encoding='utf8'))['cases']


class Readback:
    def __init__(self, rows, windows=()):
        self.rows = {r.id: deepcopy(r) for r in rows}
        self.windows = windows
        self.reads = []

    async def get_chunks(self, ids, context, *, current=False):
        self.reads.append(tuple(ids))
        return {id: deepcopy(self.rows[id]) for id in ids if id in self.rows}

    async def get_source_unit(self, anchor, context):
        return [deepcopy(self.rows[id]) for id in self.windows if id in self.rows]


def setup_case(case):
    originals = [SearchResult(**r) for r in case['reranked_originals']]
    hit = SearchResult(**case['selected'])
    ctx = CallContext('offline', TrustedScope(case['scope']['owner_id'], (hit.knowledge_base_id,)))
    state = PipelineState(query=case['q0'], targets=(SearchTarget(hit.knowledge_base_id),),
        merge_results=[hit], source_evidence_mode=MODE)
    repo = Readback(originals)
    for row in repo.rows.values(): row.score = 0
    return originals, hit, ctx, state, repo


@pytest.mark.parametrize('case', FROZEN, ids=lambda c: c['anchor_id'])
def test_frozen_extend_replays_to_exact_independent_sources(case, tmp_path, monkeypatch):
    async def run():
        originals, hit, ctx, state, repo = setup_case(case)
        merged = sequential(deepcopy(originals), preserve_sources=True)
        assert len(merged) == 1
        assert (merged[0].id, merged[0].content, merged[0].start_at, merged[0].end_at) == (
            hit.id, hit.content, hit.start_at, hit.end_at)
        assert hit.end_at - hit.start_at == len(hit.content)
        assert hit.content != case['source_http']['text']
        assert originals[0].content == case['source_http']['text']
        await project_source_evidence(state, ctx, repo)
        assert {r.id for r in state.merge_results} == {r.id for r in originals}
        assert len(repo.reads) == 1
        for row in state.merge_results:
            source = repo.rows[row.id]
            assert (row.content, row.start_at, row.end_at) == (source.content, source.start_at, source.end_at)
            assert not row.sub_chunk_ids
        assert next(r for r in state.merge_results if r.id == hit.id).score == hit.score
        assert all(r.score == 0 for r in state.merge_results if r.id != hit.id)

        finalized = await into_chat_message(state)
        req = GenerationRequest('offline', 'session', 'message', state.query, finalized,
            tuple(Evidence.from_result(r) for r in state.merge_results), citations_enabled=True)
        prepared = prepare_generation(req)
        wire = '\n'.join(m.content for m in prepared.messages)
        for row in originals: assert escape_text(row.content) in wire
        # Exercise the real Registry and reference serialization used by SSE/PG.
        for handle, _ in prepared.registry.snapshot().chunks:
            prepared.registry.expand_text('<ref id="' + handle + '"/>')
        refs = GenerationCoordinator._references(req, prepared.registry)
        assert len(refs) == len(originals)
        persisted = json.loads(json.dumps(refs))
        for ref in persisted:
            raw = repo.rows[ref['id']]
            assert (ref['content'], ref['start_at'], ref['end_at']) == (raw.content, raw.start_at, raw.end_at)
        from dataclasses import replace
        from test_generation import setup, event
        from test_history_storage import HistoryConnection, adapter
        from test_documents import make_service, PRINCIPAL
        from backend.weknora_port.generation.router import RouterPolicy
        from backend.weknora_port.models.types import TokenUsage
        mock_answer = ' '.join('<ref id="' + h + '"/>' for h, _ in prepared.registry.snapshot().chunks)
        co, cheap, expensive, store, commit, _ = setup([
            event(mock_answer), event(done=True, finish='stop', final=True, usage=TokenUsage(10, 2))])
        answer = await co.run(replace(req, policy=RouterPolicy(mode='CHEAP_ONLY')), ctx)
        assert not expensive.calls and len(cheap.calls) == 1
        assert tuple(cheap.calls[0][0]) == prepared.messages
        assert list(answer.references) == persisted
        assert next(e.data['references'] for e in store.events if e.type == 'references') == persisted
        assert store.events[-1].type == 'complete'
        sql = HistoryConnection({'is_completed': False})
        await adapter(sql).commit_final(commit.calls[0])
        saved = next(args for query, args in sql.calls if query.startswith('UPDATE messages'))
        assert json.loads(saved[2]) == persisted
        # Actual source reader with scripted PG rows; no database or Provider I/O.
        service, _ = make_service(tmp_path)
        async def read_rows(query, params):
            id, tenant, document_tenant = params
            assert tenant == document_tenant == PRINCIPAL.tenant_id
            row = repo.rows[id]
            return [dict(chunk_id=id, document_id=row.knowledge_id,
                knowledge_base_id=row.knowledge_base_id, content=row.content, source_content='',
                start_at=row.start_at, end_at=row.end_at, chunk_type=row.chunk_type,
                chunk_index=row.chunk_index, parse_status='completed',
                chunk_metadata=row.chunk_metadata, document_metadata=row.metadata, filename='frozen')]
        monkeypatch.setattr(service, '_rows', read_rows)
        for ref in persisted:
            source = await service.read_source_chunk(PRINCIPAL, ref['id'])
            assert (ref['content'], ref['start_at'], ref['end_at']) == (source['text'], source['start'], source['end'])
    asyncio.run(run())


def test_selected_composite_outside_optional_groups_is_still_checked():
    async def run():
        originals, hit, ctx, state, repo = setup_case(FROZEN[0])
        front = []
        for i in range(3):
            row = deepcopy(originals[0]); row.id = f'front-{i}'; row.knowledge_id = f'doc-{i}'
            front.append(row); repo.rows[row.id] = deepcopy(row)
        state.merge_results = front + [hit]
        await project_source_evidence(state, ctx, repo)
        assert {r.id for r in originals} <= {r.id for r in state.merge_results}
        assert len(repo.reads) == 1
    asyncio.run(run())


def test_frozen_pieter_original_survives_no_neighbor_rewrite_outside_optional_groups():
    async def run():
        frozen = json.loads((Path(__file__).parents[1] /
            'fixtures/s8_6_3/pieter_tribometer.json').read_text(encoding='utf8'))
        original = SearchResult(**frozen['source'])
        assert len(original.content) == 234 and not original.pre_chunk_id and not original.next_chunk_id
        assert frozen['historical_trace'][0]['reason'] == 'UNMATERIALIZED_SELECTED_PROJECTION'
        ctx = CallContext('offline', TrustedScope('owner', (original.knowledge_base_id,)))
        state = PipelineState(query=frozen['q0'], targets=(SearchTarget(original.knowledge_base_id),),
            source_evidence_mode=MODE)
        repo = Readback([original])
        selected = deepcopy(original)
        await expand_neighbors([selected], state, ctx, repo)
        assert selected.content == original.content and not selected.content_rewritten
        assert not selected.sub_chunk_ids
        front = []
        for i in range(3):
            row = deepcopy(original); row.id = f'front-{i}'; row.knowledge_id = f'doc-{i}'
            front.append(row); repo.rows[row.id] = deepcopy(row)
        state.merge_results = front + [selected]
        await project_source_evidence(state, ctx, repo)
        restored = next(r for r in state.merge_results if r.id == original.id)
        assert (restored.content, restored.start_at, restored.end_at) == (
            original.content, original.start_at, original.end_at)
        assert not restored.content_rewritten
        final = await into_chat_message(state)
        req = GenerationRequest('offline', 's', 'm', frozen['q0'], final,
            tuple(Evidence.from_result(r) for r in state.merge_results), citations_enabled=True)
        assert escape_text(original.content) in prepare_generation(req).messages[-1].content
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('knowledge_base_id','other-kb'),
    ('knowledge_id','other-doc'), ('version','old'), ('is_enabled',False)])
def test_materialized_ancestry_cannot_cross_source_domain(field, value):
    async def run():
        originals, hit, ctx, state, repo = setup_case(FROZEN[0])
        row = repo.rows[originals[1].id]
        if field == 'version': row.metadata['source_version'] = row.chunk_metadata['source_version'] = value
        else: setattr(row, field, value)
        with pytest.raises(RetrievalScopeError): await project_source_evidence(state, ctx, repo)
    asyncio.run(run())


def test_single_source_is_exact_after_one_authoritative_readback():
    async def run():
        originals, _, ctx, state, repo = setup_case(FROZEN[0])
        state.merge_results = [deepcopy(originals[0])]; repo.windows = [originals[0].id]
        await project_source_evidence(state, ctx, repo)
        assert len(state.merge_results)==1
        restored=state.merge_results[0]
        assert (restored.id,restored.content,restored.start_at,restored.end_at,restored.score)==(originals[0].id,originals[0].content,originals[0].start_at,originals[0].end_at,originals[0].score)
        assert repo.reads == [(originals[0].id,)]
    asyncio.run(run())


def test_missing_version_cannot_pass_by_matching_two_missing_values():
    async def run():
        originals, _, ctx, state, repo = setup_case(FROZEN[0])
        hit = deepcopy(originals[0]); hit.metadata = {}; hit.chunk_metadata = {}
        repo.rows[hit.id] = deepcopy(hit); state.merge_results = [hit]
        with pytest.raises(RetrievalScopeError):
            await project_source_evidence(state, ctx, repo)
    asyncio.run(run())


def test_missing_recorded_members_reports_omission_without_losing_known_sources():
    async def run():
        originals, hit, ctx, state, repo = setup_case(FROZEN[0])
        hit.sub_chunk_ids = [f'extra-{i}' for i in range(6)]
        other = deepcopy(originals[0]); other.id = 'safe'; repo.rows[other.id] = deepcopy(other)
        state.merge_results.append(other)
        await project_source_evidence(state, ctx, repo)
        assert [r.id for r in state.merge_results] == [hit.id,'safe']
        assert any(t.get('omitted_ids') == hit.sub_chunk_ids for t in state.evidence_trace)
    asyncio.run(run())


def test_off_keeps_legacy_composite_without_any_readback():
    async def run():
        _, hit, ctx, state, repo = setup_case(FROZEN[0]); state.source_evidence_mode = 'off'
        await project_source_evidence(state, ctx, repo)
        assert state.merge_results == [hit] and repo.reads == []
    asyncio.run(run())


@pytest.mark.parametrize('case', json.loads((Path(__file__).parents[1] /
    'fixtures/s8_6_3/ecs40_complete_context.json').read_text(encoding='utf8')),
    ids=lambda c: c['case_id'])
def test_complete_frozen_context_preserves_all_selected_originals_within_existing_budget(case):
    async def run():
        selected = [SearchResult(**r) for r in case['selected']]
        repo = Readback([SearchResult(**r) for r in case['sources']])
        kb = selected[0].knowledge_base_id
        ctx = CallContext('offline', TrustedScope('owner', (kb,)))
        state = PipelineState(query=case['q0'], targets=(SearchTarget(kb),),
            merge_results=selected, source_evidence_mode=MODE)
        required = {id for r in selected for id in (r.id, *r.sub_chunk_ids)}
        await project_source_evidence(state, ctx, repo)
        assert {r.id for r in state.merge_results} == required
        final = await into_chat_message(state)
        req = GenerationRequest('offline', 's', 'm', case['q0'], final,
            tuple(Evidence.from_result(r) for r in state.merge_results), citations_enabled=True)
        prepared = prepare_generation(req)
        for r in state.merge_results:
            assert r.content == repo.rows[r.id].content
            assert escape_text(r.content) in prepared.messages[-1].content
        assert len(repo.reads) == 1
    asyncio.run(run())
