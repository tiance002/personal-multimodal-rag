"""Closed rule planning and source-only serial execution; no real sends."""
import asyncio
from copy import deepcopy

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope, ProviderError
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing, RetrievalScopeError
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.strategy import (
    QueryType, plan_query, try_rule_compare, validate_strategy_mode, strategy_applicable,
)
from backend.weknora_port.retrieval.strategy_executor import (
    RoundResult, execute_plan, apply_strategy_result, fit_context_budget, record_delivered_coverage,
)
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest


def setup_state(query='比较PostgreSQL和Redis的持久化与并发能力'):
    state = PipelineState(query=query, rewrite_query=query, targets=(SearchTarget('kb'),),
        source_evidence_mode='bounded_source_v1', rerank_top_k=4)
    state.retrieval_strategy_mode = 'query_structure_v1'
    state.strategy_trace, state.strategy_rounds, state.strategy_coverage = [], [], {}
    return state, CallContext('request', TrustedScope('owner', ('kb',), ('doc',)))


def source(id, content, *, version='v1', score=.1):
    return SearchResult(id, content, score, knowledge_id='doc', knowledge_base_id='kb',
        start_at=0, end_at=len(content), metadata={'source_version':version},
        chunk_metadata={'source_version':version})


def test_closed_chinese_compare_preserves_full_dimension_and_spans():
    text='比较PostgreSQL和Redis的持久化与并发能力'
    rows=try_rule_compare(text)
    assert [row.query_text for row in rows] == [
        'PostgreSQL的持久化与并发能力是什么？', 'Redis的持久化与并发能力是什么？']
    assert [text[slice(*r.object_span)] for r in rows] == ['PostgreSQL', 'Redis']
    assert all(text[slice(*r.dimension_span)]=='持久化与并发能力' for r in rows)


def test_closed_english_compare_keeps_dimension():
    rows=try_rule_compare('Compare PostgreSQL and Redis in terms of persistence and concurrency.')
    assert [r.query_text for r in rows] == [
        'What is known about PostgreSQL regarding persistence and concurrency?',
        'What is known about Redis regarding persistence and concurrency?']


@pytest.mark.parametrize('query', [
    '比较PostgreSQL和Redis的非持久化能力',
    '比较PostgreSQL和Redis在2024年的持久化能力',
    '比较PostgreSQL和Redis的持久化能力，并提供文档依据',
    '比较PostgreSQL和Redis的持久化或并发能力',
    '比较PostgreSQL和Redis的客户的持久化能力',
    '比较PostgreSQL和Redis和MongoDB的持久化能力',
    '比较Redis和Redis的并发能力',
    'Compare PostgreSQL and Redis in terms of latency, only from document A.',
    'Compare PostgreSQL and Redis in terms of not persistence.',
    "Compare PostgreSQL's cluster and Redis in terms of concurrency.",
    'Compare A and B in terms of latency if memory is limited.',
    'Compare A and B in terms of latency or throughput.',
    'Compare A and B in terms of non-persistence.',
    'Compare A and B in terms of persistence and explain the result.',
    '比较PostgreSQL和Redis的性能并给出表格',
])
def test_unsafe_or_unclosed_rule_is_rejected(query):
    assert try_rule_compare(query) is None


@pytest.mark.parametrize('kind,reason', [
    ('DIRECT','SINGLE_QUERY'), ('JOINT','SINGLE_QUERY'), ('UNKNOWN','SINGLE_QUERY'),
    ('DEPENDENT','DEPENDENT_FIRST_ROUND'), ('PARALLEL','MODEL_PLAN_NOT_ENABLED'),
    ('MIXED','MODEL_PLAN_NOT_ENABLED'), ('invented','SINGLE_QUERY'),
])
def test_type_advice_does_not_create_generic_planner(kind, reason):
    plan=plan_query('q0','Compare A and B in terms of latency',kind)
    assert plan.strategy=='PLAIN' and len(plan.subqueries)==1 and plan.reason==reason
    assert plan.q0=='q0'


def test_rule_only_runs_for_model_simple_compare():
    plan=plan_query('q0','Compare A and B in terms of latency','SIMPLE_COMPARE')
    assert plan.strategy=='RULE_COMPARE' and len(plan.subqueries)==2
    fallback=plan_query('q0','Compare A and B in terms of latency if limited','SIMPLE_COMPARE')
    assert fallback.reason=='RULE_NOT_APPLICABLE' and len(fallback.subqueries)==1


