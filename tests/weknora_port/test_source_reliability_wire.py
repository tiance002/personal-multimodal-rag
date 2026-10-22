"""S8.3 offline source projection through the real adapter, no real network."""
import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from test_source_evidence import CTX, row, result, state, repository
from test_provider_transport import setup, response
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from backend.weknora_port.models import provider_config, ChatOptions
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.source_evidence import project_source_evidence
from backend.weknora_port.retrieval.search import RetrievalScopeError


def pair():
    source = row('original', 'Field Unit: NP-204. Inspection date: 2026-10-13.')
    hint = row('summary', 'Invented equipment type and wrong inspection month.', 1, 'summary',
               parent_chunk_id='original',
               metadata={'origin': 'model_generated_summary', 'upstream_parent_link': True})
    return source, hint


def test_summary_is_a_locator_and_current_original_reaches_actual_adapter_wire():
    async def run():
        source, hint = pair()
        s = state([result(hint),result(source)])
        await project_source_evidence(s, CTX, repository([source, hint]))
        assert [r.id for r in s.merge_results] == ['original']
        final = await into_chat_message(s)
        request = GenerationRequest('r', 'session', 'answer', s.query, final,
            tuple(Evidence.from_result(r) for r in s.merge_results), citations_enabled=True)
        prepared = prepare_generation(request)
        sent = []
        cfg = provider_config('siliconflow', 'Qwen/Qwen3.5-4B')
        def handler(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json=response(cfg))
        model, transport, gate = setup(handler, config=cfg)
        try:
            await model.chat(prepared.messages, ChatOptions(max_completion_tokens=256, thinking=False), CTX)
        finally:
            await transport.aclose()
        wire = '\n'.join(m['content'] for m in sent[0]['messages'])
        assert source['content'] in wire
        assert hint['content'] not in wire
        assert 'evidence_kind="source_text"' in wire
        assert len(gate.calls) == len(gate.finishes) == 1
        assert final.result_ids == ('original',)
        assert dict(prepared.registry.snapshot().chunks)['c1'][0] == 'original'
        assert s.merge_results[0].chunk_metadata['source_version'] == 'v'
        assert (s.merge_results[0].start_at, s.merge_results[0].end_at) == (0, len(source['content']))
        assert any(t.get('reason') == 'EXACT_PERSISTED_QUOTES' for t in s.evidence_trace)
    asyncio.run(run())


@pytest.mark.parametrize('missing', ['missing', 'oversize', 'disabled', 'old_version'])
def test_unavailable_original_does_not_leave_summary_facts_in_generation(missing):
    async def run():
        source, hint = pair()
        if missing == 'oversize': source['content'] = 'x' * 2049
        elif missing == 'disabled': source['is_enabled'] = False
        elif missing == 'old_version': source['metadata']['source_version'] = 'old'
        rows = [hint] if missing == 'missing' else [source, hint]
        s = state([result(hint)])
        try:
            await project_source_evidence(s, CTX, repository(rows))
        except PermissionError:
            return  # A proven invalid parent identity fails closed before generation.
        assert s.merge_results == []
        assert any(t.get('decision') == 'INSUFFICIENT_SOURCE' for t in s.evidence_trace)
        final = await into_chat_message(s)
        request = GenerationRequest('r', 'session', 'answer', s.query, final, ())
        prepared = prepare_generation(request)
        assert hint['content'] not in prepared.full_prompt
        assert prepared.registry.snapshot().chunks == ()
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'start_at': -1}, {'end_at': 999}, {'chunk_metadata': {'source_version': 'old'}},
    {'metadata': {'source_version': 'old'}}, {'is_enabled': False},
    {'knowledge_base_id': 'foreign'}, {'knowledge_id': 'foreign'},
    {'content_rewritten': True}, {'content_revision': 1},
])
def test_expansion_revalidates_injected_repository_rows(change):
    async def run():
        source, hint = pair()
        forged = result(source)
        for key, value in change.items(): setattr(forged, key, value)
        class Repo:
            async def get_chunks(self, *args, **kwargs): return {'original':forged}
        with pytest.raises((RetrievalScopeError, PermissionError, ValueError)):
            await project_source_evidence(state([result(source)]), CTX, Repo())
    asyncio.run(run())


def test_all_selected_originals_survive_without_added_window_budget():
    async def run():
        sources, hints = [], []
        for i in range(3):
            source = row(f'raw{i}', chr(65+i)*1500, knowledge_id=f'doc{i}')
            hint = row(f'hint{i}', 'Generated claim without independent verification', 1, 'summary',
                       knowledge_id=f'doc{i}', parent_chunk_id=source['id'],
                       metadata={'origin':'model_generated_summary', 'upstream_parent_link':True})
            sources.append(source); hints.append(hint)
        s = state([result(h) for h in hints]+[result(r) for r in sources])
        await project_source_evidence(s, CTX, repository(sources+hints))
        assert len(s.merge_results) == 3
        assert all(r.chunk_type == 'text' for r in s.merge_results)
        assert sum(len(r.content) for r in s.merge_results) == 4500
        assert any(t.get('decision') == 'INSUFFICIENT_SOURCE' for t in s.evidence_trace)
    asyncio.run(run())


def test_default_off_preserves_existing_summary_behavior_without_reading_repository():
    async def run():
        _, hint = pair(); s = state([result(hint)]); s.source_evidence_mode = 'off'
        before = deepcopy(s.merge_results)
        await project_source_evidence(s, CTX, None)
        assert s.merge_results == before and s.evidence_trace == []
    asyncio.run(run())


