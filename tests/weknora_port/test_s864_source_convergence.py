"""Owner S8.6.4: no second source window or arbitrary source-unit budget."""
import asyncio
from copy import deepcopy
import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval import source_evidence
from backend.weknora_port.retrieval.merge import expand_neighbors, resolve_parents, sequential
from backend.weknora_port.retrieval.search import RetrievalScopeError
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.generation.context import Evidence, prepare_generation, escape_text
from backend.weknora_port.generation.types import GenerationRequest

CTX = CallContext('offline', TrustedScope('owner', ('kb',)))


def raw(id, text, **kw):
    return SearchResult(id, text, .8, knowledge_id='doc', knowledge_base_id='kb',
        metadata={'source_version':'v'}, chunk_metadata={'source_version':'v'}, end_at=len(text), **kw)


class Repo:
    def __init__(self, rows):
        self.rows = {r.id: deepcopy(r) for r in rows}; self.windows = 0

    async def get_chunks(self, ids, context, *, current=False):
        return {id:deepcopy(self.rows[id]) for id in ids if id in self.rows}

    async def get_source_unit(self, anchor, context):
        self.windows += 1
        return list(deepcopy(self.rows).values())

    async def get_chunk_children(self, ids, context): return []


async def project(rows, repo):
    state = PipelineState(query='Read the original', targets=(SearchTarget('kb'),),
        source_evidence_mode='bounded_source_v1', merge_results=rows)
    fn = getattr(source_evidence, 'project_source_evidence', None) or source_evidence.expand_source_evidence
    await fn(state, CTX, repo)
    return state


def test_large_selected_source_is_not_removed_by_source_unit_window():
    async def run():
        original = raw('long', 'Original fact. ' * 2000)
        repo = Repo([original]); state = await project([deepcopy(original)], repo)
        assert state.merge_results[0].content == original.content and repo.windows == 0
        finalized = await into_chat_message(state)
        request = GenerationRequest('r','s','m',state.query,finalized,
            evidence=tuple(Evidence.from_result(r) for r in state.merge_results),citations_enabled=True)
        assert escape_text(original.content) in prepare_generation(request).messages[-1].content
    asyncio.run(run())


def test_all_selected_composite_members_survive_without_six_chunk_limit():
    async def run():
        originals = [raw(str(i), f'Original {i} ' * 40, chunk_index=i) for i in range(7)]
        composite = deepcopy(originals[0]); composite.content = '\n'.join(r.content for r in originals)
        composite.end_at = len(composite.content); composite.sub_chunk_ids = [r.id for r in originals[1:]]
        state = await project([composite], Repo(originals))
        assert [r.id for r in state.merge_results] == [r.id for r in originals]
        assert all(r.content == originals[int(r.id)].content for r in state.merge_results)
    asyncio.run(run())


def test_missing_selected_source_does_not_erase_other_verified_sources():
    async def run():
        good, missing = raw('good','Verified source'), raw('missing','No longer available')
        state = await project([good, missing], Repo([good]))
        assert [r.id for r in state.merge_results] == ['good']
        assert any(t.get('anchor') == 'missing' and t.get('decision') == 'INSUFFICIENT_SOURCE'
                   for t in state.evidence_trace)
    asyncio.run(run())


def test_no_neighbor_means_no_rewrite_flag():
    async def run():
        original = raw('short', 'A short unchanged original.')
        row = deepcopy(original)
        state = PipelineState(targets=(SearchTarget('kb'),), source_evidence_mode='bounded_source_v1')
        await expand_neighbors([row], state, CTX, Repo([original]))
        assert row.content == original.content and not row.content_rewritten
    asyncio.run(run())


def test_neighbors_wrap_the_entire_already_selected_body():
    async def run():
        a = raw('a','First selected.',pre_chunk_id='p')
        b = raw('b','Second selected.')
        previous = raw('p','Earlier neighbor.',next_chunk_id='a')
        selected = deepcopy(a); selected.content += '\n\n' + b.content
        selected.sub_chunk_ids = ['b']; selected.content_rewritten = True
        repo = Repo([a,b,previous])
        state = PipelineState(targets=(SearchTarget('kb'),),source_evidence_mode='bounded_source_v1')
        await expand_neighbors([selected],state,CTX,repo)
        assert all(r.content in selected.content for r in (a,b,previous))
        projected = await project([selected],repo)
        assert {r.id for r in projected.merge_results} == {'a','b','p'}
    asyncio.run(run())


def test_sequential_merge_retains_both_sides_recorded_parent_members():
    a = raw('a','abcdef');a.sub_chunk_ids=['left-parent']
    b = raw('b','defghi',chunk_index=1);b.start_at=3;b.end_at=9;b.sub_chunk_ids=['right-parent']
    merged = sequential([a,b],preserve_sources=True)
    assert len(merged)==1 and merged[0].content=='abcdefghi'
    assert merged[0].sub_chunk_ids==['left-parent','b','right-parent']


def test_parent_supplement_keeps_exact_original_with_image_link_and_child_identity():
    async def run():
        parent = raw('parent','Intro\n![unselected](local.png)\nTail',chunk_type='parent_text')
        child = raw('child','Tail',parent_chunk_id='parent')
        repo=Repo([child,parent]);s=PipelineState(targets=(SearchTarget('kb'),),source_evidence_mode='bounded_source_v1')
        selected=(await resolve_parents([deepcopy(child)],s,CTX,repo))[0]
        assert selected.id==child.id and parent.content in selected.content
        projected=await project([selected],repo)
        assert {r.id for r in projected.merge_results}=={'child','parent'}
        assert next(r.content for r in projected.merge_results if r.id=='parent')==parent.content
    asyncio.run(run())


def test_image_grandparent_cannot_cross_document_even_when_kb_is_authorized():
    async def run():
        grandparent=raw('grandparent','Foreign document body',chunk_type='parent_text');grandparent.knowledge_id='foreign'
        parent=raw('parent','Text parent',parent_chunk_id='grandparent')
        child=raw('image','Derived caption',chunk_type='image_caption',parent_chunk_id='parent')
        repo=Repo([grandparent,parent,child]);s=PipelineState(targets=(SearchTarget('kb'),),source_evidence_mode='bounded_source_v1')
        with pytest.raises(RetrievalScopeError):await resolve_parents([child],s,CTX,repo)
    asyncio.run(run())


def test_image_composite_uses_original_image_and_parent_reference_bodies():
    async def run():
        parent=raw('parent','Original text parent')
        image=raw('image','Derived caption',chunk_type='image_caption',parent_chunk_id='parent')
        repo=Repo([parent,image]);s=PipelineState(targets=(SearchTarget('kb'),),source_evidence_mode='bounded_source_v1')
        selected=(await resolve_parents([deepcopy(image)],s,CTX,repo))[0]
        projected=await project([selected],repo)
        assert {r.id:r.content for r in projected.merge_results}=={'image':image.content,'parent':parent.content}
        final=await into_chat_message(projected)
        prepared=prepare_generation(GenerationRequest('r','s','m',projected.query,final,
            evidence=tuple(Evidence.from_result(r) for r in projected.merge_results),citations_enabled=True))
        assert 'evidence_kind="generated_caption"' in prepared.messages[-1].content
    asyncio.run(run())
