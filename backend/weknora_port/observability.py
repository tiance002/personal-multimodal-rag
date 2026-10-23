"""Opt-in Langfuse observations for the formal chain; never authorizes model sends.

SDK 4.15.6 manual observation handles provide explicit ancestry even when a
stream settles in another task. Only this module's named observations export.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
_CURRENT = ContextVar('weknora_trace', default=None)
_STAGES = frozenset(('history', 'memory', 'understand', 'retrieval', 'rerank', 'merge',
    'source_projection', 'context', 'graph', 'graph_fusion', 'bridge', 'generation', 'selection'))
_META = frozenset(('component', 'run_id', 'client_request_sha256', 'session_sha256',
    'request_id', 'request_sha256', 'wire_sha256', 'provider', 'model', 'model_id', 'capability',
    'prompt_tokens', 'completion_tokens', 'total_tokens',
    'status', 'error_code', 'outcome', 'usage_status', 'settlement_success', 'settlement_error',
    'stage', 'role', 'attempt_id', 'candidate_count', 'selected_evidence_count', 'path_count',
    'added_chunks', 'history_origin_dropped', 'kb_count', 'document_count', 'router_mode',
    'retrieval_strategy_mode', 'source_evidence_mode', 'enable_rewrite', 'enable_rerank',
    'enable_expansion', 'rerank_top1_floorless', 'rerank_context_enabled', 'bridge_rescue_mode',
    'chat_model_id', 'query_understand_model_id', 'rerank_model_id', 'vlm_model_id',
    'selected_role', 'attempt_count', 'finish_reason', 'truncated', 'upgraded',
    'citation_status', 'citation_reasons', 'citation_count', 'valid_handle_count', 'invalid_handle_count', 'usable_evidence_count',
    'assessment_complete', 'semantic_support', 'cost_status', 'route_reason', 'decision_model_role',
    'router_version', 'threshold', 'score', 'mode'))


def digest(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'key|secret|authorization|password|image|audio', str(k), re.I)
                else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value if value is None or type(value) in (bool, int, float) else '[REDACTED]'
    value = re.sub(r'data:image/[^\s"\)]+|!\[[^\]]*\]\([^)]*\)', '[REDACTED IMAGE]', value, flags=re.I)
    value = re.sub(r'https?://[^\s"\)]+', '[REDACTED URL]', value, flags=re.I)
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[REDACTED EMAIL]', value)
    value = re.sub(r'(?i)(?:bearer|basic)\s+[A-Za-z0-9+/=_:.\-]+', '[REDACTED AUTH]', value)
    value = re.sub(r'''(?i)(?:api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password|authorization)["']?\s*[=:]\s*["']?[^"'\s,;}]+''', '[REDACTED CREDENTIAL]', value)
    value = re.sub(r'\b(?:sk|pk)[-_][A-Za-z0-9_-]+', '[REDACTED KEY]', value, flags=re.I)
    return re.sub(r'[A-Za-z0-9+/=]{128,}', '[REDACTED BINARY]', value)


@dataclass(frozen=True)
class ObservabilitySettings:
    enabled: bool = False
    egress_enabled: bool = False
    capture_content: bool = False
    base_url: str = 'https://us.cloud.langfuse.com'
    public_key: str = field(default='', repr=False)
    secret_key: str = field(default='', repr=False)
    environment: str = 'default'
    release: str = 'unversioned'

    @classmethod
    def from_environment(cls, env):
        enabled = env.get('RAG_LANGFUSE_ENABLED', 'false').lower() == 'true'
        egress = env.get('RAG_LANGFUSE_EGRESS_ENABLED', 'false').lower() == 'true'
        capture = env.get('RAG_LANGFUSE_CAPTURE_CONTENT', 'false').lower() == 'true'
        def read(name):
            value = env.get(name, '').strip()
            if not value and env.get(name + '_FILE'):
                try: value = Path(env[name + '_FILE']).read_text('utf-8').strip()
                except (OSError, UnicodeError): log.warning('LANGFUSE_CREDENTIAL_FILE_UNAVAILABLE')
            return value
        public = secret = ''
        if enabled and egress:
            public, secret = read('LANGFUSE_PUBLIC_KEY'), read('LANGFUSE_SECRET_KEY')
            if not (public and secret):
                try:
                    scheme, encoded = read('LANGFUSE_AUTHORIZATION').split(None, 1)
                    if scheme.lower() == 'basic':
                        public, secret = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
                except (ValueError, UnicodeError, binascii.Error): pass
        return cls(enabled, egress, capture, env.get('LANGFUSE_BASE_URL') or env.get('LANGFUSE_HOST') or cls.base_url,
                   public, secret, env.get('LANGFUSE_TRACING_ENVIRONMENT') or 'default',
                   env.get('LANGFUSE_RELEASE') or 'unversioned')


@dataclass
class TraceRun:
    observer: object
    span: object
    trace_id: str
    run_id: str
    token: object = None
    propagation: object = None
    stages: dict = field(default_factory=dict)
    closed: bool = False


class LangfuseObservability:
    def __init__(self, settings=None, *, client_factory=None):
        self.settings = settings or ObservabilitySettings()
        self.client_factory = client_factory
        self._client = None
        self._calls = {}
        self._errors = {}
        self._turns = {}
        self._closed = False
        self._warned = set()

    @property
    def enabled(self):
        return self.settings.enabled and self.settings.egress_enabled and not self._closed

    def _warn(self, code):
        if code not in self._warned:
            self._warned.add(code)
            log.warning('LANGFUSE_%s', code)  # No exception text, URL, or credentials.

    def _safe(self, action, operation):
        try: return operation()
        except Exception:
            self._warn(action + '_FAILED')
            return None

    def _metadata(self, values):
        return {k: self._content(v) for k, v in values.items() if k in _META
                and (v is None or type(v) in (str, bool, int, float) or
                     isinstance(v, (tuple, list)) and all(type(item) is str for item in v))}

    def _content(self, value):
        value = redact(value)
        if isinstance(value, dict): return {key: self._content(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)): return [self._content(item) for item in value]
        if isinstance(value, str):
            for secret in (self.settings.secret_key, self.settings.public_key):
                if secret: value = value.replace(secret, '[REDACTED KEY]')
        return value

    def _mask_spans(self, *, params):
        from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
        patches = {}
        for identifier, span in params.spans.items():
            deletes, replacements = [], {}
            for key, value in span.attributes.items():
                content = key in ('langfuse.observation.input', 'langfuse.observation.output',
                    'langfuse.trace.input', 'langfuse.trace.output', 'input.value', 'output.value',
                    'ai.prompt', 'ai.prompt.messages', 'ai.response.text') or key.startswith((
                    'gen_ai.input.messages', 'gen_ai.output.messages', 'llm.input_messages', 'llm.output_messages'))
                if content and not self.settings.capture_content:
                    deletes.append(key)
                if isinstance(value, str):
                    masked = self._content(value)
                    if masked != value: replacements[key] = masked
                elif isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
                    masked = [self._content(item) for item in value]
                    if list(value) != masked: replacements[key] = masked
            if deletes or replacements:
                patches[identifier] = OtelSpanPatch(delete_attributes=tuple(deletes), set_attributes=replacements)
        return MaskOtelSpansResult(span_patches=patches) if patches else None

    def _get_client(self):
        if not self.enabled: return None
        if self._client is not None: return self._client
        if not (self.settings.public_key and self.settings.secret_key):
            self._warn('CREDENTIALS_UNAVAILABLE')
            return None
        parsed = urlsplit(self.settings.base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            self._warn('BASE_URL_INVALID')
            return None
        def create():
            factory = self.client_factory
            if factory is None:
                from langfuse import Langfuse
                factory = Langfuse
            return factory(public_key=self.settings.public_key, secret_key=self.settings.secret_key,
                base_url=self.settings.base_url, mask=lambda data: self._content(data), mask_otel_spans=self._mask_spans,
                environment=self.settings.environment, release=self.settings.release,
                tracing_enabled=True, sample_rate=1.0,
                should_export_span=lambda span: span.name.startswith('weknora.'))
        self._client = self._safe('CLIENT_INIT', create)
        return self._client

    def _start(self, name, trace_id, parent=None, *, as_type='span', metadata=None, **fields):
        client = self._get_client()
        if client is None: return None
        trace_context = {'trace_id': trace_id}
        if parent: trace_context['parent_span_id'] = parent
        return self._safe('START', lambda: client.start_observation(name='weknora.' + name,
            trace_context=trace_context, as_type=as_type, metadata=self._metadata({'component': 'weknora_port', **(metadata or {})}), **fields))

    def begin_turn(self, turn, *, cloud_consented):
        if not self.enabled or not cloud_consented or turn.replay_only or turn.run_id in self._turns: return None
        trace_id = digest('weknora-turn:' + turn.run_id)[:32]
        def correlate():
            from langfuse import propagate_attributes
            manager = propagate_attributes(session_id=digest(turn.session_id))
            manager.__enter__()
            return manager
        propagation = self._safe('CORRELATION', correlate)
        fields = {'input': self._content(turn.request.content)} if self.settings.capture_content else {}
        span = self._start('ordinary-turn', trace_id, metadata={'run_id': turn.run_id,
            'client_request_sha256': digest(turn.client_request_id or turn.request.request_id),
            'session_sha256': digest(turn.session_id), 'kb_count': len(turn.context.scope.knowledge_base_ids),
            'document_count': len(turn.context.scope.document_ids)}, **fields)
        if span is None:
            if propagation is not None: self._safe('CORRELATION_END', lambda: propagation.__exit__(None, None, None))
            return None
        run = TraceRun(self, span, trace_id, turn.run_id, propagation=propagation)
        run.token = _CURRENT.set(run)
        self._turns[turn.run_id] = run
        log.info('LANGFUSE_TURN_STARTED trace_id=%s run_id=%s', trace_id, turn.run_id)
        return run

    def annotate(self, **metadata):
        run = _CURRENT.get()
        if run is not None and run.observer is self and not run.closed:
            self._safe('UPDATE', lambda: run.span.update(metadata=self._metadata(metadata)))

    def stage(self, run_id, stage, status, fields):
        run = _CURRENT.get()
        if run is None or run.observer is not self or run.closed or run.run_id != run_id or stage not in _STAGES: return
        key = (stage, fields.get('attempt_id', ''), fields.get('role', ''))
        span = run.stages.get(key)
        if span is None:
            span = self._start(stage, run.trace_id, run.span.id, metadata={'stage': stage, **fields})
            if span is None: return
            run.stages[key] = span
        self._safe('STAGE_UPDATE', lambda: span.update(metadata=self._metadata({'stage': stage, 'status': status, **fields})))
        if status != 'started':
            run.stages.pop(key, None)
            self._safe('STAGE_END', span.end)

    @staticmethod
    def _call_key(call):
        return (call.context.scope.owner_id, call.context.request_id, call.capability, call.wire_sha256)

    def provider_started(self, call, *, cloud_consented):
        if not self.enabled or not cloud_consented: return
        key = self._call_key(call)
        if key in self._calls: return
        run = _CURRENT.get()
        if run is not None and (run.observer is not self or run.closed): run = None
        trace_id = run.trace_id if run else digest(repr(key))[:32]
        parent = (next(reversed(run.stages.values())).id if run.stages else run.span.id) if run else None
        stage_key = next(reversed(run.stages)) if run and run.stages else None
        metadata = {'provider': call.provider, 'model': call.model, 'model_id': call.model_id,
            'capability': call.capability, 'request_id': call.context.request_id,
            'request_sha256': digest(call.context.request_id), 'wire_sha256': call.wire_sha256,
            'stage': stage_key[0] if stage_key else 'background' if run is None else 'preparation',
            'role': (stage_key[2] or stage_key[0]) if stage_key else call.capability,
            'cost_status': 'UNPRICED'}
        background = None
        if run is None:
            background = self._start('background-provider', trace_id, metadata=metadata)
            if background is None: return
            parent = background.id
        # SDK 4.15.6 only exports native model/usage for generation-like types.
        kind = 'embedding' if call.capability == 'embedding' else 'generation' if call.capability in ('chat', 'vision', 'rerank') else 'span'
        span = self._start('provider-' + call.capability, trace_id, parent, as_type=kind,
                           metadata=metadata, model=self._content(call.model))
        if span is None:
            if background is not None: self._safe('BACKGROUND_END', background.end)
            return
        self._calls[key] = (span, background, trace_id, run.run_id if run else None)
        log.info('LANGFUSE_PROVIDER_STARTED trace_id=%s run_id=%s request_sha256=%s',
                 trace_id, run.run_id if run else 'background', digest(call.context.request_id))

    def provider_error(self, call, error):
        from .models.types import ProviderError
        key = self._call_key(call)
        if key not in self._calls: return
        if isinstance(error, ProviderError):
            code = error.code if re.fullmatch(r'[A-Z0-9_]{1,96}', error.code) else 'PROVIDER_ERROR'
        elif isinstance(error, asyncio.CancelledError): code = 'CANCELLED'
        elif isinstance(error, TimeoutError): code = 'TIMEOUT'
        else: code = 'UNEXPECTED_ERROR'
        self._errors[key] = code

    def provider_finished(self, call, *, outcome, usage, settlement_success, settlement_error=None):
        pair = self._calls.pop(self._call_key(call), None)
        if pair is None: return
        span, background, trace_id, run_id = pair
        metadata = self._metadata({'outcome': outcome, 'usage_status': getattr(usage, 'status', 'UNKNOWN'),
            'settlement_success': settlement_success, 'settlement_error': settlement_error,
            'error_code': self._errors.pop(self._call_key(call), None), 'cost_status': 'UNPRICED',
            **{name: getattr(usage, name, None) for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')}})
        details = {label: value for label, value in (
            ('input', getattr(usage, 'prompt_tokens', None)), ('output', getattr(usage, 'completion_tokens', None)),
            ('total', getattr(usage, 'total_tokens', None))) if type(value) is int and value >= 0}
        if getattr(usage, 'cache_reported', False):
            details.update({label: getattr(usage, name) for label, name in (
                ('cache_read_input_tokens', 'cache_read_tokens'), ('cache_creation_input_tokens', 'cache_write_tokens'))
                if type(getattr(usage, name, None)) is int and getattr(usage, name) >= 0})
        fields = {'metadata': metadata}
        if details: fields['usage_details'] = details
        try:
            self._safe('PROVIDER_UPDATE', lambda: span.update(**fields))
        finally:
            self._safe('PROVIDER_END', span.end)
            if background is not None: self._safe('BACKGROUND_END', background.end)
            log.info('LANGFUSE_PROVIDER_FINISHED trace_id=%s run_id=%s request_sha256=%s settlement_success=%s',
                     trace_id, run_id or 'background', digest(call.context.request_id), settlement_success)

    def finish_turn(self, run, *, result=None, status='UNKNOWN', error_code=None):
        if run is None or run.closed: return
        run.closed = True
        self._turns.pop(run.run_id, None)
        def update():
            metadata = {'status': status, 'error_code': error_code, 'cost_status': 'UNPRICED'}
            if result is not None:
                metadata.update(selected_role=result.selected_role, attempt_count=len(result.attempts),
                    finish_reason=result.finish_reason, truncated=result.truncated, upgraded=result.upgraded,
                    citation_count=len(result.references))
                for item in result.attempts:
                    attempt = self._start('attempt-result', run.trace_id, run.span.id, metadata={
                        'attempt_id': item.attempt_id, 'role': item.role, 'provider': item.provider,
                        'model': item.model_requested, 'status': item.status, 'finish_reason': item.finish_reason,
                        'usage_status': item.usage.status, 'cost_status': 'UNPRICED',
                        'citation_status': item.citation_quality.status,
                        'valid_handle_count': item.citation_quality.valid_handle_count,
                        'invalid_handle_count': item.citation_quality.invalid_handle_count,
                        'usable_evidence_count': item.citation_quality.usable_evidence_count,
                        'assessment_complete': item.citation_quality.assessment_complete,
                        'semantic_support': item.citation_quality.semantic_support})
                    if attempt is not None: self._safe('ATTEMPT_END', attempt.end)
                if isinstance(result.route, dict):
                    metadata.update({key: value for key, value in result.route.items()
                        if key in ('route_reason', 'decision_model_role', 'router_version', 'threshold', 'score', 'mode')})
                selected = next((item for item in result.attempts if item.attempt_id == result.selected_attempt_id), None)
                quality = selected.citation_quality if selected is not None else getattr(result, 'citation_quality', None)
                if quality is not None:
                    metadata.update(citation_status=quality.status, citation_reasons=getattr(quality, 'reasons', ()),
                        valid_handle_count=quality.valid_handle_count, invalid_handle_count=quality.invalid_handle_count,
                        usable_evidence_count=quality.usable_evidence_count,
                        assessment_complete=quality.assessment_complete, semantic_support=quality.semantic_support)
                quality_span = self._start('quality-check', run.trace_id, run.span.id, metadata=metadata)
                if quality_span is not None: self._safe('QUALITY_END', quality_span.end)
            fields = {'metadata': self._metadata(metadata)}
            if self.settings.capture_content and result is not None and status == 'completed':
                fields['output'] = self._content(result.answer)
            run.span.update(**fields)
        try:
            self._safe('ROOT_UPDATE', update)
        finally:
            try:
                for span in tuple(run.stages.values()): self._safe('STAGE_END', span.end)
                run.stages.clear()
                self._safe('ROOT_END', run.span.end)
            finally:
                if run.propagation is not None:
                    self._safe('CORRELATION_END', lambda: run.propagation.__exit__(None, None, None))
                try: _CURRENT.reset(run.token)
                except ValueError: self._warn('CONTEXT_RESET_FAILED')
                log.info('LANGFUSE_TURN_FINISHED trace_id=%s run_id=%s status=%s', run.trace_id, run.run_id, status)

    async def shutdown(self):
        if self._closed: return
        self._closed = True
        for run in tuple(self._turns.values()):
            self.finish_turn(run, status='UNKNOWN', error_code='RUNTIME_SHUTDOWN')
        for span, background, _, _ in tuple(self._calls.values()):
            self._safe('PROVIDER_END', span.end)
            if background is not None: self._safe('BACKGROUND_END', background.end)
        self._calls.clear()
        self._errors.clear()
        if self._client is not None:
            # SDK export/flush is blocking; shutdown never stalls the event loop.
            for name in ('flush', 'shutdown'):
                method = getattr(self._client, name, None)
                if callable(method):
                    try: await asyncio.to_thread(self._safe, name.upper(), method)
                    except Exception: self._warn(name.upper() + '_DISPATCH_FAILED')
