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

    def to_dict(self) -> dict[str, Any]:
        return {'calls': [asdict(call) for call in self.calls], 'summary': self.summary()}

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for stage in ('query', 'answer', 'embedding', 'context_compaction', 'memory_extraction'):
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
        chat_calls = [call for call in self.calls if call.stage in ('query', 'answer', 'context_compaction', 'memory_extraction')]
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
    if stage not in ('query', 'answer', 'embedding', 'context_compaction', 'memory_extraction'):
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
                         status: str, usage: dict | None, latency_ms: float) -> None:
    """Capture supplier-returned tokens separately from monetary settlement."""
    capture = _CAPTURE.get()
    if capture is None:
        return
    usage = usage or {}
    stage = 'answer' if capability.startswith('chat_') else capability
    capture.calls.append(ModelCall(stage, capture.role, model, status, latency_ms,
        usage.get('prompt_tokens'), usage.get('completion_tokens'), provider=provider,
        model_key=model_key, capability=capability, settlement='UNKNOWN'))
