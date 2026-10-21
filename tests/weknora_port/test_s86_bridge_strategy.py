"""S8.6 B source/branch contracts. All model and new Search seams are mocked."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.weknora_port.models.types import (CallCancelled,CallContext,ProviderError,
    TrustedScope)
from backend.weknora_port.retrieval.bridge_rescue import (BridgePlan,
    prepare_verified_bridge,original_windows,sentence_table)
from backend.weknora_port.retrieval.bridge_strategy import (BridgeStatus,
    assess_location_sources,rescue_strategy_bridge)
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import RetrievalScopeError,SearchParams,SearchTarget
from backend.weknora_port.retrieval.types import SearchResult,context_results
from backend.weknora_port.generation.context import Evidence,prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from test_bridge_rescue import Repository,Models,CTX,QUESTION,QUOTE,row,extraction


def build(*,pool=(),normal=(),question=QUESTION,limit=16000,topk=30):
    first=row('first',QUOTE,score=.82)
    rows=[first,*pool,*normal]
    repo=Repository(rows)
    state=PipelineState(query=question,rewrite_query=question,targets=(SearchTarget('kb'),),
        source_evidence_mode='bounded_source_v1',bridge_rescue_mode='off',
        bridge_cheap_model_id='cheap',rerank_model_id='bge',bridge_context_char_limit=limit,
        rerank_top_k=topk,search_results=deepcopy(rows),rerank_results=[deepcopy(first)],
        merge_results=deepcopy([first,*normal]))
    state.retrieval_strategy_mode='query_structure_v1'
    state.strategy_rerank_scores={r.id:(.95 if r.id=='first' else .01) for r in rows}
    state.strategy_rerank_accepted_ids={'first',*(r.id for r in normal)}
    models=Models(json.dumps(extraction(first),ensure_ascii=False))
    return state,repo,models


class Recall(Repository):
    def __init__(self,rows):
        super().__init__(rows)
        self.hybrid_calls=[]
    async def hybrid_search(self,targets,params,context,query_embedding=None):
        self.hybrid_calls.append((targets,params,context))
        return deepcopy(list(self.rows.values()))


async def call(state,repo,models,new=(),*,status='OK',reason=''):
    requests=[]
    async def retrieve(q1,context):
        requests.append((q1,context))
        if status!='OK':
            return SimpleNamespace(state=None,status=status,reason=reason)
        # The callback performs one new scoped repository recall. The root
        # Pipeline integration tests exercise its actual retrieve_once stages.
        search=Recall(new)
        results=await search.hybrid_search(state.targets,SearchParams(q1),context)
        assert len(search.hybrid_calls)==1
        assert search.hybrid_calls[0][1].query_text==q1
        child=PipelineState(query=q1,rewrite_query=q1,targets=state.targets,
            source_evidence_mode='bounded_source_v1',enable_rewrite=False,enable_expansion=False,
            search_results=deepcopy(results),rerank_results=deepcopy(results),merge_results=deepcopy(results))
        child.strategy_rerank_scores={r.id:.93 for r in results}
        for r in new:
            repo.rows[r.id]=deepcopy(r)
        return SimpleNamespace(state=child,status='OK' if results else 'EMPTY',reason='')
    assessment=await rescue_strategy_bridge(state,CTX,repo,models,retrieve)
    return assessment,requests


def prepared(state,context=CTX):
    async def run():
        finalized=await into_chat_message(state,context)
        request=GenerationRequest(context.request_id,'session','answer',state.query,finalized,
            evidence=tuple(Evidence.from_result(r) for r in context_results(state)),citations_enabled=True)
        return finalized,prepare_generation(request)
    return asyncio.run(run())


def test_new_scoped_recall_adds_previously_absent_source_without_overwriting_firsthop():
    state,repo,models=build()
    source=row('new-location','Arbor Poetry Seminar is located in Harbor City, Canada.',document='place')
    before=deepcopy((state.query,state.rewrite_query,state.targets,state.search_results,state.rerank_results,state.merge_results))
    result,requests=asyncio.run(call(state,repo,models,[source]))
    assert result.status==BridgeStatus.COMPLETE and result.new_recall
    assert result.context_admitted and result.source_chain_supported
    assert not result.delivered and not result.final_answer_supported
    assert set(result.required_source_ids)=={'first','new-location'}
    assert len(requests)==1 and requests[0][0]=='Where is Arbor Poetry Seminar located?'
    assert requests[0][1].request_id=='run/strategy/round-1'
    assert requests[0][1].scope==CTX.scope
    assert len(models.chat_calls)==1 and not models.rerank_calls
    assert before==(state.query,state.rewrite_query,state.targets,state.search_results,state.rerank_results,state.merge_results)
    assert state.rescue_evidence[0].score==source.score
    final,wire=prepared(state)
    assert final.result_ids==('first','new-location')
    text='\n'.join(m['content'] for m in json.loads(wire.full_prompt))
    assert QUOTE in text and source.content in text
    assert 'source_chunk_id' not in text and 'sentence_id' not in text
    assert len(wire.registry.snapshot().chunks)==2


def test_complete_normal_source_stops_before_pool_bge_or_new_search():
    source=row('normal','Arbor Poetry Seminar is located in Harbor City.',document='place')
    state,repo,models=build(normal=[source])
    result,requests=asyncio.run(call(state,repo,models))
    assert result.status==BridgeStatus.COMPLETE and result.context_admitted
    assert not requests and not models.rerank_calls
    assert len(models.chat_calls)==1 and not state.rescue_evidence


def test_complete_old_pool_uses_once_bge_and_no_new_search():
    source=row('pooled','Arbor Poetry Seminar is located in Harbor City.',document='place')
    state,repo,models=build(pool=[source])
    result,requests=asyncio.run(call(state,repo,models))
    assert result.status==BridgeStatus.COMPLETE and result.context_admitted
    assert not requests and len(models.chat_calls)==len(models.rerank_calls)==1
    assert models.rerank_calls[0][0]=='Where is Arbor Poetry Seminar located?'
    assert models.rerank_calls[0][2].request_id=='run/strategy/bridge-rerank'


def test_related_institution_does_not_stop_single_new_recall_or_prove_event_venue():
    institution=row('institution','Arbor Poetry is located in Harbor City.',document='place')
    state,repo,models=build(pool=[institution])
    result,requests=asyncio.run(call(state,repo,models,status='EMPTY'))
    assert result.status==BridgeStatus.RELATED_ONLY and result.new_recall
    assert not result.final_answer_supported and len(requests)==1
    assert len(models.rerank_calls)==1 and len(models.chat_calls)==1
    assert state.rescue_evidence[0].id==institution.id
    final,wire=prepared(state)
    text='\n'.join(m['content'] for m in json.loads(wire.full_prompt))
    assert institution.content in text and 'seminar_venue="unverified"' in text


def test_explicit_event_question_uses_held_q1_without_inventing_event_year():
    question='Where was the seminar Alice Bennett directed in 2011–2014 held?'
    source=row('event-evidence','Arbor Poetry Seminar was held at Harbor Hall.',document='place')
    state,repo,models=build(question=question)
    result,requests=asyncio.run(call(state,repo,models,[source]))
    assert result.status==BridgeStatus.COMPLETE and result.source_chain_supported
    assert requests[0][0]=='Where was Arbor Poetry Seminar held?'
    assert '2011' not in requests[0][0]
    assert state.rescue_evidence[0].metadata['bridge_rescue']['target_attribute']=='event_venue'
    _,wire=prepared(state)
    text='\n'.join(m['content'] for m in json.loads(wire.full_prompt))
    assert 'supports="event_venue"' in text


@pytest.mark.parametrize('text',[
    'Arbor Poetry Seminar was located in Harbor City.',
    'Arbor Poetry Seminar may be located in Harbor City.',
    'Arbor Poetry Seminar is not located in Harbor City.',
    'Arbor Poetry Seminar Annex is located in Harbor City.',
    'The rival of Arbor Poetry Seminar is located in Harbor City.',
    'Arbor Poetry Seminar is studying whether the laboratory is located in Harbor City.',
    'Arbor Poetry Seminar is located in Harbor City or Paris.',
    'Arbor Poetry Seminar is located in Harbor City before moving to Paris.',
])
def test_uncertain_or_wrong_subject_location_cannot_be_complete(text):
    async def run():
        state,repo,models=build()
        plan=await prepare_verified_bridge(state,CTX,repo,models)
        assert isinstance(plan,BridgePlan)
        source=row('unsafe',text,document='place')
        assessment=assess_location_sources(plan,[source])
        assert assessment.status==BridgeStatus.UNVERIFIED
        assert not assessment.final_answer_supported
    asyncio.run(run())


@pytest.mark.parametrize('text',[
    'Arbor Poetry Seminar was not held in Harbor City.',
    'Arbor Poetry Seminar might be held in Harbor City.',
    'Arbor Poetry Seminar was formerly held in Harbor City.',
    'The rival of Arbor Poetry Seminar was held in Harbor City.',
    'Arbor Poetry Seminar was held in Harbor City and the laboratory in Paris.',
    'Arbor Poetry Seminar is located in Harbor City.',
])
def test_closed_event_predicate_rejects_location_clue_or_complex_expression(text):
    async def run():
        state,repo,models=build()
        plan=replace(await prepare_verified_bridge(state,CTX,repo,models),target_attribute='event_venue')
        assert assess_location_sources(plan,[row('unsafe',text)]).status==BridgeStatus.UNVERIFIED
    asyncio.run(run())


def test_conflicting_direct_facts_stop_without_new_recall():
    a=row('a','Arbor Poetry Seminar is located in Harbor City.')
    b=row('b','Arbor Poetry Seminar is located in Paris.')
    state,repo,models=build(normal=[a,b])
    result,requests=asyncio.run(call(state,repo,models))
    assert result.status==BridgeStatus.CONFLICT and not requests
    assert not models.rerank_calls and not state.rescue_evidence


def test_no_first_hop_does_not_recall_or_guess_object():
    state,repo,models=build()
    models.content='{"status":"NO_FIRST_HOP"}'
    result,requests=asyncio.run(call(state,repo,models))
    assert result.status==BridgeStatus.UNVERIFIED and result.reason=='NO_FIRST_HOP'
    assert not requests and not models.rerank_calls


@pytest.mark.parametrize('question',[
    'Where was Alice Bennett born?',
    'Where is the laboratory Alice Bennett directed in 2011–2014 located?',
])
def test_question_target_must_match_verified_bridge_object(question):
    state,repo,models=build(question=question)
    source=row('new','Arbor Poetry Seminar is located in Harbor City.',document='place')
    result,requests=asyncio.run(call(state,repo,models,[source]))
    assert result.status==BridgeStatus.UNVERIFIED
    assert result.reason=='QUESTION_BRIDGE_TARGET_UNVERIFIED'
    assert not result.final_answer_supported and not requests


@pytest.mark.parametrize('status,reason',[('BUDGET_OMITTED','QUERY_UNIT_LIMIT'),
    ('BUDGET_OMITTED','DUPLICATE_QUERY'),('EMPTY','')])
def test_recall_stops_at_one_empty_or_budget_result(status,reason):
    state,repo,models=build()
    result,requests=asyncio.run(call(state,repo,models,status=status,reason=reason))
    assert result.status==BridgeStatus.NO_CANDIDATE and len(requests)==1
    assert not result.delivered and not state.rescue_evidence
    assert result.new_recall is (status=='EMPTY')
    assert result.budget_omitted is (status=='BUDGET_OMITTED')
    if reason:
        assert result.reason==reason


def test_request_cache_prevents_reextract_and_resend():
    async def run():
        state,repo,models=build()
        calls=[]
        async def recall(*args):
            calls.append(args)
            return SimpleNamespace(status='EMPTY',state=None,reason='')
        one=await rescue_strategy_bridge(state,CTX,repo,models,recall)
        two=await rescue_strategy_bridge(state,CTX,repo,models,recall)
        assert one==two and len(calls)==len(models.chat_calls)==1
    asyncio.run(run())


def test_missing_identity_anchor_admission_is_reused_for_later_exact_source():
    institution=row('institution','Arbor Poetry is located in Harbor City.',document='place')
    state,repo,models=build(normal=[institution])
    anchor=row('identity-anchor','Alice Bennett is a poet.',start_at=0,
        end_at=len('Alice Bennett is a poet.'),chunk_index=0,next_chunk_id='first')
    main=row('first','She directed Arbor Poetry Seminar from 2011–2014.',chunk_index=1,
        pre_chunk_id=anchor.id,start_at=anchor.end_at,
        end_at=anchor.end_at+len('She directed Arbor Poetry Seminar from 2011–2014.'))
    state.search_results=[deepcopy(main),deepcopy(institution)]
    state.rerank_results=[deepcopy(main)]
    state.merge_results=[deepcopy(main),deepcopy(institution)]
    repo.rows[main.id]=deepcopy(main)
    repo.rows[anchor.id]=deepcopy(anchor)
    models.content=json.dumps(extraction(main,sentence_text=main.content,
        subject='She',subject_resolution='UNVERIFIED'),ensure_ascii=False)
    exact=row('new-exact','Arbor Poetry Seminar is located in Harbor Hall.',document='place')
    result,requests=asyncio.run(call(state,repo,models,[exact]))
    assert result.status==BridgeStatus.COMPLETE and result.context_admitted and result.source_chain_supported
    assert not result.delivered and not result.final_answer_supported
    assert set(result.required_source_ids)=={'first','identity-anchor','new-exact'}
    assert len(requests)==1 and len(models.chat_calls)==1
    assert [r.id for r in state.bridge_identity_evidence]==[anchor.id]
    assert len({r.id for r in context_results(state)})==len(context_results(state))
    _,wire=prepared(state)
    text='\n'.join(m['content'] for m in json.loads(wire.full_prompt))
    assert anchor.content in text and main.content in text and exact.content in text


@pytest.mark.parametrize('phase',['pool','recall'])
def test_conflicting_related_sources_across_stages_stop_further_recall(phase):
    a=row('normal-hint','Arbor Poetry is located in Harbor City.',document='place-a')
    b=row('conflict-hint','Arbor Poetry is located in Paris.',document='place-b')
    state,repo,models=build(normal=[a],pool=[b] if phase=='pool' else [])
    result,requests=asyncio.run(call(state,repo,models,[b] if phase=='recall' else []))
    assert result.status==BridgeStatus.CONFLICT
    assert not result.final_answer_supported
    assert len(requests)==(0 if phase=='pool' else 1)


@pytest.mark.parametrize('change',['kb','document','tenant','version','bytes'])
def test_new_source_scope_current_version_and_byte_mismatch_fail_closed(change):
    async def run():
        state,repo,models=build()
        source=row('new','Arbor Poetry Seminar is located in Harbor City.',document='place')
        async def recall(q1,context):
            admitted=deepcopy(source)
            if change=='kb': admitted.knowledge_base_id='other-kb'
            if change=='document':
                state.targets=(SearchTarget('kb','knowledge',('doc',)),)
            stored=deepcopy(admitted)
            if change=='tenant': stored.metadata['tenant_id']=8
            if change=='version': stored.metadata['source_version']=stored.chunk_metadata['source_version']='v2'
            if change=='bytes': stored.content=stored.content.replace('Harbor','London')
            repo.rows['new']=stored
            child=PipelineState(query=q1,rewrite_query=q1,targets=state.targets,merge_results=[admitted])
            return SimpleNamespace(status='OK',state=child,reason='')
        with pytest.raises(RetrievalScopeError):
            await rescue_strategy_bridge(state,CTX,repo,models,recall)
        assert not state.rescue_evidence
    asyncio.run(run())


@pytest.mark.parametrize('kind',['permission','unknown','cancelled','infrastructure'])
def test_second_retrieval_errors_propagate_without_conversion_to_empty(kind):
    async def run():
        state,repo,models=build()
        error={'permission':PermissionError('denied'),
            'unknown':ProviderError('LOCAL_EXECUTION_UNKNOWN_STOP',sent=True),
            'cancelled':CallCancelled(),'infrastructure':RuntimeError('DATABASE_UNAVAILABLE')}[kind]
        async def recall(*args): raise error
        with pytest.raises(type(error)):
            await rescue_strategy_bridge(state,CTX,repo,models,recall)
        assert not state.rescue_evidence
    asyncio.run(run())


def test_context_and_topk_limits_preserve_existing_evidence_and_stop_admission():
    for topk in (1,30):
        state,repo,models=build(topk=topk,limit=1 if topk==30 else 16000)
        original=deepcopy(state.merge_results)
        source=row('new','Arbor Poetry Seminar is located in Harbor City.',document='place')
        result,requests=asyncio.run(call(state,repo,models,[source]))
        assert result.status==BridgeStatus.COMPLETE and result.budget_omitted
        assert not result.delivered and not result.final_answer_supported
        assert state.merge_results==original and not state.rescue_evidence


def test_first_hop_is_re_read_after_new_recall_even_when_identity_already_visible():
    async def run():
        state,repo,models=build()
        source=row('new-location','Arbor Poetry Seminar is located in Harbor City.',document='place')
        async def recall(q1,context):
            repo.rows['first'].metadata['source_version']='v2'
            repo.rows['first'].chunk_metadata['source_version']='v2'
            repo.rows[source.id]=deepcopy(source)
            child=PipelineState(query=q1,rewrite_query=q1,targets=state.targets,merge_results=[source])
            return SimpleNamespace(status='OK',state=child,reason='')
        with pytest.raises(RetrievalScopeError):
            await rescue_strategy_bridge(state,CTX,repo,models,recall)
        assert not state.rescue_evidence
    asyncio.run(run())


def test_related_context_budget_omission_stops_before_new_recall():
    source=row('hint','Arbor Poetry is located in Harbor City.',document='place')
    state,repo,models=build(normal=[source],limit=1)
    result,requests=asyncio.run(call(state,repo,models))
    assert result.status==BridgeStatus.RELATED_ONLY and result.budget_omitted
    assert not result.context_admitted and not requests and not state.rescue_evidence


@pytest.mark.parametrize('phase',['extraction','pool'])
def test_not_sent_auxiliary_budget_denial_retains_originals_and_stops_recall(phase):
    a=row('normal-hint','Arbor Poetry is located in Harbor City.',document='place-a')
    b=row('pool-hint','Arbor Poetry is located in Harbor City.',document='place-b')
    state,repo,models=build(normal=[a] if phase=='pool' else [],pool=[b] if phase=='pool' else [])
    original=deepcopy((state.query,state.rewrite_query,state.search_results,state.rerank_results,state.merge_results))
    denied=ProviderError('LOCAL_EXECUTION_LIMIT_DENIED',sent=False)
    if phase=='extraction': models.chat_error=denied
    else: models.rerank_error=denied
    result,requests=asyncio.run(call(state,repo,models))
    assert result.budget_omitted and result.reason=='LOCAL_EXECUTION_LIMIT_DENIED'
    assert not result.final_answer_supported and not requests
    assert original==(state.query,state.rewrite_query,state.search_results,state.rerank_results,state.merge_results)
    if phase=='pool':
        assert result.status==BridgeStatus.RELATED_ONLY and result.context_admitted
        assert state.bridge_normal_qualifications[a.id]['final_answer_supported'] is False
    else:
        assert result.status==BridgeStatus.UNVERIFIED and not result.context_admitted


def test_sent_auxiliary_budget_denial_remains_a_hard_stop():
    state,repo,models=build()
    models.chat_error=ProviderError('LOCAL_EXECUTION_LIMIT_DENIED',sent=True)
    with pytest.raises(ProviderError,match='LOCAL_EXECUTION_LIMIT_DENIED'):
        asyncio.run(call(state,repo,models))


def test_candidate_off_does_not_extract():
    state,repo,models=build()
    state.retrieval_strategy_mode='off'
    result,requests=asyncio.run(call(state,repo,models))
    assert result.reason=='STRATEGY_NOT_ENABLED'
    assert not models.chat_calls and not requests


def test_frozen_frost_b_remains_related_after_new_recall_and_delivers_raw_name_anchor():
    async def run():
        fixtures=Path(__file__).parent/'fixtures'/'s8_4_bridge'/'inputs.json'
        saved=next(c for c in json.loads(fixtures.read_text(encoding='utf8'))['cases'] if c['case_id']=='frost-B')
        rows=[SearchResult(**r) for r in saved['source_rows']]
        repo=Repository(rows)
        repo.tenant_id=332
        state=PipelineState(query=saved['query'],rewrite_query=saved['rewrite_query'],
            targets=tuple(SearchTarget(k) for k in saved['knowledge_base_ids']),
            bridge_cheap_model_id='cheap',rerank_model_id='bge',source_evidence_mode='bounded_source_v1',
            search_results=[SearchResult(**r) for r in saved['search_results'] if r],
            rerank_results=[SearchResult(**r) for r in saved['rerank_results']],
            merge_results=[SearchResult(**r) for r in saved['merge_results']])
        state.retrieval_strategy_mode='query_structure_v1'
        first=next(r for r in rows if r.id=='ec07a1fd-8a8e-4f23-a7d6-65d385d2343b')
        context=CallContext('frozen-b',TrustedScope('owner',tuple(saved['knowledge_base_ids'])))
        windows,sources,_=await original_windows([first],state,context,repo)
        sentence=next(s for s in sentence_table(windows,sources).values() if s.text.startswith('She served as the Director'))
        data={'status':'FIRST_HOP_CANDIDATE','sentence_id':sentence.sentence_id,'subject':'She',
            'subject_resolution':'UNVERIFIED','predicate':'served as the Director of',
            'object':'The Frost Place Advanced Seminar','time':'2007–2010','target_attribute':'location',
            'bridge_search_phrases':['The Frost Place Advanced Seminar','The Frost Place']}
        models=Models(json.dumps(data,ensure_ascii=False))
        raw=saved['evidence_trace'][0]
        state.strategy_rerank_scores={raw['input_ids'][r['index']]:r['score'] for r in raw['response']}
        state.strategy_rerank_accepted_ids={r.id for r in state.rerank_results}
        requests=[]
        async def empty(q1,ctx):
            requests.append((q1,ctx))
            return SimpleNamespace(status='EMPTY',state=None,reason='')
        result=await rescue_strategy_bridge(state,context,repo,models,empty)
        assert result.status==BridgeStatus.RELATED_ONLY and result.new_recall
        assert result.context_admitted and not result.source_chain_supported
        assert not result.delivered and not result.final_answer_supported
        assert requests[0][0]=='Where is The Frost Place Advanced Seminar located?'
        assert len(requests)==len(models.chat_calls)==len(models.rerank_calls)==1
        finalized=await into_chat_message(state,context)
        wire=prepare_generation(GenerationRequest('frozen-b','s','m',state.query,finalized,
            evidence=tuple(Evidence.from_result(r) for r in context_results(state)),citations_enabled=True))
        text='\n'.join(m['content'] for m in json.loads(wire.full_prompt))
        anchor=repo.rows['abc3a6aa-5453-4f0a-b7b6-5270cc96067f']
        assert anchor.id in finalized.result_ids and anchor.content in text
        assert 'Jeanne Marie Beaumont' in text and 'Franconia' in text
        assert 'seminar_venue="unverified"' in text
        assert all(not r.metadata['bridge_rescue']['final_answer_supported'] for r in state.rescue_evidence)
    asyncio.run(run())
