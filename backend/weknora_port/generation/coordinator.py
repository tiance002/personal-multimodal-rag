"""At most two uniform provider streams; selected candidate only reaches P6."""
from __future__ import annotations
import asyncio, hashlib, json, re
from dataclasses import asdict, replace
from ..models.types import CallCancelled, ModelCallTimeout, ProviderError, RunDeadlineExceeded, TokenUsage
from ..models.usage import accumulate_usage
from ..references.decoder import CitationStreamDecoder
from ..streaming.events import StreamEvent
from ..streaming.replay import FinalCommit, finalize_turn
from .context import prepare_generation
from .router import decide
from .types import GenerationError, GenerationResult, AttemptRecord, ClosedLifecycle, TokenEstimate
EMPTY_TRUNCATED_FALLBACK = "Sorry, this answer hit the model's per-response output limit before any text was produced. Try narrowing the question, or raise max_completion_tokens."

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
        final_seen = False
        failure = None
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
                raise GenerationError('PROVIDER_STREAM_UNAVAILABLE', sent=False)
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
                        raise GenerationError(code if isinstance(code, str) else 'PROVIDER_STREAM_FAILED', sent=True)
                    if event.response_type == 'thinking':
                        thinking.append(thinking_decoder.feed(event.content))
                    elif event.response_type == 'answer' and (not answer_done):
                        answer.append(answer_decoder.feed(event.content))
                        if event.done:
                            answer.append(answer_decoder.flush())
                            answer_done = True
                    if event.is_final:
                        final_seen = True
                answer.append(answer_decoder.flush())
                thinking.append(thinking_decoder.flush())
                check_context(context)
            finally:
                close = getattr(stream, 'aclose', None)
                if close:
                    await close()
        except BaseException as exc:
            failure = exc
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
            return (record, None, failure)
        if is_length(finish) and (not text.strip()):
            text = EMPTY_TRUNCATED_FALLBACK
        snapshot = registry.snapshot()
        used = set(registry.used_handles)
        chunk_ids = {ref[0] for handle, ref in snapshot.chunks if handle in used}
        urls = {url for handle, url, title in snapshot.webs if handle in used}
        references = tuple(({'id': e.id, 'content': e.source_content or e.content, 'score': e.score, 'knowledge_id': e.knowledge_id, 'knowledge_base_id': e.knowledge_base_id, 'knowledge_title': e.knowledge_title, 'knowledge_filename': e.knowledge_filename, 'chunk_index': e.chunk_index, 'chunk_type': e.chunk_type, 'knowledge_source': e.knowledge_source} for e in request.evidence if e.id in chunk_ids or e.id in urls))
        return (record, (text, thought, references), None)

    async def run(self, request, context):
        check_context(context)
        if context.request_id != request.run_id or (context.session_id and context.session_id != request.session_id):
            raise GenerationError('GENERATION_RUN_IDENTITY_MISMATCH')
        if not context.scope.owner_id:
            raise GenerationError('GENERATION_SCOPE_REQUIRED')
        if any((e.knowledge_base_id not in context.scope.knowledge_base_ids or (context.scope.document_ids and e.knowledge_id not in context.scope.document_ids) for e in request.evidence if e.knowledge_source.lower() != 'web_search' and e.chunk_type.lower() != 'web_search')):
            raise GenerationError('GENERATION_EVIDENCE_SCOPE_MISMATCH')
        prepared = prepare_generation(request)
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
            check_context(context)
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
        answer, thinking, references = selected
        total_usage = accumulate_usage([record.usage for record in attempts])
        result = GenerationResult(answer, thinking, references, record.role, record.attempt_id, tuple(attempts), route, total_usage, record.finish_reason, is_length(record.finish_reason), len(attempts) > 1, prepared.prompt_sha256, prepared.context_sha256)
        check_context(context)
        metadata = {'attempt_id': record.attempt_id, 'role': record.role, 'finish_reason': record.finish_reason, 'truncated': result.truncated, 'upgraded': result.upgraded, 'model_requested': record.model_requested, 'model_reported': record.model_reported, 'usage_status': record.usage.status, 'invalid_handle_count': record.invalid_handle_count}
        if thinking:
            await self.store.append_event(request.session_id, request.message_id, StreamEvent(record.attempt_id + '-thinking', 'thinking', thinking, True, data=metadata))
        if references:
            await self.store.append_event(request.session_id, request.message_id, StreamEvent(request.run_id, 'references', data={'references': list(references)}))
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
