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
from backend.weknora_port.retrieval.source_evidence import expand_source_evidence, MODE, CONTEXT_CHARS
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


def test_summary_recovers_intact_real_body_with_independent_identity_and_score():
    async def run():
        source,hint=summary_case(); s=state([result(hint)])
        await expand_source_evidence(s,CTX,repository([source,hint]))
        assert [r.id for r in s.merge_results]==['raw']
        recovered=s.merge_results[0]
        assert recovered.content==source['content'] and recovered.score==0
        assert recovered.start_at==0 and recovered.end_at==len(source['content'])
        assert recovered.metadata['source_anchor_score']==.9
        final=await into_chat_message(s)
        req=GenerationRequest('r','s','m',s.query,final,tuple(Evidence.from_result(r) for r in s.merge_results))
        actual=prepare_generation(req)
        assert 'evidence_kind="source_text"' in actual.messages[-1].content
        assert 'evidence_kind="generated_summary"' not in actual.messages[-1].content
        assert '\\begin{bmatrix}' in actual.messages[-1].content
        assert '&amp; -3' in actual.messages[-1].content
    asyncio.run(run())


def test_preceding_window_keeps_real_ids_and_reciprocal_locators_not_guessed_titles():
    async def run():
        a=row('a','Report period: 2011',0,next_chunk_id='b')
        b=row('b','Annual activities',1,pre_chunk_id='a',next_chunk_id='c')
        c=row('c','The training programme served 800 people.',2,pre_chunk_id='b')
        s=state([result(c)]); await expand_source_evidence(s,CTX,repository([a,b,c]))
        assert {r.id for r in s.merge_results}=={'a','b','c'}
        assert all(r.content==next(x['content'] for x in [a,b,c] if x['id']==r.id) for r in s.merge_results)
        assert s.evidence_trace[-1]['reason']=='PRECEDING_SOURCE_WINDOW'
    asyncio.run(run())


@pytest.mark.parametrize('change',[
    {'tenant_id':2},{'knowledge_base_id':'other'},{'knowledge_id':'other'},
    {'metadata':{'source_version':'old'}},{'is_enabled':False},{'deleted_at':'2026-01-01'},
    {'knowledge_parse_status':'processing'},{'knowledge_enable_status':'disabled'}])
def test_invalid_current_source_does_not_enter_context(change):
    async def run():
        source,hint=summary_case(); source.update(change)
        s=state([result(hint)])
        try: await expand_source_evidence(s,CTX,repository([source,hint]))
        except PermissionError: pass
        assert [r.id for r in s.merge_results]==['hint']
    asyncio.run(run())


@pytest.mark.parametrize('metadata', [{},{'origin':'model_generated_summary'},
    {'origin':'literal','upstream_parent_link':True},
    {'origin':'model_generated_summary','upstream_parent_link':True,'source_version':'old'}])
def test_forged_or_missing_summary_link_is_not_a_source_unit(metadata):
    async def run():
        source,hint=summary_case(); hint['metadata']=metadata
        s=state([result(hint)]); await expand_source_evidence(s,CTX,repository([source,hint]))
        assert len(s.merge_results)==0
    asyncio.run(run())


def test_long_document_is_not_truncated_into_a_fake_complete_short_source():
    async def run():
        source,hint=summary_case(); source['content']='x'*2049
        s=state([result(hint)]); await expand_source_evidence(s,CTX,repository([source,hint]))
        assert [r.id for r in s.merge_results]==[]
    asyncio.run(run())


def test_disabled_source_member_prevents_fake_complete_short_document():
    async def run():
        source,hint=summary_case(); disabled=row('disabled','Unreleased source',1,is_enabled=False)
        s=state([result(hint)]);await expand_source_evidence(s,CTX,repository([source,hint,disabled]))
        assert [r.id for r in s.merge_results]==[]
    asyncio.run(run())


