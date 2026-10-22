"""Bounded source units: provenance, intact structures, and isolated rollout."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, RetrievalScopeError
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.source_evidence import project_source_evidence, MODE
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest

CTX = CallContext('r', TrustedScope('owner', ('kb',)))


def row(id, text, index=0, kind='text', **kw):
    args = dict(id=id, tenant_id=1, knowledge_id='doc', knowledge_base_id='kb', content=text,
        chunk_index=index, chunk_type=kind, start_at=index*100, end_at=index*100+len(text),
        is_enabled=True, metadata={'source_version':'v'}, knowledge_metadata={'source_version':'v'},
        knowledge_enable_status='enabled', knowledge_parse_status='completed',
        parent_chunk_id='', pre_chunk_id='', next_chunk_id='', content_revision=0)
    args.update(kw)
    return args


class Connection:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def execute(self, sql, params):
        self.calls.append((sql, params))
        if sql.startswith('SELECT id FROM chunks'):
            tenant, kb, doc, version, first, last, limit = params
            values = [r for r in self.rows if r['tenant_id']==tenant and r['knowledge_base_id']==kb
                and r['knowledge_id']==doc and r['chunk_type']=='text'
                and r['metadata'].get('source_version')==version and first<=r['chunk_index']<=last]
            values=sorted(values,key=lambda r:(r['chunk_index'],r['id']))[:limit]
        else:
            # Bindings: three tenant values, then IDs, then KB scope.
            ids=params[3:-1]
            values=[r for r in self.rows if r['id'] in ids]
        return SimpleNamespace(fetchall=_async_value(values))


def _async_value(value):
    async def invoke(): return deepcopy(value)
    return invoke


def repository(rows):
    conn=Connection(rows)
    retriever=SimpleNamespace(connection=conn, require_verified=lambda:None)
    async def tenant(owner):
        assert owner=='owner'
        return 1
    return ScopedKnowledgeRepository(retriever, tenant)


def result(raw, score=.9):
    return SearchResult(raw['id'],raw['content'],score,knowledge_id=raw['knowledge_id'],
        knowledge_base_id=raw['knowledge_base_id'], chunk_type=raw['chunk_type'],
        chunk_index=raw['chunk_index'], metadata=raw['knowledge_metadata'].copy(),
        chunk_metadata=raw['metadata'].copy(),parent_chunk_id=raw['parent_chunk_id'],
        pre_chunk_id=raw['pre_chunk_id'], next_chunk_id=raw['next_chunk_id'],
        start_at=raw['start_at'],end_at=raw['end_at'])


def state(results, **kw):
    return PipelineState(query='Read the source facts', targets=(SearchTarget('kb'),),
        merge_results=results, source_evidence_mode=MODE, **kw)


def summary_case():
    source=row('raw',r'Coefficients: \begin{bmatrix} 4 & -3 \\ 0 & 2 \end{bmatrix}')
    hint=row('hint','A summary of matrix notation',1,'summary',parent_chunk_id='raw',
        metadata={'origin':'model_generated_summary','upstream_parent_link':True})
    return source,hint


@pytest.mark.parametrize('field,value',[
    ('knowledge_enable_status','disabled'), ('knowledge_parse_status','processing'),
    ('knowledge_parse_status','failed')])
def test_unavailable_document_excludes_only_its_selected_source(field, value):
    async def run():
        originals = [row(f'source-{i}', f'Original body {i}', knowledge_id=f'doc-{i}') for i in range(4)]
        selected = [result(r) for r in originals]
        originals[-1][field] = value
        s = state(selected); await project_source_evidence(s, CTX, repository(originals))
        assert [r.id for r in s.merge_results] == [f'source-{i}' for i in range(3)]
        assert any(t.get('omitted_ids') == ['source-3'] for t in s.evidence_trace)
    asyncio.run(run())


@pytest.mark.parametrize('metadata', [{},{'origin':'model_generated_summary'},
    {'origin':'model_generated_summary','upstream_parent_link':True}])
def test_summary_never_triggers_extra_source_window(metadata):
    async def run():
        source,hint=summary_case();hint['metadata']=metadata
        repo=repository([source,hint]);s=state([result(hint)])
        await project_source_evidence(s,CTX,repo)
        assert s.merge_results == [] and repo.retriever.connection.calls == []
        assert s.evidence_trace[-1]['reason']=='SUMMARY_LOCATOR_ONLY'
    asyncio.run(run())


@pytest.mark.parametrize('change',[
    {'tenant_id':2},{'knowledge_base_id':'other'},{'knowledge_id':'other'}])
def test_invalid_authorized_readback_fails_closed(change):
    async def run():
        source=row('raw','Actual source');selected=result(source);source.update(change)
        with pytest.raises((PermissionError, RetrievalScopeError)):
            await project_source_evidence(state([selected]),CTX,repository([source]))
    asyncio.run(run())


@pytest.mark.parametrize('start,end',[(100,99),(-1,3),(0,999)])
def test_invalid_offsets_never_become_source_proof(start,end):
    async def run():
        source=row('raw','Actual source');selected=result(source);source.update(start_at=start,end_at=end)
        with pytest.raises(PermissionError,match='LOCATOR'):
            await project_source_evidence(state([selected]),CTX,repository([source]))
    asyncio.run(run())


def test_identity_projection_reads_only_selected_and_recorded_members():
    async def run():
        a=row('a','Earlier source',0,next_chunk_id='b')
        b=row('b','Selected source',1,pre_chunk_id='a',next_chunk_id='c')
        c=row('c','Later source',2,pre_chunk_id='b')
        repo=repository([a,b,c]);s=state([result(b)])
        await project_source_evidence(s,CTX,repo)
        assert [r.id for r in s.merge_results]==['b']
        assert len(repo.retriever.connection.calls)==1
        assert 'SELECT id FROM chunks' not in repo.retriever.connection.calls[0][0]
    asyncio.run(run())


def test_member_identifier_without_its_quote_is_not_delivery_proof():
    async def run():
        a=row('a','Earlier source',0);b=row('b','Selected source',1)
        selected=result(b);selected.sub_chunk_ids=['a'];s=state([selected])
        await project_source_evidence(s,CTX,repository([a,b]))
        assert [r.id for r in s.merge_results]==['b']
        assert s.evidence_trace[-1]['omitted_ids']==['a']
    asyncio.run(run())


def test_unverifiable_projection_does_not_discard_other_original():
    async def run():
        a=row('a','Original first');b=row('b','Original second')
        selected=result(a);selected.content='Lost body'
        s=state([selected,result(b)]);await project_source_evidence(s,CTX,repository([a,b]))
        assert [r.id for r in s.merge_results]==['b']
    asyncio.run(run())


def test_off_noop_and_invalid_mode_rejected():
    async def run():
        source,hint=summary_case();s=state([result(hint)]);s.source_evidence_mode='off'
        await project_source_evidence(s,CTX,None);assert len(s.merge_results)==1
        s.source_evidence_mode='guessed'
        with pytest.raises(ValueError,match='MODE'):await project_source_evidence(s,CTX,None)
    asyncio.run(run())


def test_isolated_saved_setting_and_default_are_separate():
    from backend.weknora_port.runtime import ConversationSettings
    from backend.weknora_port.conversation_configuration import resolve_saved_conversation
    cfg=ConversationSettings()
    assert resolve_saved_conversation(cfg,{})['source_evidence_mode']=='off'
    assert resolve_saved_conversation(cfg,{'context_config':{'conversation':{'source_evidence_mode':MODE}}})['source_evidence_mode']==MODE
    assert cfg.source_evidence_mode=='off'


@pytest.mark.parametrize('kind,expected', [('image_caption','generated_caption'),
    ('table_summary','generated_summary'),('image_ocr','derived_ocr'),('unknown','source_data')])
def test_generated_and_derived_content_is_not_labelled_original_text(kind,expected):
    async def run():
        # Production chunk IDs are UUIDs; a fake ID literally "generated"
        # would be compacted inside the new classification attribute itself.
        r=result(row('9cc2db65-2251-4d20-b4e7-516b1cdc6552','Source-derived content',kind=kind))
        s=state([r]);final=await into_chat_message(s)
        request=GenerationRequest('r','s','m',s.query,final,(Evidence.from_result(r),))
        actual=prepare_generation(request).messages[-1].content
        assert f'evidence_kind="{expected}"' in actual
        assert 'evidence_kind="source_text"' not in actual
    asyncio.run(run())


def test_oversize_neighbor_cannot_push_selected_formula_out_of_legacy_window():
    from backend.weknora_port.retrieval.merge import expand_neighbors
    async def run():
        body=r'\begin{bmatrix} 6 & -7 \\ 3 & 1 \end{bmatrix}'
        source=row('selected',body,1,pre_chunk_id='long')
        long=row('long','Unrelated preceding paragraph '*40,0,next_chunk_id='selected')
        hit=result(source)
        await expand_neighbors([hit],state([hit]),CTX,repository([source,long]))
        assert hit.content==body
        assert 'long' not in hit.sub_chunk_ids
    asyncio.run(run())
