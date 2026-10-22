"""Offline vision admission/accounting through the ordinary Provider and gate."""
import asyncio
import base64
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from backend.weknora_port import execution
from backend.weknora_port.models import provider_config
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport, endpoint_urls
from backend.weknora_port.models.types import CallContext, ChatOptions, Message, PreparedCall, ProviderError, TrustedScope
from scripts.s8_6_4_prompt_diagnostics import PACKAGE, canonical, render_image_pair


def image_url(format='PNG', size=(8, 8)):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'white').save(buffer, format=format)
    mime = {'PNG': 'png', 'JPEG': 'jpeg', 'GIF': 'gif'}[format]
    return 'data:image/' + mime + ';base64,' + base64.b64encode(buffer.getvalue()).decode()


def body(url=None, count=1):
    return {'model': 'deepseek-flash', 'max_tokens': 5000, 'temperature': 0.10000000149011612,
            'messages': [{'role': 'user', 'content': [
                *[{'type': 'image_url', 'image_url': {'url': url or image_url(), 'detail': 'auto'}} for _ in range(count)],
                {'type': 'text', 'text': 'Preserve the visible labels.'}]}]}


def config(**kwargs):
    return replace(provider_config('deepseek', 'deepseek-flash', model_id='vision-model'), **kwargs)


CTX = CallContext('vision/new-1', TrustedScope('owner', ('kb',), ('document',)))


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / 'execution.json'
    path.write_text(json.dumps({'status': 'OPEN', 'tenant_id': 1, 'subject_id': 'owner',
        'allowed_kbs': ['kb'], 'allowed_models': [{'provider': 'deepseek', 'model': 'deepseek-flash',
        'model_id': 'vision-model', 'capability': 'vision'}], 'allowed_request_prefixes': ['vision/'],
        'max_calls': 4, 'max_tokens': 100000, 'capture_evidence': False, 'entries': []}), encoding='utf8')
    return path


class Gate:
    def __init__(self):
        self.application = SimpleNamespace(principal=SimpleNamespace(tenant_id=1, subject_id='owner'))
        self.calls = []
    async def authorize(self, call): self.calls.append(call)
    async def finish(self, call, **kwargs): return True


def read(path): return json.loads(path.read_text('utf8'))


async def send_mock(ledger, payload, *, cfg=None, context=CTX, usage=None, capture=False, mutate_call=None,
                    outcome='RESPONSE_RECEIVED', after_response_error=False):
    """Real adapter/transport logic, mock network, ordinary wrapped SendGate."""
    cfg = cfg or config()
    saved = read(ledger)
    saved['capture_evidence'] = capture
    ledger.write_text(json.dumps(saved), encoding='utf8')
    fence = execution.LocalExecution(ledger)
    original_gate = Gate()
    gate = fence.install_gate(original_gate)
    if mutate_call:
        authorize = gate.authorize
        async def altered(call): await authorize(mutate_call(call))
        gate.authorize = altered
    requests = []
    def respond(request):
        requests.append(request.content)
        response = {'model': cfg.model, 'choices': [{'message': {'content': 'Observed content.'}, 'finish_reason': 'stop'}]}
        if usage is not None: response['usage'] = usage
        return httpx.Response(200, json=response)
    transport = AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg), gate=gate,
        credential=lambda _: 'OFFLINE_FIXTURE', http_transport=httpx.MockTransport(respond))
    model = fence.observe_model(ProviderModel(cfg, transport))
    try:
        # Use transport.open for deliberately malformed wire-field tests; normal
        # fixed cases below also exercise ProviderModel.vision separately.
        async with transport.open(cfg, '/chat/completions', payload, context, capability='vision') as handle:
            from backend.weknora_port.models.usage import normalize_usage
            raw = json.loads(await handle.response.aread())
            reported = normalize_usage(raw.get('usage'))
            if outcome == 'RESPONSE_RECEIVED': handle.observed(reported)
            else: handle.usage = reported
            if after_response_error: raise ProviderError('ANSWER_PARSE_FAILED', sent=True)
        return requests, original_gate.calls, model
    finally:
        await transport.aclose()