def test_mode_validation_and_nonapplicable_inputs():
    validate_strategy_mode('off','off')
    with pytest.raises(ValueError,match='REQUIRES_BOUNDED_SOURCE'):
        validate_strategy_mode('query_structure_v1','off')
    with pytest.raises(ValueError,match='MODE_INVALID'):
        validate_strategy_mode('bad','bounded_source_v1')
    state,_=setup_state()
    state.attachment_prompt='<no_document_attached />'
    assert strategy_applicable(state)
    for name,value in [('images',('image',)),('attachment_prompt','doc'),
                       ('quoted_context','quote'),('web_search_enabled',True),('graph_enabled',True)]:
        changed=deepcopy(state)
        setattr(changed,name,value)
        assert not strategy_applicable(changed)


def test_serial_substates_operation_ids_root_preservation_and_round_robin():
    async def run():
        state,ctx=setup_state()
        state.history=[{'query':'old','answer':'historical only'}]
        state.search_results=[source('stale','stale')]
        state.bridge_trace=[{'stale':True}]
        before=deepcopy(state)
        calls=[]
        async def retrieve(sub,child):
            assert not sub.enable_expansion and sub._strategy_serial and not sub.search_results and not sub.bridge_trace
            assert not calls or calls[-1][1]=='done'
            calls.append((child.request_id,'started'))
            await asyncio.sleep(0)
            index=len(calls)
            sub.merge_results=[source(f'chunk-{index}-1',f'fact-{index}-1'),
                               source(f'chunk-{index}-2',f'fact-{index}-2')]
            calls[-1]=(child.request_id,'done')
            return RoundResult(sub)
        plan=plan_query(state.query,state.rewrite_query,'SIMPLE_COMPARE')
        result=await execute_plan(state,ctx,plan,retrieve)
        assert [x[0] for x in calls]==['request/strategy/sq-1','request/strategy/sq-2']
        assert [r.id for r in result.selected]==['chunk-1-1','chunk-2-1','chunk-1-2','chunk-2-2']
        assert state==before
        apply_strategy_result(state,result)
        assert state.query==before.query and state.rewrite_query==before.rewrite_query
        assert state.history==before.history
        assert all(item['semantic_support']=='NOT_CHECKED' for item in state.strategy_coverage.values())
    asyncio.run(run())


def test_empty_branch_does_not_discard_good_branch():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            if len(calls)==1: raise SearchNothing()
            sub.merge_results=[source('good','current original')]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert [r.id for r in result.selected]==['good']
        assert result.coverage['sq-1']['delivery']=='NO_EVIDENCE'
        assert result.coverage['sq-2']['delivery']=='PENDING'
    asyncio.run(run())


@pytest.mark.parametrize('error',[PermissionError('DENIED'),ProviderError('UNKNOWN',sent=True),RetrievalScopeError()])
def test_unsafe_errors_are_not_empty_branches(error):
    async def run():
        state,ctx=setup_state()
        async def retrieve(sub,child): raise error
        with pytest.raises(type(error)):
            await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
    asyncio.run(run())


def test_infrastructure_error_is_distinct_and_stops_next_branch():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            return RoundResult(sub,'ERROR','DATABASE_UNAVAILABLE')
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert len(calls)==1 and result.coverage['sq-1']['delivery']=='ERROR'
        assert result.coverage['sq-2']['delivery']=='NOT_STARTED'
    asyncio.run(run())


@pytest.mark.parametrize('different',[{'version':'v2'}, {'content':'Different number 2025'}])
def test_same_id_conflicting_origin_or_raw_body_fails_closed(different):
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            sub.merge_results=[source('same',different.get('content','Original 2024'),
                version=different.get('version','v1'))] if len(calls)>1 else [source('same','Original 2024')]
            return RoundResult(sub)
        with pytest.raises(RetrievalScopeError):
            await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
    asyncio.run(run())


def test_identical_source_merges_coverage_but_different_numbers_stay():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            sub.merge_results=[source('same','Original 2024'),source('num'+str(len(calls)),'Count '+str(len(calls)))]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert [r.id for r in result.selected]==['same','num1','num2']
        assert result.selected[0].metadata['strategy_subquery_ids']==['sq-1','sq-2']
    asyncio.run(run())


def test_topk_omission_is_not_semantic_coverage():
    async def run():
        state,ctx=setup_state()
        state.rerank_top_k=1
        async def retrieve(sub,child):
            sub.merge_results=[source(child.request_id.rsplit('/',1)[-1],'source fact')]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert len(result.selected)==1
        assert result.coverage['sq-2']['delivery']=='BUDGET_OMITTED'
        assert result.coverage['sq-2']['semantic_support']=='NOT_CHECKED'
    asyncio.run(run())


def test_max_three_units_includes_previous_rounds():
    async def run():
        state,ctx=setup_state()
        state.strategy_rounds=[{},{}]
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            sub.merge_results=[source('good','source fact')]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert len(calls)==1 and result.coverage['sq-2']['delivery']=='BUDGET_OMITTED'
    asyncio.run(run())