def test_pipeline_reports_search_nothing_for_summary_without_verifiable_original():
    from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
    from backend.weknora_port.retrieval.search import SearchNothing
    from test_pipeline import Repo, Registry
    async def run():
        _, hint = pair()
        selected = result(hint)
        class NoSource(Repo):
            async def get_source_unit(self, *args): return ()
        repo = NoSource(rows=[selected], chunks={selected.id:selected})
        s = state([]); s.enable_rewrite = False; s.enable_expansion = False
        with pytest.raises(SearchNothing):
            await RetrievalPipeline(repo, Registry()).run(s, CTX)
        assert s.merge_results == []
        assert any(t.get('decision') == 'INSUFFICIENT_SOURCE' for t in s.evidence_trace)
        # Default mode uses the unchanged Summary behavior through the same path.
        original = state([]); original.enable_rewrite = False; original.enable_expansion = False
        original.source_evidence_mode = 'off'
        final = await RetrievalPipeline(repo, Registry()).run(original, CTX)
        assert final.result_ids == (selected.id,)
        assert hint['content'] in final.user_content
    asyncio.run(run())


def test_existing_original_avoids_unnecessary_summary_whole_document_load():
    async def run():
        source, hint = pair(); calls = []
        class Repo:
            async def get_chunks(self, ids, context, *, current=False):
                calls.append(tuple(ids)); assert current
                return {'original':result(source)}
        s = state([result(hint), result(source)])
        await project_source_evidence(s, CTX, Repo())
        assert calls == [('original',)]
        assert [r.id for r in s.merge_results] == ['original']
        assert any(t.get('reason') == 'SUMMARY_LOCATOR_ONLY' for t in s.evidence_trace)
    asyncio.run(run())


def test_selected_rewritten_superset_is_rematerialized_as_exact_current_source():
    async def run():
        source, hint = pair()
        hit = result(source); hit.content += ' Unverified appended assertion.'
        hit.end_at = 999; hit.content_rewritten = True
        s = state([hit, result(hint)])
        await project_source_evidence(s, CTX, repository([source, hint]))
        raw = next(r for r in s.merge_results if r.id == source['id'])
        assert raw.content == source['content']
        assert raw.end_at == len(source['content']) and not raw.content_rewritten
        final = await into_chat_message(s)
        req = GenerationRequest('r', 'session', 'answer', s.query, final,
            tuple(Evidence.from_result(r) for r in s.merge_results))
        assert 'Unverified appended assertion' not in prepare_generation(req).full_prompt
    asyncio.run(run())


def test_merged_projection_becomes_separate_source_rows_with_exact_offsets():
    async def run():
        a = row('a', 'First original statement.', 0, next_chunk_id='b')
        b = row('b', 'Second original statement.', 1, pre_chunk_id='a')
        hit = result(a); hit.content += '\n\n' + b['content']; hit.sub_chunk_ids = ['b']
        s = state([hit]); await project_source_evidence(s, CTX, repository([a, b]))
        assert {r.id for r in s.merge_results} == {'a', 'b'}
        for r in s.merge_results:
            expected = a if r.id == 'a' else b
            assert r.content == expected['content']
            assert (r.start_at, r.end_at) == (expected['start_at'], expected['end_at'])
            assert not r.content_rewritten
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'knowledge_base_id':'foreign'}, {'chunk_metadata':{'source_version':'old'}},
    {'end_at':999}, {'id':'another-id'}, {'is_enabled':False},
])
def test_materialized_subchunk_requires_exact_identity_version_and_locator(change):
    async def run():
        source, _ = pair(); extra = row('extra', 'Other original source.', 1)
        hit = result(source); hit.content += ' Composed body'; hit.sub_chunk_ids = ['extra']
        forged = result(extra)
        for key, value in change.items(): setattr(forged, key, value)
        class Repo:
            async def get_source_unit(self, *args): return [result(source)]
            async def get_chunks(self, *args, **kwargs): return {'extra':forged}
        with pytest.raises((PermissionError, ValueError, RetrievalScopeError)):
            await project_source_evidence(state([hit]), CTX, Repo())
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'chunk_metadata':{'source_version':'old'}}, {'is_enabled':False},
])
def test_selected_original_cannot_borrow_current_source_identity(change):
    async def run():
        source, _ = pair(); hit = result(source)
        for key, value in change.items(): setattr(hit, key, value)
        with pytest.raises((PermissionError, RetrievalScopeError)):
            await project_source_evidence(state([hit]), CTX, repository([source]))
    asyncio.run(run())


def test_window_exclusion_cannot_make_unverified_suffix_a_quote():
    async def run():
        originals = [result(row(f'raw{i}', f'Original source {i}', knowledge_id=f'doc{i}')) for i in range(4)]
        source = deepcopy(originals[-1])
        originals[-1].content += ' expanded without exact locator'
        originals[-1].content_rewritten = True
        class Repo:
            async def get_source_unit(self, anchor, context):
                return [r for r in originals[:3] if r.id == anchor.id]
            async def get_chunks(self, ids, context, *, current=False):
                assert current and tuple(ids) == ('raw0','raw1','raw2','raw3')
                return {r.id:r for r in [*originals[:3],source]}
        s = state(originals); await project_source_evidence(s, CTX, Repo())
        assert [r.id for r in s.merge_results] == ['raw0', 'raw1', 'raw2', 'raw3']
        assert s.merge_results[-1].content == source.content
        assert s.merge_results[-1].end_at == source.end_at
        assert not s.merge_results[-1].content_rewritten
        assert s.evidence_trace[-1]['reason'] == 'EXACT_PERSISTED_QUOTES'
    asyncio.run(run())