@pytest.mark.parametrize('qid', ['row-0958', 'row-0684'])
@pytest.mark.parametrize('variant', ['old', 'candidate'])
@pytest.mark.parametrize('mode', [None, execution.REPORTED_USAGE_ONLY])
def test_exact_real_images_account_without_treating_base64_as_tokens(ledger, qid, variant, mode):
    if mode:
        value = read(ledger); value['accounting_mode'] = mode
        ledger.write_text(json.dumps(value), encoding='utf8')
    payload = render_image_pair(PACKAGE, qid)[variant]
    before = copy.deepcopy(payload)
    historical = {'prompt_tokens': 1018, 'completion_tokens': 840, 'total_tokens': 1858} if qid == 'row-0958' else {
        'prompt_tokens': 1032, 'completion_tokens': 1161, 'total_tokens': 2193}
    requests, calls, *_ = asyncio.run(send_mock(ledger, payload, usage=historical))
    assert payload == before and requests == [canonical(payload)]
    assert len(calls) == 1 and calls[0].capability == 'vision'
    row = read(ledger)['entries'][0]
    surrogate = copy.deepcopy(payload)
    surrogate['messages'][0]['content'][0]['image_url']['url'] = ''
    assert row['image_count'] == 1 and row['wire_bytes'] == len(requests[0]) > 300000
    if mode:
        assert not any(k in row for k in ('reserved_tokens','held_tokens','input_tokens_upper_bound','input_bound_recipe'))
    else:
        assert row['input_tokens_upper_bound'] == len(canonical(surrogate)) + 1024
        assert row['reserved_tokens'] == row['input_tokens_upper_bound'] + 5000 + 1024 < 10000
        assert row['input_bound_recipe'] == 'deepseek-inline-image-text-bytes-1024/v1'
        assert row['held_tokens'] == historical['total_tokens']
    assert row['status'] == 'SETTLED' and row['usage']['total_tokens'] == historical['total_tokens']
    assert row['usage']['prompt_tokens'] == historical['prompt_tokens']
    assert not (ledger.parent / 'evidence').exists()


