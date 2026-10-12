"""Opt-in local usage capture, scoped to the current execution context.

No credentials, prompt text, output text, or network exporters are retained.
The regular adapter return contracts remain unchanged. A failed/retried call
with unknown server consumption keeps the total unavailable, not guessed zero.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class ModelCall:
    stage: str
    role: str
    model: str
    status: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None = None
    provider: str | None = None
    model_key: str | None = None
    capability: str | None = None
    settlement: str | None = None


@dataclass
class UsageCapture:
    role: str = 'business'
    calls: list[ModelCall] = field(default_factory=list)
    provider_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {'calls': [asdict(call) for call in self.calls], 'summary': self.summary()}

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for stage in ('query', 'answer', 'embedding', 'context_compaction', 'memory_extraction', 'query_understand'):
            calls = [call for call in self.calls if call.stage == stage]
            row = {'call_count': len(calls), 'error_count': sum(call.status != 'ok' for call in calls),
                   'availability': 'not_run' if not calls else 'actual',
                   'input_tokens': None, 'output_tokens': None,
                   'known_input_tokens': sum(call.input_tokens or 0 for call in calls),
                   'known_output_tokens': sum(call.output_tokens or 0 for call in calls)}
            for attribute in ('input_tokens', 'output_tokens'):
                values = [getattr(call, attribute) for call in calls]
                if values and all(value is not None for value in values):
                    row[attribute] = sum(values)
                elif calls:
                    row['availability'] = 'unavailable'
            result[stage] = row
        chat_calls = [call for call in self.calls if call.stage in ('query', 'answer', 'context_compaction', 'memory_extraction', 'query_understand')]
        totals = [None if call.input_tokens is None or call.output_tokens is None
                  else call.input_tokens+call.output_tokens for call in chat_calls]
        total = sum(totals) if totals and all(value is not None for value in totals) else None
        result['business_total_tokens'] = total if self.role == 'business' else None
        result['judge_total_tokens'] = total if self.role == 'judge' else None
        return result


_CAPTURE: ContextVar[UsageCapture | None] = ContextVar('local_model_usage_capture', default=None)
_STAGE: ContextVar[str] = ContextVar('local_model_usage_stage', default='unknown')


@contextmanager
def capture_usage(*, role: str = 'business') -> Iterator[UsageCapture]:
    if role not in ('business', 'judge'):
        raise ValueError('invalid model consumption role')
    capture = UsageCapture(role=role)
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


@contextmanager
def call_stage(stage: str) -> Iterator[None]:
    if stage not in ('query', 'answer', 'embedding', 'context_compaction', 'memory_extraction', 'query_understand'):
        raise ValueError('invalid model call stage')
    token = _STAGE.set(stage)
    try:
        yield
    finally:
        _STAGE.reset(token)


def record_call(*, model: str, status: str, response: dict[str, Any] | None, latency_ms: float) -> None:
    capture = _CAPTURE.get()
    if capture is None:
        return
    def count(name: str) -> int | None:
        value = response.get(name) if response is not None and status == 'ok' else None
        return value if type(value) is int and value >= 0 else None
    capture.calls.append(ModelCall(stage=_STAGE.get(), role=capture.role, model=model,
                                  status=status, latency_ms=latency_ms,
                                  input_tokens=count('prompt_eval_count'), output_tokens=count('eval_count'),
                                  finish_reason=response.get('done_reason') if response is not None else None))


def record_provider_call(*, model_key: str, capability: str, provider: str, model: str,
                         status: str, usage: dict | None, latency_ms: float,
                         stage: str | None = None, diagnostic_stage: str | None = None,
                         error_code: str | None = None) -> None:
    """Capture supplier-returned tokens separately from monetary settlement."""
    if stage is not None and (type(stage) is not str or stage not in (
            'query', 'answer', 'embedding', 'context_compaction', 'memory_extraction', 'query_understand')):
        raise ValueError('invalid model call stage')
    capture = _CAPTURE.get()
    if capture is None:
        return
    usage = usage or {}
    if stage is None:
        stage = 'answer' if capability.startswith('chat_') else capability
    capture.calls.append(ModelCall(stage, capture.role, model, status, latency_ms,
        usage.get('prompt_tokens'), usage.get('completion_tokens'), provider=provider,
        model_key=model_key, capability=capability, settlement='UNKNOWN'))
    if diagnostic_stage is not None:
        stages={'admission','transport','http_response','response_decode','response_validation',
                'usage_parse','usage_settlement','rerank_validation','complete'}
        reasons={'NONE','DNS_FAILED','TLS_CERTIFICATE_FAILED','TLS_HANDSHAKE_FAILED','REQUEST_TIMEOUT',
                 'NETWORK_CONNECTION_FAILED','TRANSPORT_FAILED','HTTP_401','HTTP_403','HTTP_404','HTTP_429',
                 'HTTP_5XX','HTTP_OTHER','JSON_RESPONSE_INVALID','RESPONSE_STRUCTURE_INVALID',
                 'MODEL_IDENTITY_MISMATCH','REQUEST_NOT_SENT','RESPONSE_TRUNCATED','PROVIDER_UNAVAILABLE',
                 'RESPONSE_VALIDATION_FAILED','USAGE_PARSE_FAILED','USAGE_SETTLEMENT_FAILED','RESPONSE_TOO_LARGE'}
        capture.provider_diagnostics.append(dict(schema_version='provider-diagnostic/v1',
            call_index=len(capture.calls)-1,
            stage=diagnostic_stage if diagnostic_stage in stages else 'UNKNOWN',
            reason_code=error_code if error_code in reasons else 'UNKNOWN'))