def test_local_budget_stop_preserves_prior_source_and_unknown_propagates():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            if len(calls)==2: raise ProviderError('LOCAL_EXECUTION_LIMIT_DENIED')
            sub.merge_results=[source('retained','bounded original')]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert [r.id for r in result.selected]==['retained']
        assert result.coverage['sq-2']['delivery']=='BUDGET_OMITTED'
        assert result.units_used==2
        async def sent(sub,child): raise ProviderError('LOCAL_EXECUTION_LIMIT_DENIED',sent=True)
        with pytest.raises(ProviderError):
            await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),sent)
    asyncio.run(run())


def test_source_budget_result_continues_good_branch():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            if len(calls)==1: return RoundResult(sub,'BUDGET_OMITTED','SOURCE_UNIT_BUDGET_EXCEEDED')
            sub.merge_results=[source('kept','raw second branch')]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        assert result.coverage['sq-1']['delivery']=='BUDGET_OMITTED'
        assert [r.id for r in result.selected]==['kept']
    asyncio.run(run())


@pytest.mark.parametrize('change',[('knowledge_base_id','other-kb'),('knowledge_id','other-document'),
                                  ('chunk_type','summary')])
def test_noncurrent_scope_or_generated_source_is_never_admitted(change):
    async def run():
        state,ctx=setup_state()
        async def retrieve(sub,child):
            row=source('wrong','not admissible')
            setattr(row,*change)
            sub.merge_results=[row]
            return RoundResult(sub)
        with pytest.raises(RetrievalScopeError):
            await execute_plan(state,ctx,plan_query(state.query,state.query,'DIRECT'),retrieve)
    asyncio.run(run())


def test_cancel_and_duplicate_query_stop_before_callback():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            return RoundResult(sub,'EMPTY')
        state.strategy_queries_seen={' '.join(state.query.split()).casefold()}
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'DIRECT'),retrieve)
        assert not calls and result.coverage['sq-1']['reason']=='DUPLICATE_QUERY'
        from dataclasses import replace
        from backend.weknora_port.models.types import CallCancelled
        event=asyncio.Event()
        event.set()
        with pytest.raises(CallCancelled):
            await execute_plan(state,replace(ctx,cancelled=event),plan_query(state.query,state.query,'DIRECT'),retrieve)
    asyncio.run(run())


def test_actual_prepared_messages_required_for_delivery_and_budget_trim():
    async def run():
        state,ctx=setup_state()
        calls=[]
        async def retrieve(sub,child):
            calls.append(child.request_id)
            sub.merge_results=[source('source-'+str(len(calls)),'Exact raw '+str(len(calls)))]
            return RoundResult(sub)
        result=await execute_plan(state,ctx,plan_query(state.query,state.query,'SIMPLE_COMPARE'),retrieve)
        apply_strategy_result(state,result)
        finalized=await into_chat_message(state,ctx)
        prepared=prepare_generation(GenerationRequest('run','session','message',q0=state.query,
            evidence=tuple(Evidence.from_result(r) for r in state.merge_results),finalized=finalized,
            citations_enabled=True))
        record_delivered_coverage(state,prepared)
        assert all(item['delivery']=='DELIVERED' for item in state.strategy_coverage.values())
        # Public citation visibility is independent of actual source delivery.
        hidden=prepare_generation(GenerationRequest('run','session','message',q0=state.query,
            evidence=tuple(Evidence.from_result(r) for r in state.merge_results),finalized=finalized,
            citations_enabled=False))
        snapshot=hidden.registry.snapshot()
        record_delivered_coverage(state,hidden)
        assert all(item['delivery']=='DELIVERED' for item in state.strategy_coverage.values())
        assert hidden.registry.snapshot()==snapshot
        # An old parent/subchunk ID is not a delivered original body.
        state.merge_results[0].content='Absent raw text'
        record_delivered_coverage(state,prepared)
        assert state.strategy_coverage['sq-1']['delivery']=='BUDGET_OMITTED'
        assert state.strategy_coverage['sq-2']['delivery']=='DELIVERED'
        # Finalized generation encoding, not a raw-content length estimate.
        state.merge_results=[source('big-1','A'*7900),source('big-2','B'*7900)]
        state.merge_results[0].metadata['strategy_subquery_ids']=['sq-1']
        state.merge_results[1].metadata['strategy_subquery_ids']=['sq-2']
        await fit_context_budget(state,ctx)
        assert len(state.merge_results)==1
        assert state.strategy_coverage['sq-2']['delivery']=='BUDGET_OMITTED'
    asyncio.run(run())