def test_two_images_one_request_counts_each_and_preserves_raw_capture(ledger):
    payload = body(count=2)
    requests, *_ = asyncio.run(send_mock(ledger, payload, usage={
        'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}, capture=True))
    row = read(ledger)['entries'][0]
    assert row['image_count'] == 2
    assert row['input_tokens_upper_bound'] >= 2048
    assert row['held_tokens'] == 150
    assert next((ledger.parent/'evidence').glob('*.response.body')).read_bytes()
    assert json.loads(next((ledger.parent/'evidence').glob('*.request.json')).read_text('utf8'))['body'] == payload


@pytest.mark.parametrize('alteration', ['remote_url', 'invalid_base64', 'wrong_mime', 'gif', 'animated_png',
    'empty_image', 'corrupt_image', 'decompression_bomb', 'non_user_image', 'unknown_part', 'file_id', 'audio', 'tool',
    'missing_image', 'bad_detail', 'unknown_provider', 'unknown_model', 'wrong_endpoint', 'wrong_protocol', 'missing_output'])
def test_unbounded_or_invalid_image_request_denied_before_gate_or_mock_transport(ledger, alteration):
    payload = body()
    cfg = config()
    part = payload['messages'][0]['content'][0]
    if alteration == 'remote_url': part['image_url']['url'] = 'https://example.com/image.png'
    if alteration == 'invalid_base64': part['image_url']['url'] = 'data:image/png;base64,%%%%'
    if alteration == 'wrong_mime': part['image_url']['url'] = image_url('JPEG').replace('image/jpeg', 'image/png')
    if alteration == 'gif': part['image_url']['url'] = image_url('GIF')
    if alteration == 'animated_png':
        data = io.BytesIO(); frames = [Image.new('RGB', (8,8), c) for c in ('red','blue')]
        frames[0].save(data, format='PNG', save_all=True, append_images=frames[1:])
        part['image_url']['url'] = 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()
    if alteration == 'empty_image': part['image_url']['url'] = 'data:image/png;base64,'
    if alteration == 'corrupt_image': part['image_url']['url'] = 'data:image/png;base64,' + base64.b64encode(b'not PNG').decode()
    if alteration == 'decompression_bomb':
        import struct, zlib
        raw = bytearray(base64.b64decode(image_url().split(',',1)[1]))
        raw[16:24] = struct.pack('>II', 20000,20000)
        raw[29:33] = struct.pack('>I', zlib.crc32(raw[12:29]))
        part['image_url']['url'] = 'data:image/png;base64,' + base64.b64encode(raw).decode()
    if alteration == 'non_user_image': payload['messages'][0]['role'] = 'system'
    if alteration == 'unknown_part': payload['messages'][0]['content'].append({'type': 'unknown', 'text': 'x'})
    if alteration == 'file_id': part.clear(); part.update(type='file', file_id='file-api-unknown')
    if alteration == 'audio': payload['audio'] = {'data': 'unknown'}
    if alteration == 'tool': payload['messages'][0]['tool_calls'] = [{'id': 'x'}]
    if alteration == 'missing_image': payload['messages'][0]['content'] = [{'type': 'text', 'text': 'hello'}]
    if alteration == 'bad_detail': part['image_url']['detail'] = 'unknown'
    if alteration == 'unknown_provider': cfg = replace(cfg, provider='unknown')
    if alteration == 'unknown_model': cfg = replace(cfg, model='unknown')
    if alteration == 'wrong_endpoint': cfg = replace(cfg, base_url='https://example.com')
    if alteration == 'wrong_protocol': cfg = replace(cfg, api='unknown')
    if alteration == 'missing_output': payload.pop('max_tokens')
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND_UNVERIFIABLE'):
        asyncio.run(send_mock(ledger, payload, cfg=cfg))
    assert read(ledger)['entries'] == []


@pytest.mark.parametrize('guard,value', [('VISION_MAX_WIRE_BYTES', 100), ('VISION_MAX_IMAGE_BYTES', 10),
    ('VISION_MAX_TOTAL_IMAGE_BYTES', 10), ('VISION_MAX_IMAGES', 1), ('VISION_MAX_DIMENSION', 4)])
def test_independent_media_byte_count_and_dimension_guards(ledger, monkeypatch, guard, value):
    monkeypatch.setattr(execution, guard, value)
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND_UNVERIFIABLE'):
        asyncio.run(send_mock(ledger, body(count=2)))
    assert read(ledger)['entries'] == []


def test_fifteen_images_use_4096_dimension_limit(ledger):
    payload = body(image_url(size=(4097,1)), count=15)
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND_UNVERIFIABLE'):
        asyncio.run(send_mock(ledger, payload))
    assert read(ledger)['entries'] == []


@pytest.mark.parametrize('usage', [None, {'prompt_tokens':100,'total_tokens':100},
    {'prompt_tokens':-1,'completion_tokens':1,'total_tokens':0}])
def test_unknown_usage_stops_and_holds_bound(ledger, usage):
    if usage and usage.get('prompt_tokens') == -1:
        with pytest.raises(ProviderError, match='PROVIDER_USAGE_INVALID'):
            asyncio.run(send_mock(ledger, body(), usage=usage))
    else:
        asyncio.run(send_mock(ledger, body(), usage=usage))
    value = read(ledger); row = value['entries'][0]
    assert value['status'] == 'STOP' and row['status'] == 'UNKNOWN'
    assert row['held_tokens'] == row['reserved_tokens']


def test_reported_overshoot_preserves_raw_anomaly_and_stops(ledger):
    anomaly = {'prompt_tokens': 90000, 'completion_tokens': 5000, 'total_tokens': 95000}
    asyncio.run(send_mock(ledger, body(), usage=anomaly))
    value = read(ledger); row = value['entries'][0]
    assert value['status'] == 'STOP' and row['status'] == 'UNKNOWN'
    assert row['held_tokens'] == row['reserved_tokens']
    assert row['reported_usage_anomaly']['total_tokens'] == 95000


@pytest.mark.parametrize('field', ['request_id', 'model_id', 'wire_sha256', 'wire_bytes', 'capability', 'output_budget'])
def test_vision_bound_is_tied_to_exact_current_call(ledger, field):
    def alter(call):
        if field == 'request_id': return replace(call, context=replace(call.context, request_id='vision/other'))
        return replace(call, **{field: {'model_id':'other', 'wire_sha256':'a'*64,
                                      'wire_bytes':100, 'capability':'chat', 'output_budget':1}[field]})
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND'):
        asyncio.run(send_mock(ledger, body(), mutate_call=alter))
    assert read(ledger)['entries'] == []


def test_direct_vision_admission_without_observed_bound_is_denied(ledger):
    gate = execution.LocalExecution(ledger).install_gate(Gate())
    call = PreparedCall(CTX, 'deepseek', 'deepseek-flash', 'vision-model', 'vision',
                        'https://api.deepseek.com/chat/completions', 'a'*64, 100, 5000)
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND'):
        asyncio.run(gate.authorize(call))
    assert read(ledger)['entries'] == []


def reported_mode(ledger, **options):
    value = read(ledger); value.update(accounting_mode=execution.REPORTED_USAGE_ONLY, **options)
    ledger.write_text(json.dumps(value), encoding='utf8')


@pytest.mark.parametrize('usage', [None, {'prompt_tokens':100,'total_tokens':100},
    {'prompt_tokens':100,'completion_tokens':50,'total_tokens':149}])
def test_reported_only_unknown_or_inconsistent_usage_stops_without_zero_usage(ledger, usage):
    reported_mode(ledger)
    asyncio.run(send_mock(ledger, body(), usage=usage))
    value = read(ledger); row = value['entries'][0]
    assert value['status'] == 'STOP' and row['status'] == 'UNKNOWN' and row['usage'] is None
    assert not any(k in row for k in ('held_tokens','reserved_tokens','input_tokens_upper_bound'))
    assert 'reported_usage_anomaly' in row
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_UNKNOWN_STOP'):
        asyncio.run(send_mock(ledger, body(), context=replace(CTX, request_id='vision/new-2'), usage=usage))
    assert len(read(ledger)['entries']) == 1


def test_reported_actual_cap_overshoot_is_settled_and_future_sends_stop(ledger):
    reported_mode(ledger, max_tokens=100)
    actual = {'prompt_tokens':200, 'completion_tokens':20, 'total_tokens':220}
    asyncio.run(send_mock(ledger, body(), usage=actual))
    value = read(ledger); row = value['entries'][0]
    assert row['status'] == 'SETTLED' and row['usage']['total_tokens'] == 220
    assert value['status'] == 'STOP' and value['stopped_reason'] == 'REPORTED_USAGE_LIMIT_REACHED'
    assert not any(k in row for k in ('held_tokens','reserved_tokens'))
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_UNKNOWN_STOP'):
        asyncio.run(send_mock(ledger, body(), context=replace(CTX, request_id='vision/new-2'), usage=actual))


@pytest.mark.parametrize('change', [{'accounting_mode':'unknown'},
    {'accounting_mode':execution.REPORTED_USAGE_ONLY, 'entries':[{'reserved_tokens':123,'held_tokens':123}]}])
def test_reported_mode_rejects_unknown_mode_and_legacy_conversion(ledger, change):
    value = read(ledger); value.update(change); ledger.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_INVALID'):
        execution.LocalExecution(ledger)


def test_in_use_ledger_mode_cannot_silently_change(ledger):
    fence = execution.LocalExecution(ledger)
    reported_mode(ledger)
    with pytest.raises(ProviderError, match='ACCOUNTING_MODE_CHANGED'):
        with fence._ledger(): pass


def test_registry_provider_vision_keeps_default_options_and_context_vars_reset(ledger):
    """The actual neutral registry + ProviderModel.vision, no network or DB."""
    from backend.weknora_port.model_registry import NeutralModelRegistry, SourceCatalogue
    from backend.weknora_port.storage.configuration import ConfiguredRole
    reported_mode(ledger)
    role = ConfiguredRole(role='vlm', provider='deepseek', model='deepseek-flash',
                          base_url='https://api.deepseek.com', api_key='OFFLINE_FIXTURE', source='remote')
    class Store:
        async def resolve(self, model_id):
            assert model_id == 'vision-model'; return role
    async def scenario():
        requests = []
        gate = execution.LocalExecution(ledger).install_gate(Gate())
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'model':'deepseek-flash', 'choices':[
                {'message':{'content':'Observed image.'}, 'finish_reason':'stop'}],
                'usage':{'prompt_tokens':30,'completion_tokens':10,'total_tokens':40}})
        async def factory(cfg, key):
            transport = AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg), gate=gate,
                credential=lambda _:key, http_transport=httpx.MockTransport(respond))
            return execution.LocalExecution(ledger).observe_model(ProviderModel(cfg, transport))
        registry = NeutralModelRegistry(Store(), factory, owner_id='owner', catalogue=SourceCatalogue())
        try:
            model = await registry.get('vision-model', CTX)
            response = await model.vision((Message('user','Extract visible labels.',images=(image_url(),)),),None,CTX)
            assert response.usage.total_tokens == 40
            assert requests[0]['max_tokens'] == 5000 and requests[0]['temperature'] == 0.10000000149011612
            assert execution._VISION_BOUND.get() is None and execution._OUTPUT_BOUND.get() is None
        finally: await registry.close()
    asyncio.run(scenario())
    row = read(ledger)['entries'][0]
    assert row['usage']['total_tokens'] == 40 and 'held_tokens' not in row


