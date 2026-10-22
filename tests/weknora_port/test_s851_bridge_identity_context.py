"""S8.5.1: selected original identity anchors become bounded citable Context.

Frost input is frozen S8.4 data. Extractor fields and BGE score below equal
settled B3 receipts; this offline replay makes no new provider calls.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.s8_4_bridge_ecs import build_state
from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult
from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.types import SearchResult, context_results
from backend.weknora_port.retrieval.search import RetrievalScopeError
from backend.weknora_port.retrieval.types import ContextBudgetError
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from test_bridge_rescue import Models, Repository

INPUT = Path(__file__).parent/'fixtures/s8_4_bridge/inputs.json'
INPUT_SHA = 'e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135'
ANCHOR = 'abc3a6aa-5453-4f0a-b7b6-5270cc96067f'
FIRST = 'ec07a1fd-8a8e-4f23-a7d6-65d385d2343b'
LOCATION = '31417c7c-7abf-4723-956a-74bf7ee14683'
# GB3 final SHA: 57fa2b184e471b30e544f99e4ccfea0389ce70e4a1d69c45cb40a58a4e368acc
RECORDED = {'status':'FIRST_HOP_CANDIDATE','sentence_id':'s-c9a81754f7673886cbb035bf',
    'subject':'She','subject_resolution':'UNVERIFIED','predicate':'served as the Director of',
    'object':'The Frost Place Advanced Seminar','time':'2007–2010','target_attribute':'location',
    'bridge_search_phrases':['The Frost Place Advanced Seminar']}


def scenario(*, limit=16000, mutate=None, mode='cheap_bridge_v1'):
    raw = INPUT.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==INPUT_SHA
    case = next(row for row in json.loads(raw)['cases'] if row['case_id']=='frost-B')
    case['document_ids'] = sorted({row['knowledge_id'] for row in case['source_rows']})
    state = build_state(case, {'cheap':{'model_id':'cheap'},'rerank':{'model_id':'rerank'}})
    state.bridge_rescue_mode = mode
    state.bridge_context_char_limit = limit
    context = CallContext('offline-B3',TrustedScope('owner',tuple(case['knowledge_base_ids']),tuple(case['document_ids'])))
    repository = Repository([SearchResult(**row) for row in case['source_rows']])
    class RecordedModels(Models):
        async def _rerank(self,*args):
            response = await super()._rerank(*args)
            if mutate: mutate(repository.rows[ANCHOR])
            return response
    models = RecordedModels(json.dumps(RECORDED,ensure_ascii=False),scores=[RerankResult(0,.989133894443512)])
    return state,context,repository,models


async def render(state,context):
    finalized = await into_chat_message(state,context)
    evidence = tuple(Evidence.from_result(row) for row in context_results(state))
    prepared = prepare_generation(GenerationRequest('offline','session','answer',state.query,finalized,
        evidence=evidence,language='English',citations_enabled=True))
    return finalized,prepared


def test_frozen_B_actual_generation_visible_original_fullname_is_citable():
    async def run():
        state,context,repository,models = scenario()
        before = deepcopy((state.search_results,state.rerank_results,state.merge_results))
        await rescue_bridge(state,context,repository,models)
        assert before==(state.search_results,state.rerank_results,state.merge_results)
        finalized,prepared = await render(state,context)
        assert ANCHOR in finalized.result_ids
        wire = prepared.messages[-1].content.split('</retrieval>')[0]
        assert repository.rows[ANCHOR].content.strip() in wire
        chunks = dict(prepared.registry.snapshot().chunks)
        handle = next(key for key,value in chunks.items() if value[0]==ANCHOR)
        assert handle in prepared.registry.snapshot().citable
        assert 'Jeanne Marie Beaumont is an American poet' in wire
        assert 'role="bridge_identity" supports="subject_identity"' in wire
        assert 'seminar_venue="unverified"' in wire and 'final_answer_supported="false"' in wire
        # A mock generation receives the real prepared messages/registry; no model call.
        observed = []
        async def mock_generation(messages):
            observed.append(messages)
            assert 'Jeanne Marie Beaumont is an American poet' in messages[-1].content.split('</retrieval>')[0]
            return 'The original identifies Jeanne Marie Beaumont. <ref id="'+handle+'"/>'
        output = await mock_generation(prepared.messages)
        assert '<ref id="'+handle+'"/>' in output and len(observed)==1
        details = state.rescue_evidence[0].metadata['bridge_rescue']
        assert details['identity_proof']['final_context_identity_supported'] is True
        assert details['entity_bridge_verified'] is False and details['final_answer_supported'] is False
        assert details['relation_status']=='INSTITUTION_LOCATION_ONLY_EVENT_VENUE_UNVERIFIED'
        assert state.rescue_evidence[0].score==repository.rows[LOCATION].score
        assert any(row.get('decision')=='IDENTITY_CONTEXT_ADMITTED' for row in state.bridge_trace)
        assert len(models.chat_calls)==len(models.rerank_calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('field',['tenant','owner','kb','document','version','pointer','disabled'])
def test_current_chain_revalidated_after_rerank_before_identity_admission(field):
    def corrupt(row):
        if field=='tenant': row.metadata['tenant_id']='foreign-tenant'
        if field=='owner': row.metadata['owner_id']='foreign-owner'
        if field=='kb': row.knowledge_base_id='foreign-kb'
        if field=='document': row.knowledge_id='foreign-document'
        if field=='version': row.metadata['source_version']='expired-version'
        if field=='pointer': row.next_chunk_id='expired-chain'
        if field=='disabled': row.is_enabled=False
    async def run():
        state,context,repository,models = scenario(mutate=corrupt)
        with pytest.raises((RetrievalScopeError,PermissionError)):
            await rescue_bridge(state,context,repository,models)
        assert not getattr(state,'bridge_identity_evidence',())
        assert all(row.id!=ANCHOR for row in context_results(state))
    asyncio.run(run())


@pytest.mark.parametrize('limit',[2000,2500])
def test_identity_context_budget_rejection_keeps_first_and_second_hop_unchanged(limit):
    async def run():
        state,context,repository,models = scenario(limit=limit)
        await rescue_bridge(state,context,repository,models)
        assert not getattr(state,'bridge_identity_evidence',())
        assert len(state.rescue_evidence)==1 and state.rescue_evidence[0].id==LOCATION
        assert state.rescue_evidence[0].metadata['bridge_rescue']['identity_proof']['final_context_identity_supported'] is False
        assert any(row.get('decision')=='IDENTITY_CONTEXT_SKIPPED' and row.get('reason')=='IDENTITY_CONTEXT_BUDGET_EXCEEDED' for row in state.bridge_trace)
        if limit==2000:
            # The unchanged base generation encoding is itself >2000; do not
            # claim a usable answer merely because the optional anchor rolled back.
            with pytest.raises(ContextBudgetError):
                await render(state,context)
        else:
            _,prepared = await render(state,context)
            assert 'Jeanne Marie Beaumont is an American poet' not in prepared.messages[-1].content.split('</retrieval>')[0]
            assert 'seminar_venue="unverified"' in prepared.messages[-1].content
    asyncio.run(run())


def test_bridge_off_never_reads_or_admits_identity():
    async def run():
        state,context,repository,models = scenario(mode='off')
        await rescue_bridge(state,context,repository,models)
        assert not repository.calls and not models.chat_calls and not models.rerank_calls
        assert not getattr(state,'bridge_identity_evidence',())
        assert not state.rescue_evidence
    asyncio.run(run())


def test_citation_handles_are_included_in_identity_admission_budget():
    async def run():
        from dataclasses import replace
        # Prove the exact citations-off/on boundary using the actual encoders.
        wide,wide_context,wide_repo,wide_models = scenario()
        await rescue_bridge(wide,wide_context,wide_repo,wide_models)
        wide_final,_ = await render(wide,wide_context)
        limited = replace(wide_final,context_char_limit=2875)
        request = GenerationRequest('budget','session','answer',wide.query,limited,
            evidence=tuple(Evidence.from_result(row) for row in context_results(wide)))
        prepare_generation(replace(request,citations_enabled=False))
        with pytest.raises(ContextBudgetError):
            prepare_generation(replace(request,citations_enabled=True))
        state,context,repository,models = scenario(limit=2875)
        await rescue_bridge(state,context,repository,models)
        assert not state.bridge_identity_evidence
        assert state.rescue_evidence[0].id==LOCATION
        assert state.rescue_evidence[0].metadata['bridge_rescue']['identity_proof']['final_context_identity_supported'] is False
        assert any(row.get('decision')=='IDENTITY_CONTEXT_SKIPPED' for row in state.bridge_trace)
        _,prepared = await render(state,context)
        assert '<chunk id="c1"' in prepared.messages[-1].content
    asyncio.run(run())


def test_identity_field_preserves_legacy_pipeline_positional_query_binding():
    from backend.weknora_port.retrieval.pipeline import PipelineState
    # Existing inherited positional fields: retrieval flag, top K, three
    # result arrays; query was and remains the next positional argument.
    state = PipelineState(True,30,[],[],[],'Original positional query')
    assert state.query=='Original positional query'
    assert state.bridge_identity_evidence==[]
    keyed = PipelineState(True,30,[],[],[],'Original positional query',bridge_identity_evidence=[])
    assert keyed.query==state.query


def test_fresh_prepare_turn_fixed_clarification_clears_admitted_identity_evidence():
    """Concrete reviewer reproduction: bridge runs before ambiguity cleanup."""
    from dataclasses import replace
    from types import SimpleNamespace
    from backend.weknora_port.service import (RuntimeService, TurnConfiguration,
        AcceptedTurn, OrdinaryTurnRequest, MemoryBundle)
    from backend.weknora_port.conversation.types import MemoryContext
    from backend.weknora_port.generation import RouterPolicy
    from backend.weknora_port.retrieval.query import known_ambiguous_reference
    from backend.weknora_port.streaming.manager import InMemoryStreamStore
    async def run():
        state,context,repository,models = scenario()
        query = ('Where is the seminar Jeanne Marie Beaumont directed in 2007-2010 '
                 'located, and what is it used for?')
        state.query = state.rewrite_query = query
        context = replace(context,session_id='session')
        ordinary = OrdinaryTurnRequest('session',query,context.request_id,
            knowledge_base_scope=context.scope.knowledge_base_ids,document_scope=context.scope.document_ids)
        accepted = AcceptedTurn(context.request_id,'session','user','assistant',context,
            MemoryContext(7,'owner',False),ordinary,True,False)
        prior = SimpleNamespace(query='Compare Jeanne Marie Beaumont and Alice Bennett.',answer='A comparison.',
            knowledge_base_scope=context.scope.knowledge_base_ids,document_scope=context.scope.document_ids,
            knowledge_references=())
        async def history(*args,**kwargs): return (prior,)
        async def memory(*args): return MemoryBundle()
        class Pipeline:
            async def run(self,s,c):
                assert known_ambiguous_reference(s)
                await rescue_bridge(s,c,repository,models)
                assert any(row.id==ANCHOR for row in s.bridge_identity_evidence)
                return await into_chat_message(s,c)
        async def pipeline(*args): return Pipeline()
        service = RuntimeService(None,InMemoryStreamStore(),SimpleNamespace(load=history),SimpleNamespace(load=memory),
            configuration=lambda _:TurnConfiguration(state,{'policy':RouterPolicy(mode='CHEAP_ONLY'),'citations_enabled':True}),
            pipeline_factory=pipeline,generation_factory=None)
        prepared = await service.prepare_turn(accepted)
        assert prepared.request.fixed_fallback
        assert prepared.request.finalized.result_ids==() and prepared.request.finalized.rendered_contexts==''
        assert prepared.request.evidence==() and prepared.request.history==() and prepared.request.memory_prompt==''
        assert not state.merge_results and not state.rescue_evidence and not state.bridge_identity_evidence
        assert dict(prepared.rankings)['context']==()
        assert state.query_trace[-1]['facts_admitted']==0
        assert len(models.chat_calls)==len(models.rerank_calls)==1  # Retrieval simulation still executed.
        rendered = prepare_generation(prepared.request)
        assert not rendered.registry.snapshot().chunks
    asyncio.run(run())
