"""Ordinary Python chain orchestration; durable admission and model gates injected.

HTTP attaches to replay. It never owns the background run cancellation token.
"""
from __future__ import annotations
import asyncio
import inspect
import re
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from .conversation.types import MemoryContext, resolve_scope
from .generation import GenerationRequest, GenerationReplay, Evidence, History, ValidatedRewrite
from .generation.coordinator import check_context
from .models.types import CallContext, ProviderError, CallCancelled, RunDeadlineExceeded
from .retrieval.pipeline import PipelineState
from .retrieval.search import SearchNothing
from .streaming.events import StreamEvent
from .streaming.replay import ReplayService, AssistantRun

@dataclass(frozen=True)
class OrdinaryTurnRequest:
    session_id: str
    content: str = field(repr=False)
    request_id: str
    mode: str = 'quick'
    knowledge_base_scope: tuple[str, ...] = ()
    document_scope: tuple[str, ...] = ()
    summary_model_id: str = ''
    reasoning_effort: str = ''
    accept_language: str = ''

    def __post_init__(self):
        if not all(isinstance(x,str) and x for x in (self.session_id,self.content,self.request_id)):
            raise ProviderError('TURN_IDENTITY_REQUIRED')
        if not all(isinstance(value,str) and len(value)<=256 for value in (self.summary_model_id,self.reasoning_effort,self.accept_language)):
            raise ProviderError('TURN_CONFIGURATION_INVALID')
        if self.mode != 'quick':
            raise ProviderError('ORDINARY_MODE_REQUIRED')
        if not all(isinstance(x,tuple) and all(isinstance(v,str) and v for v in x) for x in (self.knowledge_base_scope,self.document_scope)):
            raise ProviderError('TURN_SCOPE_INVALID')

@dataclass(frozen=True)
class AcceptedTurn:
    run_id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str
    context: CallContext = field(repr=False)
    memory_context: MemoryContext = field(repr=False)
    request: OrdinaryTurnRequest = field(repr=False)
    is_new: bool = False
    replay_only: bool = True
    client_request_id: str = ''

@dataclass(frozen=True)
class MemoryBundle:
    background: str = field(default='',repr=False)
    prompt: str = field(default='',repr=False)
    origin_kbs: tuple[str, ...] | None = None

@dataclass(frozen=True)
class PreparedOrdinaryTurn:
    request: Any = field(repr=False)
    context: CallContext = field(repr=False)
    rankings: tuple[tuple[str,tuple[tuple[str,str,str],...]],...] = ()

@dataclass
class TurnConfiguration:
    pipeline_state: PipelineState
    generation_options: dict[str, Any] = field(default_factory=dict)
    fallback: dict[str, Any] | None = None

class OrdinaryMemoryLoader:
    """Use existing P4 reads and the exact QUERY_UNDERSTAND background wrapper."""
    def __init__(self, memory_service, origin_resolver=None):
        self.memory_service = memory_service
        self.origin_resolver = origin_resolver

    async def load_scoped(self, context, query, scope):
        # Whole-subject memory has conservative, session-wide provenance.
        # Check it before recall can embed or render out-of-scope document titles.
        if self.origin_resolver is not None:
            origins = tuple(await self.origin_resolver(resolve_scope(context)))
            if not set(origins).issubset(scope.knowledge_base_ids):
                return MemoryBundle()
        return await self.load(context, query)

    async def load(self, context, query):
        recall = await self.memory_service.recall(context, query)
        value = await self.memory_service.retrieval_context(context)
        background = ''
        if value.background or value.interests or value.documents:
            background = '\n\n<asker_background note="背景仅用于消解指代和补全检索词，不要当作问题的一部分">'
            if value.background:
                background += '\n' + value.background
            if value.interests:
                background += '\n长期关注：' + '、'.join(value.interests)
            if value.documents:
                background += '\n常查资料：' + '、'.join(value.documents)
            background += '\n</asker_background>'
        origins = None
        if (background or recall.prompt) and self.origin_resolver is not None:
            origins = tuple(await self.origin_resolver(resolve_scope(context)))
        return MemoryBundle(background, recall.prompt, origins)

