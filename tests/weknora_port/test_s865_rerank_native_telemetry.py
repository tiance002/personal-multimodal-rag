"""Pinned SDK export regression: real OpenTelemetry spans, no external sends."""
import json
import socket
from importlib.metadata import version
from types import SimpleNamespace

import httpx
import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.weknora_port.models.types import CallContext, PreparedCall, TokenUsage, TrustedScope
from backend.weknora_port.observability import LangfuseObservability, ObservabilitySettings


@pytest.mark.parametrize('usage,expected,settled', [
    (TokenUsage(4112, 0, 4112, 'REPORTED'), {'input': 4112, 'output': 0, 'total': 4112}, True),
    (TokenUsage(0, 0, 0, 'REPORTED'), {'input': 0, 'output': 0, 'total': 0}, True),
    (TokenUsage(), None, False),
])
def test_rerank_exports_native_model_and_reported_usage_without_content_or_network(monkeypatch, usage, expected, settled):
    assert version('langfuse') == '4.15.6'
    network_attempts = []

    def forbidden_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError('offline telemetry regression attempted network access')

    monkeypatch.setattr(socket.socket, 'connect', forbidden_network)
    monkeypatch.setattr(httpx.Client, 'send', forbidden_network)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()

    def factory(**options):
        return Langfuse(**options, tracer_provider=provider, span_exporter=exporter)

    obs = LangfuseObservability(ObservabilitySettings(True, True,
        base_url='https://telemetry.invalid', public_key='synthetic-rerank-' + str(expected),
        secret_key='synthetic-only'), client_factory=factory)
    context = CallContext('synthetic-rerank-request', TrustedScope('owner', ('kb',)))
    turn = SimpleNamespace(run_id='synthetic-rerank-run', session_id='PRIVATE session',
        client_request_id='synthetic-client', replay_only=False,
        request=SimpleNamespace(content='PRIVATE question', request_id='synthetic-server'), context=context)
    call = PreparedCall(context, 'synthetic-provider', 'synthetic-reranker', 'synthetic-model-id',
        'rerank', 'https://provider.invalid', 'a' * 64, 64, 64)
    root = obs.begin_turn(turn, cloud_consented=True)
    assert root is not None
    try:
        obs.stage(turn.run_id, 'rerank', 'started', {'candidate_count': 4, 'input': 'PRIVATE documents'})
        obs.provider_started(call, cloud_consented=True)
        obs.provider_finished(call, outcome='RESPONSE_RECEIVED', usage=usage,
            settlement_success=settled, settlement_error=None if settled else 'SYNTHETIC_SETTLEMENT_FAILURE')
        obs.stage(turn.run_id, 'rerank', 'completed', {'candidate_count': 4})
    finally:
        obs.finish_turn(root, status='completed')
        obs._client.flush()
        obs._client.shutdown()

    spans = exporter.get_finished_spans()
    child = next(span for span in spans if span.name == 'weknora.provider-rerank')
    attrs = child.attributes
    native_usage = attrs.get('langfuse.observation.usage_details')
    assert (json.loads(native_usage) if native_usage is not None else None) == expected
    assert attrs.get('langfuse.observation.model.name') == 'synthetic-reranker'
    assert attrs['langfuse.observation.type'] == 'generation'
    assert attrs['langfuse.observation.metadata.capability'] == 'rerank'
    assert attrs['langfuse.observation.metadata.settlement_success'] is settled
    assert attrs['langfuse.observation.metadata.usage_status'] == usage.status
    stage = next(span for span in spans if span.name == 'weknora.rerank')
    assert child.parent.span_id == stage.context.span_id
    assert stage.parent.span_id == int(root.span.id, 16)
    assert {span.context.trace_id for span in spans} == {int(root.trace_id, 16)}
    assert len(spans) == 3 and not obs._calls
    assert not any('PRIVATE' in str(span.attributes) for span in spans)
    assert network_attempts == []