@pytest.mark.parametrize('start,end', [(100,99),(-1,3),(0,999)])
def test_invalid_offsets_are_not_promoted_to_valid_source_proof(start,end):
    async def run():
        source,hint=summary_case();source.update(start_at=start,end_at=end)
        with pytest.raises(PermissionError,match='LOCATOR'):
            await expand_source_evidence(state([result(hint)]),CTX,repository([source,hint]))
    asyncio.run(run())


def test_unrelated_later_neighbor_is_not_loaded_and_duplicate_anchors_do_not_duplicate_source():
    async def run():
        source,hint=summary_case()
        s=state([result(source), result(hint)])
        await expand_source_evidence(s,CTX,repository([source,hint,row('later','Unrelated next chapter',8)]))
        assert [r.id for r in s.merge_results]==['raw']
    asyncio.run(run())


def test_broken_locator_fails_closed():
    async def run():
        a=row('a','Prior',0,next_chunk_id='wrong')
        b=row('b','Selected',1,pre_chunk_id='a')
        s=state([result(b)])
        with pytest.raises(PermissionError,match='LOCATOR'):
            await expand_source_evidence(s,CTX,repository([a,b]))
    asyncio.run(run())


@pytest.mark.parametrize('order', [('a','c'),('c','a'),('hint','a','c')])
def test_empty_or_already_present_anchor_does_not_suppress_later_source_window(order):
    async def run():
        rows=[row('a','Source introduction',0,next_chunk_id='b'),
              row('b','Source period and original intermediate text',1,pre_chunk_id='a',next_chunk_id='c'),
              row('c','Source fact',2,pre_chunk_id='b'),
              row('oversize','x'*2049,3),
              row('hint','Whole-document summary',4,'summary',parent_chunk_id='a',
                  metadata={'origin':'model_generated_summary','upstream_parent_link':True})]
        by_id={r['id']:r for r in rows}
        s=state([result(by_id[id]) for id in order])
        await expand_source_evidence(s,CTX,repository(rows))
        assert {r.id for r in s.merge_results}>={'a','b','c'}
        assert len([r for r in s.merge_results if r.id=='b'])==1
    asyncio.run(run())


def test_subchunk_identifier_without_full_quote_is_not_a_coverage_proof():
    async def run():
        a=row('a','The original source period is 2011.',0,next_chunk_id='b')
        b=row('b','Intermediate source',1,pre_chunk_id='a',next_chunk_id='c')
        c=row('c','The source count is 800.',2,pre_chunk_id='b')
        selected=result(c);selected.sub_chunk_ids=['a']
        s=state([selected]);await expand_source_evidence(s,CTX,repository([a,b,c]))
        assert any(r.id=='a' and r.content==a['content'] for r in s.merge_results)
    asyncio.run(run())


def test_same_identifier_with_lost_body_is_not_reported_as_complete():
    async def run():
        source,hint=summary_case();hit=result(source);hit.content='Only a partial old projection'
        with pytest.raises(ValueError,match='SOURCE_BODY_NOT_PRESERVED'):
            await expand_source_evidence(state([hit,result(hint)]),CTX,repository([source,hint]))
    asyncio.run(run())


def test_context_budget_refuses_intact_oversize_source_instead_of_slicing_formula():
    async def run():
        s=state([result(row('huge', r'\begin{bmatrix}'+'x'*CONTEXT_CHARS+r'\end{bmatrix}'))])
        with pytest.raises(ValueError,match='BUDGET'):
            await into_chat_message(s)
    asyncio.run(run())


def test_off_is_a_true_noop_and_unknown_mode_is_rejected():
    async def run():
        source,hint=summary_case(); s=state([result(hint)])
        s.source_evidence_mode='off'
        await expand_source_evidence(s,CTX,None)
        assert len(s.merge_results)==1
        s.source_evidence_mode='guessed'
        with pytest.raises(ValueError,match='MODE'):
            await expand_source_evidence(s,CTX,None)
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