class TurnRepository(Protocol):
    async def accept(self, request: OrdinaryTurnRequest, principal: Any) -> AcceptedTurn:
        """Authorize scope and atomically claim stable run/user/assistant identities.

        Same payload replay returns is_new=False forever, including after restart;
        payload mismatch must fail. Never infer ownership from request JSON.
        """
    async def validate_run(self, session_id: str, assistant_message_id: str, principal: Any) -> AcceptedTurn: ...
    async def commit_failure(self, accepted: AcceptedTurn, safe_code: str) -> bool:
        """Persist assistant failure before terminal events; preserve attempts/UNKNOWN.

        Return True after committed failure, False for already successful final.
        A successful final commit cannot be overwritten. This callback must raise
        rather than manufacture failure completion if persistence fails.
        """

async def _resolve(value):
    return await value if inspect.isawaitable(value) else value

class RuntimeService:
    def __init__(self, repository, store, history_service, memory_loader, *,
                 configuration, pipeline_factory, generation_factory, poll_seconds=.1, document_clarifier=None):
        self.repository,self.store=repository,store
        self.history_service,self.memory_loader=history_service,memory_loader
        self.configuration=configuration
        self.document_clarifier=document_clarifier
        self.pipeline_factory,self.generation_factory=pipeline_factory,generation_factory
        self.poll_seconds=poll_seconds
        self._tasks: dict[tuple[str,str,str], asyncio.Task] = {}
        self._active: dict[tuple[str,str,str], asyncio.Task] = {}
        self.background_errors: dict[tuple[str,str,str], str] = {}

    @staticmethod
    def _key(turn):
        return (turn.context.scope.owner_id,turn.session_id,turn.run_id)

    @staticmethod
    def _validate(turn):
        if not isinstance(turn,AcceptedTurn) or not isinstance(turn.context,CallContext) or not isinstance(turn.memory_context,MemoryContext):
            raise ProviderError('TRUSTED_TURN_REQUIRED')
        resolve_scope(turn.memory_context)
        if not isinstance(turn.request,OrdinaryTurnRequest):
            raise ProviderError('TURN_REQUEST_REQUIRED')
        if (turn.context.request_id,turn.context.session_id,turn.request.request_id,turn.request.session_id)!=(turn.run_id,turn.session_id,turn.run_id,turn.session_id):
            raise ProviderError('TURN_IDENTITY_MISMATCH')
        if not turn.context.scope.owner_id or turn.memory_context.principal_storage_id != turn.context.scope.owner_id:
            raise ProviderError('TURN_OWNER_MISMATCH')
        if turn.is_new == turn.replay_only:
            raise ProviderError('TURN_ADMISSION_INVALID')

    async def accept_turn(self, request, principal):
        turn=await self.repository.accept(request,principal)
        self._validate(turn)
        if turn.is_new:
            try:
                await self.store.append_event(turn.session_id,turn.assistant_message_id,
                    StreamEvent(turn.run_id,'agent_query',data={'request_id':turn.run_id,'query':request.content,'session_id':turn.session_id,'assistant_message_id':turn.assistant_message_id}))
            except Exception as exc:
                await self.publish_error(turn,exc)
                return turn
            key=self._key(turn)
            if key not in self._tasks:
                task=asyncio.create_task(self.run_turn(turn),name='ordinary:'+turn.run_id)
                self._tasks[key]=task
                def completed(done):
                    # An older callback must not release a replacement task.
                    if self._tasks.get(key) is done:
                        self._tasks.pop(key)
                    try:
                        done.result()
                    except BaseException as exc:
                        self.background_errors[key]=self._safe_code(exc)
                        # Keep diagnostic codes only, with bounded process memory.
                        while len(self.background_errors)>128:
                            self.background_errors.pop(next(iter(self.background_errors)))
                task.add_done_callback(completed)
        return turn

    async def validate_run(self, session_id, assistant_message_id, principal):
        turn=await self.repository.validate_run(session_id,assistant_message_id,principal)
        self._validate(turn)
        return turn

    @staticmethod
    def _safe_code(error):
        if isinstance(error,asyncio.CancelledError):
            return 'MODEL_CALL_CANCELLED'
        code=getattr(error,'code','ORDINARY_CHAIN_FAILED')
        return code if isinstance(code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}',code) else 'ORDINARY_CHAIN_FAILED'

    async def _publish_cancelled(self, accepted):
        events,_=await self.store.get_events(accepted.session_id,accepted.assistant_message_id,0)
        answer=''.join(event.content for event in events if event.type=='answer')
        thinking=''.join(event.content for event in events if event.type=='thinking')
        import html
        references={}
        for event in events:
            if event.type!='references':continue
            for ref in (event.data or {}).get('references',[]):
                identifier=ref.get('id','')
                if not identifier:continue
                attribute='url' if ref.get('knowledge_source','').lower()=='web_search' else 'chunk_id'
                if f'{attribute}="{html.escape(identifier,quote=True)}"' in answer:references[identifier]=ref
        receipt=await self.repository.commit_cancelled(accepted,answer,thinking,list(references.values()))
        if receipt is not True and receipt is not False:raise ProviderError('TURN_CANCEL_COMMIT_UNVERIFIED')
        status='cancelled';code='MODEL_CALL_CANCELLED'
        if receipt is False:
            state=await self.repository.status(accepted.session_id,accepted.assistant_message_id)
            status='failed' if state.status=='error' else state.status
            code=state.error_code
        events,_=await self.store.get_events(accepted.session_id,accepted.assistant_message_id,0)
        if not any(event.type=='complete' for event in events):
            await self.store.append_event(accepted.session_id,accepted.assistant_message_id,
                StreamEvent(accepted.run_id,'complete',data={'status':status,'error_code':code,'lifecycle_completed':True}))

    async def publish_error(self, accepted, error):
        from .models.cloud_errors import public_cloud_error
        self._validate(accepted)
        code=self._safe_code(error)
        if isinstance(error,(CallCancelled,asyncio.CancelledError)):
            cleanup=asyncio.create_task(self._publish_cancelled(accepted))
            try:await asyncio.wait_for(asyncio.shield(cleanup),5)
            except BaseException:
                cleanup.cancel();await asyncio.gather(cleanup,return_exceptions=True)
                raise
            return
        receipt=await self.repository.commit_failure(accepted,code)
        if receipt is False:
            return
        if receipt is not True:
            raise ProviderError('TURN_FAILURE_COMMIT_UNVERIFIED')
        await self.store.append_event(accepted.session_id,accepted.assistant_message_id,
            StreamEvent(accepted.run_id,'error',content=public_cloud_error(code),done=True,data={'error_code':code,'status':'failed'}))
        await self.store.append_event(accepted.session_id,accepted.assistant_message_id,
            StreamEvent(accepted.run_id,'complete',data={'status':'cancelled' if isinstance(error,(CallCancelled,asyncio.CancelledError)) else 'failed','error_code':code}))

    async def _progress(self, turn, stage, status, **fields):
        observer = getattr(getattr(self, 'application', None), 'observability', None)
        if observer is not None:
            observer._safe('PROGRESS', lambda: observer.stage(turn.run_id, stage, status, fields))
        safe={k:v for k,v in fields.items() if k in ('candidate_count','selected_evidence_count') and type(v) is int and v>=0}
        if stage == 'understand':
            for key in ('q0_sha256', 'rewrite_sha256'):
                if isinstance(fields.get(key),str) and re.fullmatch('[0-9a-f]{64}',fields[key]):
                    safe[key] = fields[key]
            reason = fields.get('rewrite_reason')
            if isinstance(reason,str) and re.fullmatch('[A-Z_]{0,64}',reason):
                safe['rewrite_reason'] = reason
        if stage == 'history':
            count = fields.get('history_origin_dropped')
            if type(count) is int and count >= 0:
                safe['history_origin_dropped'] = count
            reasons = fields.get('history_origin_reasons')
            if isinstance(reasons,list) and all(value in ('HISTORY_ORIGIN_UNKNOWN','HISTORY_SCOPE_OUTSIDE_CURRENT','HISTORY_REFERENCE_SCOPE_MISMATCH') for value in reasons):
                safe['history_origin_reasons'] = reasons
        if fields.get('role') in ('cheap','expensive','baseline'):
            safe['role']=fields['role']
        attempt=fields.get('attempt_id')
        if isinstance(attempt,str) and len(attempt)<=128 and attempt.isascii():
            safe['attempt_id']=attempt
        try:
            await self.store.append_event(turn.session_id,turn.assistant_message_id,
                StreamEvent(turn.run_id,'progress',data={'stage':stage,'status':status,**safe}))
        except (CallCancelled,RunDeadlineExceeded):
            raise
        except Exception:
            pass

    async def request_stop(self, session_id, message_id, principal):
        turn=await self.validate_run(session_id,message_id,principal)
        events,_=await self.store.get_events(session_id,message_id,0)
        if any(event.type=='complete' for event in events):
            return {'success':True,'message':'Message already completed'}
        import uuid
        await self.store.append_event(session_id,message_id,StreamEvent('stop-'+str(uuid.uuid4()),'stop',done=True,
            data={'session_id':session_id,'message_id':message_id,'reason':'user_requested'}))
        return {'success':True,'message':'Generation stopped'}

    async def _watch_stop(self,turn):
        # Fixed source helpers.go:startStopWatcher: shared stream,300ms,2h;
        # request disconnect never owns this cancellation mechanism.
        if turn.context.cancelled is None:return
        deadline=asyncio.get_running_loop().time()+7200;offset=0
        while asyncio.get_running_loop().time()<deadline:
            await asyncio.sleep(.3)
            try:events,offset=await self.store.get_events(turn.session_id,turn.assistant_message_id,offset)
            except Exception:continue
            for event in events:
                if event.type=='stop':turn.context.cancelled.set();return
                if event.type=='complete' or event.type=='error' and event.done:return

    async def prepare_turn(self,turn,progress=None):
        """The ordinary entry's sole history/memory/retrieval/Context preparation."""
        self._validate(turn)
        if turn.replay_only:raise ProviderError('TURN_PREPARATION_REPLAY_DENIED')
        if progress is None:
            async def progress(stage,status,**fields):await self._progress(turn,stage,status,**fields)
        check_context(turn.context)
        config=await _resolve(self.configuration(turn))
        if not isinstance(config,TurnConfiguration) or not isinstance(config.pipeline_state,PipelineState):
            raise ProviderError('TURN_CONFIGURATION_REQUIRED')
        if 'evidence_insufficient' in config.generation_options:
            raise ProviderError('TURN_GENERATION_IDENTITY_OVERRIDE')
        state=config.pipeline_state
        # q0 and identities are server admission values, not mutable config.
        state.query=turn.request.content
        state.user_message_id=turn.user_message_id
        if self.document_clarifier is not None:
            clarification=await _resolve(self.document_clarifier(turn,state.language))
            check_context(turn.context)
            if clarification is not None:
                from .retrieval.context import FinalizedContext
                options=dict(config.generation_options)
                options.update(is_fallback=True,fixed_fallback=clarification,fallback_response=clarification)
                options.setdefault('language',state.language)
                state.query_trace.append({'stage':'clarification','decision':'FIXED_CLARIFICATION',
                                          'reason':'DOCUMENT_SCOPE_AMBIGUOUS','facts_admitted':0})
                await progress('clarification','completed',reason='DOCUMENT_SCOPE_AMBIGUOUS')
                request=GenerationRequest(turn.run_id,turn.session_id,turn.assistant_message_id,
                    turn.request.content,FinalizedContext(turn.request.content,'',()),**options)
                return PreparedOrdinaryTurn(request,turn.context)
        await progress('history','started')
        rounds=tuple(h for h in await self.history_service.load(turn.memory_context,turn.session_id,state.max_rounds,
                     exclude_request_id=turn.run_id) if getattr(h,'request_id',None) != turn.run_id)
        admitted, origin_reasons = [], set()
        for h in rounds:
            kbs, documents = getattr(h,'knowledge_base_scope',None), getattr(h,'document_scope',None)
            if (not isinstance(kbs,tuple) or not kbs or not isinstance(documents,tuple)
                    or any(not isinstance(v,str) or not v for v in (*kbs,*documents))):
                origin_reasons.add('HISTORY_ORIGIN_UNKNOWN')
                continue
            if (not set(kbs).issubset(turn.context.scope.knowledge_base_ids)
                    or turn.context.scope.document_ids and (not documents or not set(documents).issubset(turn.context.scope.document_ids))):
                origin_reasons.add('HISTORY_SCOPE_OUTSIDE_CURRENT')
                continue
            refs = getattr(h,'knowledge_references',())
            if any(not isinstance(r,dict) or r.get('knowledge_base_id') not in kbs
                   or not r.get('knowledge_id') or documents and r.get('knowledge_id') not in documents for r in refs):
                origin_reasons.add('HISTORY_REFERENCE_SCOPE_MISMATCH')
                continue
            admitted.append(h)
        dropped = len(rounds) - len(admitted)
        rounds = tuple(admitted)
        history=tuple(History(h.query,h.answer) for h in rounds)
        state.history=list(rounds)
        await progress('history','completed',history_origin_dropped=dropped,history_origin_reasons=sorted(origin_reasons))
        check_context(turn.context)
        await progress('memory','started')
        scoped_loader = getattr(self.memory_loader, 'load_scoped', None)
        memory = await scoped_loader(turn.memory_context, turn.request.content, turn.context.scope) if scoped_loader else await self.memory_loader.load(turn.memory_context,turn.request.content)
        if not isinstance(memory,MemoryBundle):
            raise ProviderError('MEMORY_BUNDLE_REQUIRED')
        model_context = turn.context
        if memory.background or memory.prompt:
            if not isinstance(memory.origin_kbs, tuple) or not memory.origin_kbs or not all(isinstance(k, str) and k for k in memory.origin_kbs):
                raise ProviderError('MODEL_MEMORY_ORIGIN_UNKNOWN')
            if set(memory.origin_kbs).issubset(turn.context.scope.knowledge_base_ids):
                model_context = replace(turn.context, required_origin_kbs=tuple(sorted(set(turn.context.required_origin_kbs) | set(memory.origin_kbs))))
            else:
                # Drop the content together with its lineage; never relabel it.
                memory = MemoryBundle()
        state.memory_background=memory.background
        await progress('memory','completed')
        check_context(turn.context)
        pipeline=await _resolve(self.pipeline_factory(turn,progress))
        from .retrieval.query import requires_ambiguity_clarification
        fallback=None
        evidence_insufficient=False
        try:
            finalized=await pipeline.run(state,model_context)
        except SearchNothing:
            from .retrieval.context import FinalizedContext,render_placeholders
            from .retrieval.query import go_strip
            check_context(model_context)
            if requires_ambiguity_clarification(state):
                finalized=FinalizedContext(state.query,'',())
            else:
                if config.fallback is None:raise
                fallback=dict(config.fallback)
                evidence_insufficient=True
                query=go_strip(state.rewrite_query) or state.query
                prompt=fallback['prompt']
                if fallback['strategy']=='model' and prompt!='':
                    listing=await _resolve(fallback['documents']())
                    check_context(model_context)
                    prompt=render_placeholders(prompt,{'query':query,'language':state.language,'kb_documents':listing})
                    if state.image_description and not state.chat_model_supports_vision:
                        prompt+='\n\n[用户上传图片内容]\n'+state.image_description
                    if state.quoted_context:prompt+='\n\n'+state.quoted_context
                if state.attachment_prompt:query+=state.attachment_prompt
                finalized=FinalizedContext(query,'',(),prompt,state.images)
            state.merge_results=[]
            state.rescue_evidence=[]
        ambiguity_clarification=requires_ambiguity_clarification(state)
        if ambiguity_clarification:
            from .retrieval.context import FinalizedContext
            # Retrieval remains real, but candidates cannot choose the user's
            # intended entity. The existing fixed path performs no answer call.
            finalized=FinalizedContext(state.query,'',())
            state.merge_results=[]
            state.rescue_evidence=[]
            state.bridge_identity_evidence=[]
            state.rendered_contexts=''
            state.user_content=state.query
            state._finalized_context=finalized
            state.query_trace.append({'stage':'clarification','decision':'FIXED_CLARIFICATION',
                                      'reason':'AMBIGUOUS_REFERENCE','facts_admitted':0})
            history=()
            fallback=None
            evidence_insufficient=False
            if hasattr(pipeline,'_progress'):
                await pipeline._progress('context','completed',selected_evidence_count=0)
        check_context(turn.context)
        from .retrieval.types import context_results
        evidence=tuple(Evidence.from_result(r) for r in context_results(state) if r is not None)
        # Existing query-understand validation supplies the rewrite; P5
        # retains q0 and computes max features, without another model call.
        understanding=ValidatedRewrite(turn.request.content,state.rewrite_query) if state.rewrite_query else None
        from datetime import datetime
        options=dict(config.generation_options)
        if fallback is not None:
            options.update(is_fallback=True,fallback_response=fallback['response'],options=fallback['options'],system_prompt=finalized.system_prompt_override)
            if fallback['strategy']!='model' or fallback['prompt']=='':options['fixed_fallback']=fallback['response']
        if ambiguity_clarification:
            text=('Which item do you mean? Please specify its name so I can search for the right document evidence.'
                  if state.language.startswith('English') else '你指的是哪一个对象？请明确名称，我再检索对应的文档依据。')
            options.update(is_fallback=True,fixed_fallback=text,fallback_response=text)
        options.setdefault('rendered_at',datetime.now())
        options.setdefault('language',state.language)
        options.setdefault('supports_vision',state.chat_model_supports_vision)
        options.setdefault('faq_priority_enabled',state.faq_priority_enabled)
        forbidden={'run_id','session_id','message_id','q0','finalized','evidence','history','memory_prompt','understanding','on_context_prepared','evidence_insufficient'} & options.keys()
        if forbidden:
            raise ProviderError('TURN_GENERATION_IDENTITY_OVERRIDE')
        if getattr(state, 'retrieval_strategy_mode', 'off') == 'query_structure_v1':
            from .retrieval.strategy_executor import record_delivered_coverage
            options['on_context_prepared'] = lambda prepared: record_delivered_coverage(state, prepared)
        request=GenerationRequest(turn.run_id,turn.session_id,turn.assistant_message_id,turn.request.content,finalized,
            evidence=evidence,history=history,memory_prompt='' if fallback is not None or ambiguity_clarification else memory.prompt,understanding=understanding,evidence_insufficient=evidence_insufficient,**options)
        if getattr(state, 'rescue_evidence', ()):
            from .generation.context import prepare_generation
            from .retrieval.types import ContextBudgetError
            try:
                prepare_generation(request)
            except ContextBudgetError:
                # The final source encoder can be larger than retrieval markup.
                # Preserve every normal source and drop only the optional addition.
                from .retrieval.context import into_chat_message
                state.rescue_evidence=[]
                state.bridge_trace.append({'decision':'SKIPPED','reason':'BRIDGE_GENERATION_CONTEXT_BUDGET_EXCEEDED'})
                state.bridge_trace.append({'stage':'bridge_context','decision':'FINAL_ANSWER_UNVERIFIED',
                    'evidence_chain_supported':False,'generated_answer_verified':False,
                    'reason':'BRIDGE_GENERATION_CONTEXT_BUDGET_EXCEEDED'})
                finalized=await into_chat_message(state,model_context,persist_sink=getattr(pipeline,'persist_sink',None))
                evidence=tuple(Evidence.from_result(r) for r in context_results(state) if r is not None)
                request=replace(request,finalized=finalized,evidence=evidence)
                if hasattr(pipeline,'_progress'):
                    await pipeline._progress('context','completed',selected_evidence_count=len(evidence),bridge_budget_skipped=True)
        stages=[('search',state.search_results),('rerank',state.rerank_results),('context',context_results(state))]
        rankings=tuple((name,tuple((r.id,r.knowledge_base_id,r.knowledge_id) for r in rows if r is not None)) for name,rows in stages)
        return PreparedOrdinaryTurn(request,model_context,rankings)

    async def run_turn(self, turn):
        self._validate(turn)
        if turn.replay_only:
            return None
        key=self._key(turn)
        if key in self._active:
            return None
        owner=asyncio.current_task()
        self._active[key]=owner
        stop_watch=asyncio.create_task(self._watch_stop(turn),name='stop-watch:'+turn.run_id)
        async def progress(stage,status,**fields):
            await self._progress(turn,stage,status,**fields)
        try:
            prepared=await self.prepare_turn(turn,progress)
            request,model_context=prepared.request,prepared.context
            coordinator=await _resolve(self.generation_factory(turn,progress))
            return await coordinator.run(request,model_context)
        except GenerationReplay:
            return None  # source persistent lifecycle: attach, never resend
        except (Exception,asyncio.CancelledError) as exc:
            await self.publish_error(turn,exc)
            return None
        finally:
            if self._active.get(key) is owner:
                self._active.pop(key)
            stop_watch.cancel()
            await asyncio.gather(stop_watch,return_exceptions=True)

    async def replay(self, accepted, principal):
        authorized=await self.validate_run(accepted.session_id,accepted.assistant_message_id,principal)
        if authorized.run_id!=accepted.run_id or authorized.context.scope!=accepted.context.scope:
            raise ProviderError('TURN_REPLAY_IDENTITY_MISMATCH')
        class Identity:
            async def validate_session(self, session_id, supplied_principal):
                if session_id != authorized.session_id or supplied_principal != principal:
                    raise ProviderError('TURN_REPLAY_IDENTITY_MISMATCH')

            async def validate_assistant_message(self, session_id, message_id):
                if (session_id,message_id)!=(authorized.session_id,authorized.assistant_message_id):
                    raise ProviderError('TURN_REPLAY_IDENTITY_MISMATCH')
                return AssistantRun(authorized.run_id,authorized.assistant_message_id)

        replay=ReplayService(self.store,Identity(),poll_seconds=self.poll_seconds)
        async for event in replay.replay(authorized.session_id,authorized.assistant_message_id,principal):
            yield event

    async def drain_background(self):
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()),return_exceptions=True)