@pytest.mark.parametrize('format', ['JPEG','PNG'])
def test_reported_mode_static_inline_formats_remain_supported(ledger, format):
    reported_mode(ledger)
    asyncio.run(send_mock(ledger, body(image_url(format)), usage={
        'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}))
    assert read(ledger)['entries'][0]['status'] == 'SETTLED'


def test_official_image_count_boundary_is_independent_of_token_admission(ledger):
    reported_mode(ledger)
    asyncio.run(send_mock(ledger, body(count=600), usage={
        'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}))
    assert read(ledger)['entries'][0]['image_count'] == 600
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND_UNVERIFIABLE'):
        asyncio.run(send_mock(ledger, body(count=601), context=replace(CTX, request_id='vision/new-2')))
    assert len(read(ledger)['entries']) == 1


def test_legacy_unknown_cannot_be_reinterpreted_as_reported_only(ledger):
    asyncio.run(send_mock(ledger, body()))
    value = read(ledger)
    assert value['status'] == 'STOP' and value['entries'][0]['status'] == 'UNKNOWN'
    value['accounting_mode'] = execution.REPORTED_USAGE_ONLY
    ledger.write_text(json.dumps(value), encoding='utf8'); before = ledger.read_bytes()
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_INVALID'):
        execution.LocalExecution(ledger)
    assert ledger.read_bytes() == before


def test_reported_counts_are_preserved_when_response_outcome_is_unclear(ledger):
    reported_mode(ledger)
    asyncio.run(send_mock(ledger, body(), usage={
        'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}, outcome='UNKNOWN'))
    value = read(ledger); row = value['entries'][0]
    assert value['status'] == 'STOP' and row['status'] == 'UNKNOWN'
    assert row['usage']['total_tokens'] == 120
    assert not any(k in row for k in ('reserved_tokens','held_tokens'))


def test_completed_usage_is_not_discarded_by_later_answer_contract_failure(ledger):
    reported_mode(ledger)
    with pytest.raises(ProviderError, match='ANSWER_PARSE_FAILED'):
        asyncio.run(send_mock(ledger, body(), usage={
            'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}, after_response_error=True))
    row = read(ledger)['entries'][0]
    assert row['status'] == 'SETTLED' and row['usage']['total_tokens'] == 120


def test_reported_mode_denies_concurrent_admission_without_poisoning_active_call(ledger):
    from backend.weknora_port.models.types import TokenUsage
    reported_mode(ledger)
    # Exercise the same gate with a text call, whose legacy direct admission
    # contract already exists. This isolates serialization from image parsing.
    value = read(ledger); value['allowed_models'][0]['capability'] = 'chat'
    ledger.write_text(json.dumps(value), encoding='utf8')
    gate = execution.LocalExecution(ledger).install_gate(Gate())
    first = PreparedCall(CTX, 'deepseek', 'deepseek-flash', 'vision-model', 'chat',
                         'https://api.deepseek.com/chat/completions', 'a'*64, 100, 5000)
    second = replace(first, context=replace(CTX, request_id='vision/new-2'))
    async def scenario():
        await gate.authorize(first)
        with pytest.raises(ProviderError, match='LOCAL_EXECUTION_INFLIGHT_DENIED'):
            await gate.authorize(second)
        value = read(ledger)
        assert value['status'] == 'OPEN' and value['entries'][0]['status'] == 'INFLIGHT'
        await gate.finish(first, outcome='RESPONSE_RECEIVED', usage=TokenUsage(100,20,120,'REPORTED'))
    asyncio.run(scenario())
    value = read(ledger)
    assert value['status'] == 'OPEN' and value['entries'][0]['status'] == 'SETTLED'
    assert len(value['entries']) == 1
