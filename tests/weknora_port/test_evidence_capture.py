"""Offline evidence observation contracts; no cloud, application server or database."""
import asyncio
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.evidence_capture import PassiveEvidenceCapture
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport, configured_endpoint
from backend.weknora_port.models.types import CallContext, ModelConfig, ProviderError, TrustedScope
from backend.weknora_port.runtime import ApplicationRuntime


@pytest.fixture
def evidence():
    root = Path(__file__).resolve().parents[2] / 'var'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        yield Path(folder) / 'evidence'


class Gate:
    def __init__(self): self.calls = []; self.finished = []
    async def authorize(self, call): self.calls.append(call)
    async def finish(self, call, **fields): self.finished.append((call, fields)); return True


def setup_model(response, capture=None):
    config = ModelConfig('siliconflow', 'vision-model', 'vision-id', 'https://example.invalid')
    gate = Gate()
    transport = AsyncHTTPTransport(allowed_endpoints=[configured_endpoint(config)], gate=gate,
        credential=lambda _: 'PRIVATE_API_KEY', http_transport=httpx.MockTransport(response))
    app = ApplicationRuntime.__new__(ApplicationRuntime)
    app.model_factory = lambda config, key, gate: ProviderModel(config, transport)
    if capture is not None: app.evidence_capture = capture
    model = asyncio.run(app._make_model(config, 'PRIVATE_API_KEY', gate))
    return config, gate, model


def invoke(config, model, body, *, stream=False):
    context = CallContext('vision/request', TrustedScope('owner', ('kb',)))
    async def execute():
        try:
            async with model.transport.open(config, '/', body, context, capability='vision', stream=stream) as handle:
                if stream: return b''.join([part async for part in handle.response.aiter_bytes()])
                return await handle.response.aread()
        finally: await model.transport.aclose()
    return asyncio.run(execute())


def test_no_configuration_preserves_transport_and_gate(evidence):
    config, gate, model = setup_model(lambda _: httpx.Response(200, content=b'original'))
    original = model.transport.open
    assert invoke(config, model, {'messages': [{'content': 'question'}]}) == b'original'
    assert model.transport.open == original
    assert model.transport.gate is gate and len(gate.calls) == len(gate.finished) == 1
    assert not hasattr(model.transport, '_passive_evidence_observed')
    assert not hasattr(gate, '_local_execution') and not evidence.exists()


@pytest.mark.parametrize('stream', [False, True])
def test_capture_image_body_raw_response_timing_without_auth_and_no_mutation(evidence, stream):
    sent = []
    raw = b'data: {"answer":"caption"}\n\ndata: [DONE]\n\n'
    def response(request):
        sent.append(request)
        return httpx.Response(200, content=raw, headers={'x-secret': 'RESPONSE_HEADER_SECRET'})
    capture = PassiveEvidenceCapture(evidence)
    config, gate, model = setup_model(response, capture)
    image = b'actual-image-bytes'
    body = {'messages': [{'role': 'user', 'content': [{'type': 'image_url',
        'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(image).decode()}}]}],
        'max_tokens': 100}
    original = json.loads(json.dumps(body))
    authorize, finish = gate.authorize, gate.finish
    assert capture.observe_model(model) is model
    assert invoke(config, model, body, stream=stream) == raw
    assert body == original and model.transport.gate is gate
    assert gate.authorize == authorize and gate.finish == finish
    assert len(sent) == len(gate.calls) == len(gate.finished) == 1
    assert not hasattr(gate, '_local_execution')
    saved = json.loads(next(evidence.glob('*.request.json')).read_text())
    assert saved['body'] == json.loads(sent[0].content) == body
    encoded = saved['body']['messages'][0]['content'][0]['image_url']['url'].split(',', 1)[1]
    assert hashlib.sha256(base64.b64decode(encoded)).digest() == hashlib.sha256(image).digest()
    assert saved['wire_sha256'] == hashlib.sha256(sent[0].content).hexdigest() == gate.calls[0].wire_sha256
    assert saved['wire_bytes'] == len(sent[0].content)
    assert saved['mode'] == 'passive_evidence_only' and saved['budget_protection'] is False
    assert next(evidence.glob('*.response.body')).read_bytes() == raw
    metadata = json.loads(next(evidence.glob('*.response.json')).read_text())
    assert metadata == {'complete': True, 'headers_saved': False, 'status_code': 200}
    timing = json.loads(next(evidence.glob('*.timing.json')).read_text())
    assert timing['transport_open_started_monotonic'] <= timing['response_headers_observed_monotonic'] <= timing['first_raw_byte_monotonic'] <= timing['read_finished_monotonic']
    saved_bytes = b''.join(path.read_bytes() for path in evidence.iterdir())
    assert b'PRIVATE_API_KEY' not in saved_bytes and b'RESPONSE_HEADER_SECRET' not in saved_bytes
    assert b'Authorization' not in saved_bytes


def test_invalid_directory_and_provider_error_preserved(evidence):
    for path in ('relative', 'C:/private', 'E:relative'):
        with pytest.raises(ValueError, match='EVIDENCE_ABSOLUTE_D_E_PATH_REQUIRED'):
            PassiveEvidenceCapture(path)
    evidence.parent.joinpath('not-a-directory').write_text('existing file')
    with pytest.raises(ValueError, match='EVIDENCE_DIRECTORY_REQUIRED'):
        PassiveEvidenceCapture(evidence.parent / 'not-a-directory')
    config, gate, model = setup_model(lambda _: httpx.Response(503, content=b'provider failure'), PassiveEvidenceCapture(evidence))
    with pytest.raises(ProviderError, match='PROVIDER_HTTP_ERROR') as error:
        invoke(config, model, {'messages': [{'content': 'image request'}]})
    assert error.value.status_code == 503 and error.value.sent is True
    assert len(gate.calls) == len(gate.finished) == 1
    assert json.loads(next(evidence.glob('*.error.json')).read_text())['error_type'] == 'ProviderError'


def test_pipeline_same_stages_final_context_and_callback(evidence):
    @dataclass
    class Context:
        user_content: str
    result = Context('real context')
    state = SimpleNamespace(search_results=[], rerank_results=[], merge_results=[],
        query='question', user_content='real context', evidence_trace=[])
    received = []
    pipeline = SimpleNamespace(on_progress=lambda stage, status, **fields: received.append((stage, status)))
    async def run(current, context):
        assert current is state
        await pipeline.on_progress('retrieval', 'completed')
        state.evidence_trace.append({'stage': 'context'})
        await pipeline.on_progress('context', 'completed')
        return result
    pipeline.run = run
    capture = PassiveEvidenceCapture(evidence)
    assert capture.observe_pipeline(pipeline, SimpleNamespace(run_id='run', session_id='session')) is pipeline
    assert asyncio.run(pipeline.run(state, None)) is result
    saved = json.loads(next(evidence.glob('*.stages.json')).read_text())
    assert received == [('retrieval', 'completed'), ('context', 'completed')]
    assert saved['finalized_context'] == {'user_content': 'real context'}
    assert saved['stages'][0]['state']['evidence_trace'] == []
    assert saved['stages'][1]['state']['evidence_trace'] == [{'stage': 'context'}]
