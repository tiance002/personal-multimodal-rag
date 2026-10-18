"""Real P3/P5 mechanisms with injected offline storage/providers; no live calls."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import pytest
from backend.weknora_port.service import (RuntimeService,OrdinaryTurnRequest,AcceptedTurn,
    TurnConfiguration,MemoryBundle,OrdinaryMemoryLoader)
from backend.weknora_port.conversation.types import MemoryContext,MemoryRecall,RetrievalContext
from backend.weknora_port.models.types import CallContext,TrustedScope,ProviderError,CallCancelled,RunDeadlineExceeded
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.streaming.events import StreamEvent
from test_pipeline import Repo,Registry,row,state
from test_generation import setup,event

def run(value):return asyncio.run(value)
REQ=OrdinaryTurnRequest('session','extract source','run',knowledge_base_scope=('kb',))
class DurableRepository:
    def __init__(self):self.turns={};self.failures=[];self.trace=[]
    async def accept(self,request,principal):
        if principal!='owner':raise ProviderError('SCOPE_DENIED')
        if request.knowledge_base_scope!=('kb',) or request.document_scope:raise ProviderError('SCOPE_DENIED')
        if request.request_id in self.turns:
            existing=self.turns[request.request_id]
            if existing.request!=request:raise ProviderError('TURN_PAYLOAD_CONFLICT')
            return replace(existing,is_new=False,replay_only=True)
        turn=AcceptedTurn(request.request_id,request.session_id,'user','assistant',
            CallContext(request.request_id,TrustedScope('owner',('kb',)),cancelled=asyncio.Event(),session_id=request.session_id),
            MemoryContext(1,'owner'),request,True,False)
        self.turns[request.request_id]=turn;self.trace.append('ACCEPT');return turn
    async def validate_run(self,session,message,principal):
        if principal!='owner':raise ProviderError('SCOPE_DENIED')
        t=self.turns['run']
        if (session,message)!=(t.session_id,t.assistant_message_id):raise ProviderError('SCOPE_DENIED')
        return replace(t,is_new=False,replay_only=True)
    async def commit_failure(self,turn,code):self.failures.append((turn.assistant_message_id,code));self.trace.append('FAILURE_COMMIT');return True
    async def commit_cancelled(self,turn,answer,thinking,references):
        self.failures.append((turn.assistant_message_id,'MODEL_CALL_CANCELLED'));self.trace.append('CANCEL_COMMIT');return True
class History:
    def __init__(self):self.calls=[]
    async def load(self,context,session,max_rounds):
        self.calls.append((context,session,max_rounds));return (SimpleNamespace(query='historic question',answer='historic answer'),)
class Memory:
    def __init__(self):self.calls=[]
    async def load(self,context,query):self.calls.append((context,query));return MemoryBundle('background','memory prompt',('kb',))

def runtime(*,error=None,configuration=None,repo=None,blocked=None):
    co,c,e,store,commit,life=setup([event('partial',done=True,finish='length',final=True)])
    async def get_events(session,message,offset):return store.events[offset:],len(store.events)
    store.get_events=get_events
    repository=repo or DurableRepository();retrieval=Repo([row('chunk-real','real source source evidence')])
    history=History();memory=Memory();counts={'pipeline':0,'generation':0}
    async def pipeline_factory(turn,progress):
        counts['pipeline']+=1
        if error:raise error
        if blocked:await blocked.wait()
        return RetrievalPipeline(retrieval,Registry(),on_progress=progress)
    async def generation_factory(turn,progress):counts['generation']+=1;co.on_progress=progress;return co
    config=configuration or (lambda turn:TurnConfiguration(state(),{'policy':RouterPolicy(mode='DYNAMIC')}))
    service=RuntimeService(repository,store,history,memory,configuration=config,
        pipeline_factory=pipeline_factory,generation_factory=generation_factory,poll_seconds=.001)
    return service,repository,retrieval,co,c,e,store,commit,counts

async def scenario_complete():
    service,repo,retrieval,co,c,e,store,commit,counts=runtime()
    accepted=await service.accept_turn(REQ,'owner')
    await service.drain_background()
    assert not service.background_errors and not repo.failures
    assert counts=={'pipeline':1,'generation':1} and len(retrieval.calls)==1
    assert len(c.calls)==len(e.calls)==1
    assert c.calls[0][0] is e.calls[0][0]
    rendered=''.join(m.content for m in e.calls[0][0])
    assert 'real source source evidence' in rendered and 'historic question' in rendered and 'memory prompt' in rendered
    assert store.events[0].type=='agent_query' and store.events[0].event_id=='run'
    assert store.events[0].data['request_id']==accepted.run_id
    assert sum(ev.type=='agent_query' for ev in store.events)==1
    assert store.trace[-2:]==['COMMIT','complete'] and len(commit.calls)==1
    progress=[ev.data for ev in store.events if ev.type=='progress']
    assert [p['stage'] for p in progress if p['status']=='started']==['history','memory','understand','retrieval','rerank','merge','context','generation','generation']
    assert any(p.get('selected_evidence_count')==1 for p in progress)
    assert all('query' not in p and 'content' not in p for p in progress)
    existing=await service.accept_turn(REQ,'owner')
    assert existing.replay_only and not existing.is_new
    assert await service.run_turn(existing) is None
    replay=[ev async for ev in service.replay(existing,'owner')]
    assert replay==store.events and len(c.calls)==len(e.calls)==1
    return service,accepted

def test_real_pipeline_generation_context_and_replay_chain():run(scenario_complete())

def test_restart_replays_durable_claim_without_retrieval_or_model():
    async def scenario():
        service,accepted=await scenario_complete()
        replacement,repo,retrieval,co,c,e,store,commit,counts=runtime(repo=service.repository)
        store.events=service.store.events.copy()
        existing=await replacement.accept_turn(REQ,'owner')
        assert await replacement.run_turn(existing) is None
        assert counts=={'pipeline':0,'generation':0} and not c.calls and not e.calls
        assert [ev async for ev in replacement.replay(existing,'owner')]==store.events
    run(scenario())

def test_http_disconnect_does_not_cancel_background_run():
    async def scenario():
        release=asyncio.Event();service,repo,retrieval,co,c,e,store,commit,counts=runtime(blocked=release)
        accepted=await service.accept_turn(REQ,'owner')
        iterator=service.replay(accepted,'owner')
        initial=await anext(iterator);assert initial.type=='agent_query'
        await iterator.aclose()
        assert not accepted.context.cancelled or not accepted.context.cancelled.is_set()
        release.set();await service.drain_background()
        assert len(commit.calls)==1 and store.events[-1].type=='complete'
    run(scenario())

@pytest.mark.parametrize('error',[ProviderError('MODEL_EGRESS_DENIED'),ProviderError('PROVIDER_HTTP_ERROR',sent=True),CallCancelled(),RunDeadlineExceeded(),RuntimeError('private URL secret')])
def test_failure_persists_assistant_before_error_complete_without_retry(error):
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime(error=error)
        accepted=await service.accept_turn(REQ,'owner');await service.drain_background()
        assert not c.calls and not e.calls and not commit.calls and len(repo.failures)==1
        assert repo.failures[0][0]=='assistant'
        if isinstance(error,CallCancelled):
            assert not any(event.type=='error' for event in store.events)
            assert store.events[-1].type=='complete' and store.events[-1].data['status']=='cancelled'
            assert store.events[-1].data['lifecycle_completed']
        else:
            assert store.events[-2].type=='error' and store.events[-1].type=='complete'
        assert 'private' not in str(store.events) and 'secret' not in str(store.events)
        await service.accept_turn(REQ,'owner');assert counts['pipeline']==1
    run(scenario())

def test_sent_provider_failure_preserves_attempt_unknown_and_no_retry():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime()
        c.outputs=[event('private draft'),ProviderError('PROVIDER_HTTP_ERROR',sent=True)]
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert len(c.calls)==1 and not e.calls and not commit.calls
        assert co.lifecycle.finished[0][0].cost_status=='UNKNOWN'
        assert 'private draft' not in str(store.events) and store.events[-1].type=='complete'
    run(scenario())

def test_failure_commit_failure_never_manufactures_terminal_completion():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime(error=ProviderError('MODEL_EGRESS_DENIED'))
        async def fail(*args):raise RuntimeError('private db failure')
        repo.commit_failure=fail
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert not any(ev.type in ('error','complete') for ev in store.events)
        assert list(service.background_errors.values())==['ORDINARY_CHAIN_FAILED']
    run(scenario())

def test_payload_scope_and_owner_rejections_never_start_chain():
    async def scenario():
        service,repo,*_=runtime()
        for request,principal in [(replace(REQ,knowledge_base_scope=('other',)),'owner'),(REQ,'other')]:
            with pytest.raises(ProviderError):await service.accept_turn(request,principal)
        accepted=await service.accept_turn(REQ,'owner');await service.drain_background()
        with pytest.raises(ProviderError,match='TURN_PAYLOAD_CONFLICT'):await service.accept_turn(replace(REQ,content='changed'),'owner')
        with pytest.raises(ProviderError):await anext(service.replay(accepted,'other'))
    run(scenario())

def test_generation_identity_overrides_rejected_before_model():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime(configuration=lambda turn:TurnConfiguration(state(),{'q0':'injected'}))
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert repo.failures[0][1]=='TURN_GENERATION_IDENTITY_OVERRIDE' and not c.calls
    run(scenario())

def test_exact_memory_background_adapter_uses_existing_p4_reads():
    class P4:
        async def recall(self,context,query):return MemoryRecall('relevant memory')
        async def retrieval_context(self,context):return RetrievalContext('profile',('interest',),('document',))
    bundle=run(OrdinaryMemoryLoader(P4()).load(MemoryContext(1,'owner'),'question'))
    assert bundle.prompt=='relevant memory'
    assert bundle.background=='\n\n<asker_background note="背景仅用于消解指代和补全检索词，不要当作问题的一部分">\nprofile\n长期关注：interest\n常查资料：document\n</asker_background>'

@pytest.mark.parametrize('failure',[CallCancelled(),RunDeadlineExceeded(),asyncio.CancelledError()])
def test_pipeline_progress_does_not_swallow_cancellation_or_run_deadline(failure):
    async def progress(*args,**kw):raise failure
    with pytest.raises(type(failure)):
        run(RetrievalPipeline(Repo(),Registry(),on_progress=progress).run(state(),CallContext('run',TrustedScope('owner',('kb',)))))

def test_pipeline_ordinary_observer_failure_nonfatal():
    async def progress(*args,**kw):raise RuntimeError('mock observer')
    result=run(RetrievalPipeline(Repo([row()]),Registry(),on_progress=progress).run(state(),CallContext('run',TrustedScope('owner',('kb',)))))
    assert result.result_ids==('a',)


def test_disconnect_offset_zero_replay_uses_source_initial_delta_coalescing():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime()
        accepted=await repo.accept(REQ,'owner')
        store.events=[StreamEvent('run','agent_query'),StreamEvent('segment','answer','first'),StreamEvent('segment','answer',' second'),StreamEvent('run','complete')]
        stream=service.replay(accepted,'owner')
        assert (await anext(stream)).type=='agent_query'
        await stream.aclose()
        replay=[ev async for ev in service.replay(accepted,'owner')]
        assert len(replay)==3 and replay[1].content=='first second' and replay[1].event_id=='segment'
        assert not c.calls and not e.calls and counts['pipeline']==0
    run(scenario())


def test_successful_atomic_final_cannot_be_replaced_by_sse_failure_terminal():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime()
        original=store.append_event
        async def append(session,message,ev):
            if ev.type=='complete':raise RuntimeError('mock unavailable SSE store')
            await original(session,message,ev)
        store.append_event=append
        async def persist_failure(turn,code):
            assert commit.calls  # authoritative atomic answer already persisted
            return False
        repo.commit_failure=persist_failure
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert len(commit.calls)==1 and not repo.failures
        assert not any(ev.type=='error' for ev in store.events)
        assert not any(ev.type=='complete' and ev.data.get('status')=='failed' for ev in store.events)
    run(scenario())

def test_failure_without_persistence_receipt_cannot_publish_terminal():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime(error=ProviderError('MODEL_EGRESS_DENIED'))
        async def no_receipt(*args):return None
        repo.commit_failure=no_receipt
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert not any(ev.type in ('error','complete') for ev in store.events)
        assert list(service.background_errors.values())==['TURN_FAILURE_COMMIT_UNVERIFIED']
    run(scenario())


def test_memory_unknown_lineage_fails_before_pipeline_and_chat():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime()
        async def unknown(context,query):return MemoryBundle('private derived text','')
        service.memory_loader.load=unknown
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert repo.failures==[('assistant','MODEL_MEMORY_ORIGIN_UNKNOWN')]
        assert counts=={'pipeline':0,'generation':0} and not c.calls and not e.calls
    run(scenario())


def test_in_scope_memory_lineage_reaches_query_pipeline_and_both_chat_attempts():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime()
        async def known(context,query):return MemoryBundle('derived private background','memory',('kb',))
        service.memory_loader.load=known
        original=service.pipeline_factory;seen=[]
        async def factory(turn,progress):
            pipeline=await original(turn,progress);actual=pipeline.run
            async def capture(state,context):
                seen.append(context);return await actual(state,context)
            pipeline.run=capture;return pipeline
        service.pipeline_factory=factory
        accepted=await service.accept_turn(REQ,'owner');await service.drain_background()
        assert not repo.failures and len(seen)==1
        assert seen[0].scope.knowledge_base_ids==('kb',)
        assert seen[0].required_origin_kbs==('kb',)
        assert accepted.context.required_origin_kbs==()
        assert c.calls[0][2].required_origin_kbs==e.calls[0][2].required_origin_kbs==('kb',)
    run(scenario())


def test_out_of_scope_memory_is_omitted_together_with_its_lineage():
    async def scenario():
        service,repo,retrieval,co,c,e,store,commit,counts=runtime()
        async def cross_kb(context,query):return MemoryBundle('private old title','private old fact',('kb-private',))
        service.memory_loader.load=cross_kb
        await service.accept_turn(REQ,'owner');await service.drain_background()
        assert not repo.failures and len(retrieval.calls)==1 and len(c.calls)==len(e.calls)==1
        for model in (c,e):
            assert 'private old' not in ''.join(m.content for m in model.calls[0][0])
            assert model.calls[0][2].scope.knowledge_base_ids==('kb',)
            assert model.calls[0][2].required_origin_kbs==()
    run(scenario())


def test_scoped_memory_checks_origins_before_any_recall_or_embedding():
    class P4:
        async def recall(self,*args):raise AssertionError('out-of-scope recall must not run')
        async def retrieval_context(self,*args):raise AssertionError('out-of-scope titles must not load')
    async def origins(scope):return ('kb','kb-private')
    bundle=run(OrdinaryMemoryLoader(P4(),origins).load_scoped(MemoryContext(1,'owner'),'question',TrustedScope('owner',('kb',))))
    assert bundle==MemoryBundle()


def test_shared_stop_marker_cancels_original_run_without_generation_retry_or_reader():
    async def scenario():
        release=asyncio.Event();service,repo,retrieval,co,c,e,store,commit,counts=runtime(blocked=release)
        accepted=await service.accept_turn(REQ,'owner')
        while counts['pipeline']==0:await asyncio.sleep(.001)
        reply=await service.request_stop('session','assistant','owner')
        assert reply['success']
        for _ in range(60):
            if accepted.context.cancelled.is_set():break
            await asyncio.sleep(.01)
        assert accepted.context.cancelled.is_set()
        release.set();await service.drain_background()
        assert not c.calls and not e.calls and counts['generation']==0
        assert repo.failures==[('assistant','MODEL_CALL_CANCELLED')]
        assert store.events[-1].type=='complete' and store.events[-1].data['status']=='cancelled'
        before=len(store.events)
        assert (await service.request_stop('session','assistant','owner'))['message']=='Message already completed'
        assert len(store.events)==before
        with pytest.raises(ProviderError,match='SCOPE_DENIED'):await service.request_stop('session','assistant','foreign')
    run(scenario())
