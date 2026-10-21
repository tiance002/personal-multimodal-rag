"""Source incremental streams; only upgrade-eligible Cheap drafts are buffered."""
from __future__ import annotations
import asyncio, hashlib, json, re
from dataclasses import asdict, replace
from ..models.types import CallCancelled, ModelCallTimeout, ProviderError, RunDeadlineExceeded, TokenUsage
from ..models.usage import accumulate_usage
from ..models.cloud_errors import CLOUD_ERRORS
from ..references.decoder import CitationStreamDecoder
from ..streaming.events import StreamEvent
from ..streaming.replay import FinalCommit, finalize_turn
from .context import prepare_generation
from .router import decide
from .types import GenerationError, GenerationResult, AttemptRecord, ClosedLifecycle, TokenEstimate
EMPTY_TRUNCATED_FALLBACK = "Sorry, this answer hit the model's per-response output limit before any text was produced. Try narrowing the question, or raise max_completion_tokens."


class FallbackChannelError(GenerationError):
    """Only constructed at this coordinator's unavailable/incomplete channel boundary."""


def fallback_output_failure(error):
    # Closed allowlist over actual adapter types/codes. In particular, no
    # subclass timeout/cancel/deadline, unknown API body, identity or gate error.
    if type(error) is FallbackChannelError:return True
    if type(error) is not ProviderError:return False
    if error.code=='PROVIDER_HTTP_ERROR':
        return type(error.status_code) is int and error.status_code in {400,404,409,422,429,500,502,503}
    return error.code in {'PROVIDER_TRANSPORT_ERROR','PROVIDER_RESPONSE_INVALID','PROVIDER_STREAM_FAILED','PROVIDER_NO_RESPONSE'}


def is_length(reason):
    return reason.strip().lower() in ('length', 'max_tokens', 'max_output_tokens')

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

def usage_dict(usage):
    return asdict(usage)

def public_attempt(record):
    from urllib.parse import urlsplit
    result=asdict(record)
    endpoint=urlsplit(record.endpoint)
    result['endpoint']=endpoint.scheme+'://'+(endpoint.hostname or '')+((':'+str(endpoint.port)) if endpoint.port else '')
    result['endpoint_sha256']=hashlib.sha256(record.endpoint.encode()).hexdigest()
    return result

def check_context(context):
    if context.cancelled and context.cancelled.is_set():
        raise CallCancelled()
    if context.deadline_kind == 'run' and context.deadline is not None and context.deadline <= asyncio.get_running_loop().time():
        raise RunDeadlineExceeded()

class GenerationCoordinator:

    def __init__(self, bindings, store, committer, *, lifecycle=None, estimator=None, on_progress=None):
        self.bindings = dict(bindings)
        self.store = store
        self.committer = committer
        self.lifecycle = lifecycle or ClosedLifecycle()
        self.estimator = estimator
        self.on_progress = on_progress

    async def _progress(self, stage, status, **fields):
        if self.on_progress is None:
            return
        try:
            result = self.on_progress(stage, status, **fields)
            if hasattr(result, '__await__'):
                await result
        except Exception:
            pass

    def _binding(self, role):
        binding = self.bindings.get(role)
        if binding is None:
            raise GenerationError('GENERATION_ROLE_UNAVAILABLE')
        if binding.role != role:
            raise GenerationError('GENERATION_ROLE_IDENTITY_MISMATCH')
        config = getattr(binding.model, 'config', None)
        if config is None or (config.provider, config.model) != (binding.provider, binding.model_requested):
            raise GenerationError('GENERATION_MODEL_IDENTITY_MISMATCH')
        if (config.endpoint_url or config.base_url) != binding.endpoint:
            raise GenerationError('GENERATION_ENDPOINT_IDENTITY_MISMATCH')
        return binding

    async def _attempt(self, request, prepared, context, role, ordinal, reason):
        binding = self._binding(role)
        attempt_id = f'{request.run_id}/answer/{ordinal}/{role}'
        envelope = digest({'attempt_id': attempt_id, 'role': role, 'prompt': prepared.prompt_sha256, 'context': prepared.context_sha256, 'options': asdict(request.options), 'provider': binding.provider, 'model': binding.model_requested, 'endpoint': binding.endpoint})
        record = AttemptRecord(attempt_id, ordinal, role, binding.provider, binding.model_requested, binding.endpoint, envelope, prepared.prompt_sha256, prepared.context_sha256, upgrade_reason=reason)
        attempt_context = replace(context, request_id=attempt_id)
        await self.lifecycle.begin_attempt(record, attempt_context)
        await self._progress('generation', 'started', attempt_id=attempt_id, role=role)
        registry = prepared.registry.clone_for_attempt()
        thinking_registry = prepared.registry.clone_for_attempt()
        answer_decoder = CitationStreamDecoder(registry)
        thinking_decoder = CitationStreamDecoder(thinking_registry)
        answer = []
        thinking = []
        usage = TokenUsage()
        finish = ''
        reported = None
        answer_done = False
        publish_live = not (role == 'cheap' and request.policy.mode == 'DYNAMIC')
        thinking_closed = False
        live_answer_closed = False

        async def publish(kind, content, done=False):
            if publish_live:
                await self.store.append_event(request.session_id, request.message_id,
                    StreamEvent(attempt_id + '-' + kind, kind, content, done,
                                data={'attempt_id': attempt_id, 'role': role,
                                      'finish_reason': finish, 'truncated': is_length(finish), 'is_fallback': request.is_fallback}))

        async def close_thinking():
            nonlocal thinking_closed
            if thinking and not thinking_closed:
                await publish('thinking', '', True)
                thinking_closed = True
        final_seen = False
        failure = None
        stream_error = None
        error_events = []
        decoders_flushed = False

        async def flush_decoders():
            nonlocal decoders_flushed, thinking_closed
            if decoders_flushed:
                return
            decoders_flushed = True
            thinking_tail = thinking_decoder.flush()
            if thinking_tail:
                thinking.append(thinking_tail)
                await publish('thinking', thinking_tail)
            answer_tail = answer_decoder.flush()
            if answer_tail:
                answer.append(answer_tail)
                await publish('answer', answer_tail)
            await close_thinking()
        try:
            check_context(context)
            if context.deadline_kind == 'model' and context.deadline is not None and context.deadline <= asyncio.get_running_loop().time():
                raise GenerationError('MODEL_DEADLINE_EXPIRED_BEFORE_CALL')
            if request.supports_vision and request.finalized.images and (not binding.model.config.supports_vision):
                raise GenerationError('GENERATION_SHARED_VISION_UNSUPPORTED')
            stream = binding.model.chat_stream(prepared.messages, request.options, attempt_context)
            if hasattr(stream, '__await__'):
                stream = await stream
            if stream is None:
                error=FallbackChannelError if request.is_fallback else GenerationError
                raise error('PROVIDER_STREAM_UNAVAILABLE', sent=False)
            try:
                async for event in stream:
                    check_context(context)
                    if event.usage is not None:
                        usage = event.usage
                    data = event.data or {}
                    if isinstance(data.get('model_reported'), str) and data['model_reported']:
                        reported = data['model_reported']
                    if reported is not None and reported != binding.model_requested:
                        raise GenerationError('PROVIDER_MODEL_IDENTITY_MISMATCH', sent=True)
                    if event.finish_reason:
                        finish = event.finish_reason
                    if event.response_type == 'error':
                        code = data.get('error_code') or 'PROVIDER_STREAM_FAILED'
                        if code == 'MODEL_CALL_TIMEOUT' and data.get('error_class') == 'ModelCallTimeout':
                            raise ModelCallTimeout(sent=True)
                        if code == 'MODEL_CALL_CANCELLED':
                            raise CallCancelled(sent=True)
                        if code == 'RUN_DEADLINE_EXCEEDED':
                            raise RunDeadlineExceeded(sent=True)
                        code = code if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code) else 'PROVIDER_STREAM_FAILED'
                        # Source OnEvent emits errors and continues the SAME channel.
                        # Security/identity/settlement denials are never recoverable.
                        if code.startswith(('MODEL_', 'SCOPE_', 'TURN_', 'GENERATION_')) or code in CLOUD_ERRORS or code == 'PROVIDER_MODEL_IDENTITY_MISMATCH':
                            raise GenerationError(code, sent=True)
                        if request.is_fallback:
                            if data.get('error_class')!='ProviderError':raise GenerationError(code,sent=True)
                            stream_error=ProviderError(code,status_code=data.get('status_code'),sent=True)
                            if not fallback_output_failure(stream_error):raise stream_error
                            continue  # Go fallback consumer ignores non-answer events.
                        stream_error = GenerationError(code, sent=True)
                        notice = StreamEvent(attempt_id + '-error-' + str(len(error_events)), 'error', code, True,
                            data={'error_code': code, 'stage': 'chat_completion_stream', 'non_terminal': True,
                                  'attempt_id': attempt_id, 'role': role})
                        error_events.append(notice)
                        if publish_live:
                            await self.store.append_event(request.session_id, request.message_id, notice)
                        continue
                    if event.is_final and event.response_type=='answer' and data.get('provider_answer_done') is False:
                        if request.is_fallback:
                            continue  # adapter EOF bookkeeping is not the source channel's Done.
                        # The adapter has already settled the response and any
                        # reported usage. Its EOF/[DONE] bookkeeping cannot
                        # certify that this answer actually finished.
                        raise GenerationError('PROVIDER_STREAM_INCOMPLETE',sent=True)
                    if request.is_fallback and event.response_type=='thinking':
                        continue  # fixed source fallback consumer handles answer events only.
                    if event.response_type == 'thinking':
                        delta = thinking_decoder.feed(event.content)
                        if event.done:
                            delta += thinking_decoder.flush()
                        if delta:
                            thinking_closed = False
                            thinking.append(delta)
                            await publish('thinking', delta)
                        if event.done:
                            await close_thinking()
                    elif event.response_type == 'answer' and (not answer_done):
                        delta = answer_decoder.feed(event.content)
                        if event.done:
                            delta += answer_decoder.flush()
                            answer_done = True
                        answer.append(delta)
                        if event.done and is_length(finish) and not ''.join(answer).strip():
                            delta = EMPTY_TRUNCATED_FALLBACK
                        await close_thinking()
                        await publish('answer', delta, event.done)
                        live_answer_closed = event.done
                    if event.is_final:
                        final_seen = True
                if request.is_fallback and not answer_done:
                    raise stream_error or FallbackChannelError('PROVIDER_STREAM_INCOMPLETE',sent=True)
                await flush_decoders()
                if stream_error is not None and not answer_done:
                    raise stream_error  # error-only EOF is not a successful answer or P5 upgrade.
                if not live_answer_closed:
                    tail = EMPTY_TRUNCATED_FALLBACK if is_length(finish) and not ''.join(answer).strip() else ''
                    await publish('answer', tail, True)
                    live_answer_closed = True
                check_context(context)
            finally:
                close = getattr(stream, 'aclose', None)
                if close:
                    import sys
                    if isinstance(sys.exc_info()[1], (CallCancelled, asyncio.CancelledError)):
                        try:await asyncio.wait_for(close(),2)
                        except (Exception,asyncio.CancelledError):pass
                    else:
                        await close()
        except BaseException as exc:
            failure = exc
            if not isinstance(exc, (CallCancelled, asyncio.CancelledError)) and (not request.is_fallback or answer_done):
                try:
                    await flush_decoders()
                except Exception:
                    setattr(exc, 'cleanup_error_code', 'FAILED_STREAM_FLUSH_UNAVAILABLE')
        if isinstance(failure, (CallCancelled, asyncio.CancelledError)):
            async def close_cancelled_public_stream():
                nonlocal thinking_closed, live_answer_closed
                answer_tail = answer_decoder.flush()
                thinking_tail = thinking_decoder.flush()
                answer.append(answer_tail)
                if thinking_tail:
                    thinking.append(thinking_tail)
                    await publish('thinking', thinking_tail)
                await close_thinking()
                if not live_answer_closed:
                    await publish('answer', answer_tail, True)
                    live_answer_closed = True
                # DYNAMIC Cheap has never been public; do not expose its draft or citations.
                if publish_live:
                    references = self._references(request, registry)
                    if references:
                        await self.store.append_event(request.session_id, request.message_id,
                            StreamEvent(attempt_id + '-cancel-references', 'references',
                                data={'references': list(references)}))
            cleanup = asyncio.create_task(close_cancelled_public_stream())
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), 5)
            except BaseException:
                cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
                # Preserve cancellation identity; persistence reads only acknowledged events.
                setattr(failure, 'cleanup_error_code', 'CANCELLED_STREAM_FLUSH_UNAVAILABLE')
        text = ''.join(answer)
        thought = ''.join(thinking)
        objectives = tuple((name for name, present in (('TRUNCATED', is_length(finish)), ('EMPTY', not text.strip()), ('INVALID_HANDLES', registry.invalid_count > 0)) if present))
        if failure is not None:
            code = failure.code if isinstance(failure, ProviderError) else 'GENERATION_CANCELLED' if isinstance(failure, asyncio.CancelledError) else 'PROVIDER_STREAM_FAILED'
            status = 'CANCELLED' if isinstance(failure, (CallCancelled, asyncio.CancelledError)) else 'FAILED'
            record = replace(record, status=status, model_reported=reported, finish_reason=finish, usage=usage, objective=objectives, error_code=code, invalid_handle_count=registry.invalid_count)
        else:
            status = 'TRUNCATED' if is_length(finish) else 'COMPLETED'
            record = replace(record, status=status, model_reported=reported, finish_reason=finish, usage=usage, objective=objectives, invalid_handle_count=registry.invalid_count)
        try:
            await self.lifecycle.finish_attempt(record, attempt_context)
        except Exception:
            raise GenerationError('GENERATION_ATTEMPT_FINALIZE_FAILED', sent=True) from None
        await self._progress('generation', record.status.lower(), attempt_id=attempt_id, role=role)
        if failure is not None:
            partial=(text,thought,(),tuple(error_events)) if request.is_fallback and fallback_output_failure(failure) else None
            return (record, partial, failure)
        if is_length(finish) and (not text.strip()):
            text = EMPTY_TRUNCATED_FALLBACK
        references = self._references(request, registry)
        return (record, (text, thought, references, tuple(error_events)), None)

    @staticmethod
    def _references(request, registry):
        snapshot = registry.snapshot()
        used = set(registry.used_handles)
        chunk_ids = {ref[0] for handle, ref in snapshot.chunks if handle in used}
        urls = {url for handle, url, title in snapshot.webs if handle in used}
        references=[]
        for e in request.evidence:
            if e.id not in chunk_ids and e.id not in urls:continue
            row={'id':e.id,'content':e.source_content or e.content,'score':e.score,
                 'knowledge_id':e.knowledge_id,'knowledge_base_id':e.knowledge_base_id,
                 'knowledge_title':e.knowledge_title,'knowledge_filename':e.knowledge_filename,
                 'chunk_index':e.chunk_index,'chunk_type':e.chunk_type,'knowledge_source':e.knowledge_source,
                 'start_at':e.start_at,'end_at':e.end_at,'seq':e.seq,'match_type':e.match_type,
                 'sub_chunk_id':list(e.sub_chunk_ids) if e.sub_chunk_ids is not None else None,
                 'metadata':e.metadata,'parent_chunk_id':e.parent_chunk_id,'image_info':e.image_info,
                 'knowledge_channel':e.knowledge_channel}
            # Match upstream SearchResult JSON omitempty, without losing populated metadata.
            for name in ('chunk_metadata','matched_content','knowledge_description','knowledge_custom_metadata'):
                value=getattr(e,name)
                if value:row[name]=value
            references.append(row)
        return tuple(references)

    async def _fixed_fallback(self,request,context,*,attempts=(),route=None,partial='',buffered=False):
        from ..references.registry import SourceRegistry
        text=request.fixed_fallback if request.fixed_fallback is not None else request.fallback_response
        if not request.citations_enabled:text=SourceRegistry(False).decode_output_text(text)
        if not attempts:
            await self.lifecycle.claim_run(request.run_id,digest({'run':request.run_id,'fixed':text}),context)
        check_context(context)
        if partial and buffered:
            await self.store.append_event(request.session_id,request.message_id,
                StreamEvent(request.run_id+'-fallback-partial','answer',partial,False,data={'is_fallback':True}))
        await self.store.append_event(request.session_id,request.message_id,
            StreamEvent(request.run_id+'-fallback','answer',text,True,data={'is_fallback':True}))
        text=partial+text  # qa.go completion handler appends every source answer chunk.
        check_context(context)
        coordinator=self
        class CheckedCommitter:
            async def commit_final(self,commit):
                check_context(context)
                await coordinator.committer.commit_final(commit)
                check_context(context)
        # Fixed output causes no new provider attempt; retain existing attempt usage.
        usage=accumulate_usage([a.usage for a in attempts]) if attempts else TokenUsage()
        await finalize_turn(self.store,CheckedCommitter(),FinalCommit(request.session_id,request.message_id,
            request.run_id,text,[],usage_dict(usage)),complete_data={'is_fallback':True,'attempts':[public_attempt(a) for a in attempts],'router':route})
        return GenerationResult(text,'',(),'fixed','',tuple(attempts),route,usage,'',False,len(attempts)>1,'','')

    async def run(self, request, context):
        check_context(context)
        if context.request_id != request.run_id or (context.session_id and context.session_id != request.session_id):
            raise GenerationError('GENERATION_RUN_IDENTITY_MISMATCH')
        if not context.scope.owner_id:
            raise GenerationError('GENERATION_SCOPE_REQUIRED')
        if any((e.knowledge_base_id not in context.scope.knowledge_base_ids or (context.scope.document_ids and e.knowledge_id not in context.scope.document_ids) for e in request.evidence if e.knowledge_source.lower() != 'web_search' and e.chunk_type.lower() != 'web_search')):
            raise GenerationError('GENERATION_EVIDENCE_SCOPE_MISMATCH')
        if request.fixed_fallback is not None:
            return await self._fixed_fallback(request,context)
        prepared = prepare_generation(request)
        if request.on_context_prepared is not None:
            request.on_context_prepared(prepared)
        route = None
        role = 'baseline'
        if request.policy.mode != 'OFF':
            estimate = self.estimator.estimate(prepared.messages, request.options) if self.estimator else TokenEstimate()
            if not isinstance(estimate, TokenEstimate):
                raise GenerationError('TOKEN_ESTIMATE_INVALID')
            query = request.understanding.rewrite_query if request.understanding else request.q0
            decision = decide(query, prepared.full_prompt, request.policy, estimated_tokens=estimate.tokens, estimator=estimate.estimator, understanding=request.understanding)
            route = decision.public(request.policy)
            role = decision.role
        payload = digest({'request': request.run_id, 'session': request.session_id, 'message': request.message_id, 'q0': request.q0, 'prompt': prepared.prompt_sha256, 'context': prepared.context_sha256, 'options': asdict(request.options), 'mode': request.policy.mode, 'owner': context.scope.owner_id, 'knowledge_bases': context.scope.knowledge_base_ids, 'documents': context.scope.document_ids})
        await self.lifecycle.claim_run(request.run_id, payload, context)
        attempts = []
        reason = None
        selected = None
        for ordinal in (1, 2):
            check_context(context)
            record, candidate, error = await self._attempt(request, prepared, context, role, ordinal, reason)
            attempts.append(record)
            if isinstance(error, (CallCancelled, asyncio.CancelledError)):
                raise error
            check_context(context)
            if request.is_fallback and fallback_output_failure(error):
                return await self._fixed_fallback(request,context,attempts=tuple(attempts),route=route,
                    partial=candidate[0] if candidate else '',buffered=role=='cheap' and request.policy.mode=='DYNAMIC')
            upgrade = ordinal == 1 and role == 'cheap' and (request.policy.mode == 'DYNAMIC') and error is None and bool(record.objective)
            if upgrade:
                reason = 'TRUNCATION' if 'TRUNCATED' in record.objective else 'MODEL_EMPTY' if 'EMPTY' in record.objective else 'INVALID_HANDLES'
                role = 'expensive'
                continue
            if error is not None:
                if isinstance(error, asyncio.CancelledError):
                    raise error
                if isinstance(error, ProviderError):
                    raise error
                raise GenerationError('PROVIDER_STREAM_FAILED', sent=True) from None
            selected = candidate
            break
        if selected is None:
            raise GenerationError('GENERATION_NO_SELECTED_ATTEMPT')
        answer, thinking, references, error_events = selected
        total_usage = accumulate_usage([record.usage for record in attempts])
        result = GenerationResult(answer, thinking, references, record.role, record.attempt_id, tuple(attempts), route, total_usage, record.finish_reason, is_length(record.finish_reason), len(attempts) > 1, prepared.prompt_sha256, prepared.context_sha256)
        check_context(context)
        metadata = {'is_fallback':request.is_fallback, 'attempt_id': record.attempt_id, 'role': record.role, 'finish_reason': record.finish_reason, 'truncated': result.truncated, 'upgraded': result.upgraded, 'model_requested': record.model_requested, 'model_reported': record.model_reported, 'usage_status': record.usage.status, 'invalid_handle_count': record.invalid_handle_count}
        metadata['generation_issues'] = list(record.objective)
        buffered = record.role == 'cheap' and request.policy.mode == 'DYNAMIC'
        if buffered:
            for notice in error_events:
                await self.store.append_event(request.session_id, request.message_id, notice)
        if thinking and buffered:
            await self.store.append_event(request.session_id, request.message_id, StreamEvent(record.attempt_id + '-thinking', 'thinking', thinking, True, data=metadata))
        if references:
            await self.store.append_event(request.session_id, request.message_id, StreamEvent(request.run_id, 'references', data={'references': list(references)}))
        if buffered:
            await self.store.append_event(request.session_id, request.message_id, StreamEvent(record.attempt_id + '-answer', 'answer', answer, True, data=metadata, usage=usage_dict(total_usage)))
        check_context(context)
        coordinator=self
        class CheckedCommitter:
            async def commit_final(self,commit):
                check_context(context)
                try:
                    await coordinator.committer.commit_final(commit)
                except (CallCancelled,RunDeadlineExceeded,asyncio.CancelledError):
                    raise
                except Exception:
                    raise GenerationError('GENERATION_FINAL_COMMIT_FAILED',sent=True) from None
                check_context(context)
        await finalize_turn(self.store, CheckedCommitter(), FinalCommit(request.session_id, request.message_id, request.run_id, answer, list(references), usage_dict(total_usage)), complete_data={**metadata, 'attempts': [public_attempt(a) for a in attempts], 'router': route})
        return result
