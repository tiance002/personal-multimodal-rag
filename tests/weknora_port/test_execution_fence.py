import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from backend.weknora_port.execution import LocalExecution
from backend.weknora_port.models.types import CallContext, PreparedCall, ProviderError, TokenUsage, TrustedScope


class Gate:
    def __init__(self, fail=False):
        self.application = SimpleNamespace(principal=SimpleNamespace(tenant_id=1, subject_id='owner'))
        self.calls = 0
        self.fail = fail
    async def authorize(self, call):
        self.calls += 1
        if self.fail: raise ProviderError('ORIGINAL_DENIED')
    async def finish(self, call, **kwargs): return True


def call(identity='run/1'):
    return PreparedCall(CallContext(identity, TrustedScope('owner', ('kb',))),
        'provider', 'model', 'm1', 'chat', 'https://example.invalid', 'a'*64, 100, 50)


@pytest.fixture
def ledger():
    root = Path(os.environ.get('WEKNORA_EXECUTION_TEST_ROOT', Path(__file__).resolve().parents[2] / 'var'))
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        path = Path(folder) / 'owner.json'
        path.write_text(json.dumps({'status': 'OPEN', 'tenant_id': 1, 'subject_id': 'owner',
            'allowed_kbs': ['kb'], 'allowed_models': [{'provider': 'provider', 'model': 'model', 'model_id': 'm1', 'capability': 'chat'}],
            'allowed_request_prefixes': ['run/'], 'max_calls': 12, 'max_tokens': 100000, 'capture_evidence': True, 'entries': []}))
        yield path


def data(path): return json.loads(path.read_text())
def change(path, **kwargs): path.write_text(json.dumps({**data(path), **kwargs}))
def run(coro): return asyncio.run(coro)


def test_pipeline_observation_preserves_state_and_result(ledger):
    from dataclasses import dataclass
    @dataclass
    class Context:
        user_content: str
    seen = []
    state = SimpleNamespace(search_results=[], rerank_results=[], merge_results=[], rescue_evidence=[],
        query='question', rewrite_query='same', intent='kb_search', needs_retrieval=True, user_content='raw evidence')
    async def progress(stage, status, **fields): seen.append((stage, status))
    pipeline = SimpleNamespace(on_progress=progress)
    result = Context('raw evidence')
    async def execute(current, context):
        assert current is state
        await pipeline.on_progress('context', 'completed')
        return result
    pipeline.run = execute
    assert LocalExecution(ledger).observe_pipeline(pipeline, SimpleNamespace(run_id='run', session_id='session')) is pipeline
    assert run(pipeline.run(state, None)) is result
    assert seen == [('context', 'completed')]
    snapshot = json.loads(next((ledger.parent/'evidence').glob('*.stages.json')).read_text())
    assert snapshot['finalized_context']['user_content'] == 'raw evidence'
    assert snapshot['stages'][0]['state']['needs_retrieval'] is True


def test_trace_snapshot_cannot_acquire_future_selection_events(ledger):
    from dataclasses import dataclass
    @dataclass
    class Context:
        user_content: str
    state=SimpleNamespace(search_results=[],rerank_results=[],merge_results=[],rescue_evidence=[],evidence_trace=[])
    pipeline=SimpleNamespace(on_progress=None)
    async def execute(current,context):
        await pipeline.on_progress('retrieval','completed')
        state.evidence_trace.append({'stage':'later_source_unit'})
        await pipeline.on_progress('context','completed')
        return Context('text')
    pipeline.run=execute
    LocalExecution(ledger).observe_pipeline(pipeline,SimpleNamespace(run_id='trace',session_id='s'))
    run(pipeline.run(state,None))
    saved=json.loads(next((ledger.parent/'evidence').glob('*.stages.json')).read_text())
    assert saved['stages'][0]['state']['evidence_trace']==[]
    assert saved['stages'][1]['state']['evidence_trace']==[{'stage':'later_source_unit'}]


def test_install_preserves_identity_and_preload(ledger):
    fence, gate = LocalExecution(ledger), Gate()
    assert fence.install_gate(gate) is gate
    assert LocalExecution(ledger).install_gate(gate) is gate
    assert data(ledger)['entries'] == []
    run(gate.authorize(call()))
    assert gate.calls == 1
    run(gate.finish(call(), outcome='RESPONSE_RECEIVED', usage=TokenUsage(10, 5, 15, 'REPORTED')))
    assert data(ledger)['entries'][0]['held_tokens'] == 15
    with pytest.raises(ProviderError, match='REUSE'): run(gate.authorize(call()))


@pytest.mark.parametrize('alter', ['tenant', 'owner', 'scope', 'origin', 'model', 'model_id', 'prefix', 'closed'])
def test_scope_before_original(ledger, alter):
    gate = LocalExecution(ledger).install_gate(Gate())
    c = call()
    if alter == 'tenant': gate.application.principal.tenant_id = 2
    if alter == 'owner': c = replace(c, context=replace(c.context, scope=TrustedScope('other', ('kb',))))
    if alter == 'scope': c = replace(c, context=replace(c.context, scope=TrustedScope('owner', ('other',))))
    if alter == 'origin': c = replace(c, context=replace(c.context, required_origin_kbs=('other',)))
    if alter == 'model': c = replace(c, model='other')
    if alter == 'model_id': c = replace(c, model_id='different-registry-row')
    if alter == 'prefix': c = replace(c, context=replace(c.context, request_id='other/1'))
    if alter == 'closed': change(ledger, status='CLOSED')
    with pytest.raises(ProviderError): run(gate.authorize(c))
    assert gate.calls == 0 and not data(ledger)['entries']


@pytest.mark.parametrize('limit', [{'max_calls': 1}, {'max_tokens': 1200}])
def test_limits_and_reservation(ledger, limit):
    change(ledger, **limit)
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    assert data(ledger)['entries'][0]['held_tokens'] == 1174
    with pytest.raises(ProviderError, match='LIMIT'): run(gate.authorize(call('run/2')))
    assert gate.calls == 1


@pytest.mark.parametrize('usage', [TokenUsage(), TokenUsage(total_tokens=-1, status='REPORTED'),
    TokenUsage(total_tokens=5000, status='REPORTED'), TokenUsage(-1, 3, 2, 'REPORTED')])
def test_unknown_stops_and_retains(ledger, usage):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    run(gate.finish(call(), outcome='UNKNOWN', usage=usage))
    state = data(ledger)
    assert state['status'] == 'STOP' and state['entries'][0]['held_tokens'] == 1174
    with pytest.raises(ProviderError): run(gate.authorize(call('run/2')))


def test_admission_failure_keeps_reservation(ledger):
    gate = LocalExecution(ledger).install_gate(Gate(fail=True))
    with pytest.raises(ProviderError, match='ORIGINAL'): run(gate.authorize(call()))
    state = data(ledger)
    assert state['status'] == 'STOP'
    assert state['entries'][0]['status'] == 'LOCAL_ADMISSION_FAILED_STOP'
    assert state['entries'][0]['held_tokens'] == 1174


def test_reported_usage_with_unknown_outcome_stops(ledger):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    run(gate.finish(call(), outcome='UNKNOWN', usage=TokenUsage(10, 5, 15, 'REPORTED')))
    state = data(ledger)
    assert state['status'] == 'STOP'
    assert state['entries'][0]['held_tokens'] == 1174
    assert state['entries'][0]['outcome'] == 'UNKNOWN'
    assert state['entries'][0]['usage']['total_tokens'] == 15


def test_live_owner_survives_independent_assembly(ledger):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    restarted = LocalExecution(ledger).install_gate(Gate())
    assert data(ledger)['status'] == 'OPEN'
    run(restarted.authorize(call('run/2')))
    assert restarted.calls == 1


def contend(path, number, queue):
    try:
        run(LocalExecution(path).install_gate(Gate()).authorize(call(f'run/{number}')))
        queue.put('admitted')
    except ProviderError: queue.put('denied')


def test_processes_share_atomic_claim(ledger):
    change(ledger, max_calls=1)
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    processes = [ctx.Process(target=contend, args=(ledger, n, queue)) for n in range(4)]
    for process in processes: process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert [queue.get(timeout=5) for _ in processes].count('admitted') == 1
    assert len(data(ledger)['entries']) == 1


@pytest.mark.parametrize('stream', [False, True])
def test_observer_byte_preservation_no_headers(ledger, stream):
    raw = b'data: {"answer":"test"}\n\n'
    class Response:
        status_code = 200
        async def aread(self): return raw
        async def aiter_bytes(self):
            yield raw[:5]
            yield raw[5:]
    @asynccontextmanager
    async def opened(*args, **kwargs): yield SimpleNamespace(response=Response())
    model = SimpleNamespace(transport=SimpleNamespace(open=opened))
    fence = LocalExecution(ledger)
    assert fence.observe_model(model) is model
    fence.observe_model(model)
    from backend.weknora_port.models.types import ModelConfig
    config = ModelConfig('siliconflow', 'model', 'm1', 'https://example.invalid', custom_headers={'Authorization': 'secret'})
    async def consume():
        async with model.transport.open(config, '/', {'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 50, 'enable_thinking': False, 'api_key': 'secret', 'nested': {'Authorization': 'secret'}}, call().context, capability='chat') as handle:
            if stream: return b''.join([chunk async for chunk in handle.response.aiter_bytes()])
            return await handle.response.aread()
    assert run(consume()) == raw
    assert next(fence.evidence.glob('*.response.body')).read_bytes() == raw
    request = next(fence.evidence.glob('*.request.json')).read_text()
    assert 'secret' not in request and 'Authorization' not in request
    assert json.loads(next(fence.evidence.glob('*.response.json')).read_text())['complete'] is True


def test_observer_can_close_from_stream_cleanup_context(ledger):
    from backend.weknora_port.models.types import ModelConfig
    closed = []
    @asynccontextmanager
    async def opened(*args, **kwargs):
        try: yield SimpleNamespace(response=SimpleNamespace())
        finally: closed.append(True)
    change(ledger, capture_evidence=False)
    model = LocalExecution(ledger).observe_model(SimpleNamespace(transport=SimpleNamespace(open=opened)))
    async def scenario():
        manager = model.transport.open(ModelConfig('siliconflow', 'model', 'm1', 'https://example.invalid'),
            '/', {'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 50,
                 'enable_thinking': False}, call().context, capability='chat')
        await manager.__aenter__()
        await asyncio.create_task(manager.__aexit__(None, None, None))
    run(scenario())
    assert closed == [True]


def test_observer_refuses_siliconflow_unspecified_thinking_before_open(ledger):
    from backend.weknora_port.models.types import ModelConfig
    opened = []
    @asynccontextmanager
    async def original_open(*args, **kwargs):
        opened.append(True)
        yield SimpleNamespace(response=SimpleNamespace())
    model = LocalExecution(ledger).observe_model(
        SimpleNamespace(transport=SimpleNamespace(open=original_open)))
    async def execute():
        async with model.transport.open(ModelConfig('siliconflow', 'model', 'm1',
                'https://example.invalid'), '/',
                {'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 50},
                call().context, capability='chat'):
            pass
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND_UNVERIFIABLE'):
        run(execute())
    assert opened == []


@pytest.mark.parametrize('alter', ['negative_held', 'negative_reserve', 'duplicate', 'bad_id', 'bad_usage', 'bad_unknown_usage', 'bad_json'])
def test_invalid_ledger_is_never_rewritten(ledger, alter):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    run(gate.finish(call(), outcome='RESPONSE_RECEIVED', usage=TokenUsage(10, 5, 15, 'REPORTED')))
    value = data(ledger)
    row = value['entries'][0]
    if alter == 'negative_held': row['held_tokens'] = -1
    if alter == 'negative_reserve': row['reserved_tokens'] = -1
    if alter == 'duplicate': value['entries'].append(dict(row))
    if alter == 'bad_id': row['id'] = 'forged'
    if alter == 'bad_usage': row['usage']['prompt_tokens'] = 100
    if alter == 'bad_unknown_usage':
        value['status'], row['status'], row['held_tokens'] = 'STOP', 'UNKNOWN', row['reserved_tokens']
        row['usage']['total_tokens'] = -1
    ledger.write_text('{' if alter == 'bad_json' else json.dumps(value))
    original = ledger.read_bytes()
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_INVALID'): LocalExecution(ledger)
    assert ledger.read_bytes() == original


@pytest.mark.parametrize('constructor', [False, True])
@pytest.mark.parametrize('owner', [None, {'pid': 999999999, 'creation_token': 'gone'}, {'pid': 1, 'creation_token': 'wrong'}])
def test_orphan_stop_is_persistent_without_releasing(ledger, constructor, owner):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    value = data(ledger)
    value['entries'][0]['process_identity'] = owner
    ledger.write_text(json.dumps(value))
    if constructor: LocalExecution(ledger)
    else:
        with pytest.raises(ProviderError): run(gate.authorize(call('run/2')))
    assert data(ledger)['status'] == 'STOP'
    assert data(ledger)['entries'][0]['held_tokens'] == 1174


@pytest.mark.parametrize('result', [None, False, 'error'])
def test_original_settlement_must_explicitly_succeed(ledger, result):
    gate = Gate()
    async def finish(*args, **kwargs):
        if result == 'error': raise RuntimeError('synthetic PG failure')
        return result
    gate.finish = finish
    LocalExecution(ledger).install_gate(gate)
    run(gate.authorize(call()))
    with pytest.raises((ProviderError, RuntimeError)):
        run(gate.finish(call(), outcome='RESPONSE_RECEIVED', usage=TokenUsage(10, 5, 15, 'REPORTED')))
    assert data(ledger)['status'] == 'STOP'
    assert data(ledger)['entries'][0]['held_tokens'] == 1174


@pytest.mark.parametrize('usage', [TokenUsage(10, 6, 15, 'REPORTED'), TokenUsage(16, None, 15, 'REPORTED'),
    TokenUsage(10, 5, 15, 'REPORTED', cached_tokens=-1)])
def test_inconsistent_reported_usage_retains_all_occupancy(ledger, usage):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    run(gate.finish(call(), outcome='RESPONSE_RECEIVED', usage=usage))
    assert data(ledger)['status'] == 'STOP'
    assert data(ledger)['entries'][0]['held_tokens'] == 1174


@pytest.mark.parametrize('kind', ['chat', 'embedding', 'rerank', 'unknown_provider', 'unknown_protocol', 'multimedia', 'missing_cap'])
@pytest.mark.parametrize('mode', [None, 'reported_usage_only_v1'])
def test_actual_transport_open_bound_and_default_private(ledger, kind, mode):
    import httpx
    from backend.weknora_port.models.transport import AsyncHTTPTransport, configured_endpoint
    from backend.weknora_port.models.types import ModelConfig
    config = ModelConfig('siliconflow', 'model', 'm1', 'https://example.invalid', requires_auth=False)
    capability = kind if kind in ('embedding', 'rerank') else 'chat'
    body = {'messages': [{'role': 'user', 'content': 'private prompt'}], 'max_tokens': 50,
            'enable_thinking': False}
    if kind == 'embedding': body = {'input': ['private prompt']}
    if kind == 'rerank': body = {'query': 'private prompt', 'documents': ['private evidence']}
    if kind == 'unknown_provider': config = replace(config, provider='unknown')
    if kind == 'unknown_protocol': config = replace(config, api='unknown')
    if kind == 'multimedia': body['messages'][0]['content'] = [{'type': 'image_url', 'image_url': 'secret'}]
    if kind == 'missing_cap': body.pop('max_tokens')
    change(ledger, capture_evidence=False, allowed_models=[{'provider': config.provider, 'model': 'model', 'model_id': 'm1', 'capability': capability}])
    if mode: change(ledger, accounting_mode=mode)
    gate = LocalExecution(ledger).install_gate(Gate())
    sent = []
    def response(request):
        sent.append(request.content)
        return httpx.Response(200, json={'private': 'response'})
    transport = AsyncHTTPTransport(allowed_endpoints=[configured_endpoint(config, capability)], gate=gate,
        http_transport=httpx.MockTransport(response))
    LocalExecution(ledger).observe_model(SimpleNamespace(transport=transport))
    async def execute():
        try:
            async with transport.open(config, '/', body, call().context, capability=capability) as handle:
                await handle.response.aread()
                handle.observed(TokenUsage(10, 5, 15, 'REPORTED'))
        finally: await transport.aclose()
    if kind in ('unknown_provider', 'unknown_protocol', 'multimedia', 'missing_cap'):
        with pytest.raises(ProviderError, match='LOCAL_EXECUTION_BOUND_UNVERIFIABLE'): run(execute())
        assert not sent and not data(ledger)['entries'] and gate.calls == 0
    else:
        run(execute())
        assert len(sent) == 1
        row = data(ledger)['entries'][0]
        if mode:
            assert 'reserved_tokens' not in row and 'held_tokens' not in row
            assert row['usage']['total_tokens'] == 15
        else:
            assert row['reserved_tokens'] == len(sent[0]) + (50 if capability == 'chat' else 0) + 1024
        assert row['status'] == 'SETTLED'
    assert not (ledger.parent / 'evidence').exists()


def test_default_pipeline_does_not_capture_original_text(ledger):
    change(ledger, capture_evidence=False)
    async def execute(*args): return 'private response'
    pipeline = SimpleNamespace(run=execute, on_progress=None)
    fence = LocalExecution(ledger)
    assert fence.observe_pipeline(pipeline, SimpleNamespace(run_id='run', session_id='session')) is pipeline
    assert run(pipeline.run(None, None)) == 'private response'
    assert not fence.evidence.exists()


def test_complete_output_upper_is_reserved_without_body_mutation(ledger, monkeypatch):
    from backend.weknora_port.models import output_contract
    from backend.weknora_port.models.types import ModelConfig
    import httpx
    from backend.weknora_port.models.transport import AsyncHTTPTransport, configured_endpoint
    monkeypatch.setitem(output_contract.CAP_CONTRACTS, ('siliconflow', 'openai-completions'),
        ('max_tokens', output_contract.VISIBLE_PLUS_REASONING, 'reasoning_limit'))
    config = ModelConfig('siliconflow', 'model', 'm1', 'https://example.invalid', requires_auth=False)
    body = {'messages': [{'role': 'user', 'content': 'question'}], 'max_tokens': 50, 'reasoning_limit': 200}
    unchanged = json.dumps(body, sort_keys=True)
    change(ledger, capture_evidence=False, allowed_models=[{'provider': 'siliconflow', 'model': 'model', 'model_id': 'm1', 'capability': 'chat'}])
    gate = LocalExecution(ledger).install_gate(Gate())
    sent = []
    def response(request):
        sent.append(request.content)
        return httpx.Response(200, json={})
    transport = AsyncHTTPTransport(allowed_endpoints=[configured_endpoint(config)], gate=gate,
        http_transport=httpx.MockTransport(response))
    LocalExecution(ledger).observe_model(SimpleNamespace(transport=transport))
    async def execute():
        try:
            async with transport.open(config, '/', body, call().context, capability='chat') as handle:
                await handle.response.aread()
                handle.observed(TokenUsage(10, 5, 15, 'REPORTED'))
        finally: await transport.aclose()
    run(execute())
    assert json.dumps(body, sort_keys=True) == unchanged
    assert data(ledger)['entries'][0]['reserved_tokens'] == len(sent[0]) + 250 + 1024


def test_siliconflow_explicitly_disabled_thinking_reserves_visible_cap(ledger):
    from backend.weknora_port.models.types import ModelConfig
    import httpx
    from backend.weknora_port.models.transport import AsyncHTTPTransport, configured_endpoint
    config = ModelConfig('siliconflow', 'model', 'm1', 'https://example.invalid', requires_auth=False)
    body = {'messages': [{'role': 'user', 'content': 'question'}], 'max_tokens': 50,
            'enable_thinking': False}
    unchanged = json.dumps(body, sort_keys=True)
    change(ledger, capture_evidence=False, allowed_models=[{'provider': 'siliconflow', 'model': 'model', 'model_id': 'm1', 'capability': 'chat'}])
    gate = LocalExecution(ledger).install_gate(Gate())
    sent = []
    def response(request):
        sent.append(request.content)
        return httpx.Response(200, json={})
    transport = AsyncHTTPTransport(allowed_endpoints=[configured_endpoint(config)], gate=gate,
        http_transport=httpx.MockTransport(response))
    LocalExecution(ledger).observe_model(SimpleNamespace(transport=transport))
    async def execute():
        try:
            async with transport.open(config, '/', body, call().context, capability='chat') as handle:
                await handle.response.aread()
                handle.observed(TokenUsage(10, 5, 15, 'REPORTED'))
        finally: await transport.aclose()
    run(execute())
    assert json.dumps(body, sort_keys=True) == unchanged
    assert data(ledger)['entries'][0]['reserved_tokens'] == len(sent[0]) + 50 + 1024


@pytest.mark.parametrize('status,code', [('STOP', 'LOCAL_EXECUTION_UNKNOWN_STOP'), ('CLOSED', 'LOCAL_EXECUTION_CLOSED')])
def test_persistent_status_has_specific_error_before_scope(ledger, status, code):
    gate = LocalExecution(ledger).install_gate(Gate())
    change(ledger, status=status)
    gate.application.principal.tenant_id = 'also_invalid'
    with pytest.raises(ProviderError, match=code): run(gate.authorize(call()))
    assert gate.calls == 0


def test_optimized_python_retains_ledger_and_bound_guards(ledger):
    import subprocess
    import sys
    script = r"""
import asyncio, json, sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from backend.weknora_port.execution import LocalExecution
from backend.weknora_port.models.types import ModelConfig, ProviderError
path = Path(sys.argv[1])
original = path.read_bytes()
value = json.loads(original)
value['max_calls'] = -1
path.write_text(json.dumps(value))
broken = path.read_bytes()
try:
    LocalExecution(path)
except ProviderError as error:
    if error.code != 'LOCAL_EXECUTION_INVALID': raise
else: raise RuntimeError('optimized mode accepted corrupt ledger')
if path.read_bytes() != broken: raise RuntimeError('corrupt ledger was rewritten')
path.write_bytes(original)
@asynccontextmanager
async def forbidden(*args, **kwargs):
    raise RuntimeError('unknown bound reached transport')
    yield
model = SimpleNamespace(transport=SimpleNamespace(open=forbidden))
LocalExecution(path).observe_model(model)
config = ModelConfig('unknown', 'model', 'm1', 'https://example.invalid')
async def execute():
    try:
        async with model.transport.open(config, '/', {'messages': [{'content': 'text'}], 'max_tokens': 50},
                None, capability='chat'): pass
    except ProviderError as error:
        if error.code != 'LOCAL_EXECUTION_BOUND_UNVERIFIABLE': raise
    else: raise RuntimeError('optimized mode accepted unknown bound')
asyncio.run(execute())
print('optimized guards rejected corrupt ledger and unknown bound')
"""
    result = subprocess.run([sys.executable, '-O', '-c', script, str(ledger)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'optimized guards rejected' in result.stdout


@pytest.mark.parametrize('status', ['OPEN', 'STOP', 'CLOSED'])
def test_legacy_model_permission_without_registry_id_is_read_only(ledger, status):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    assert data(ledger)['entries'][0]['model_id'] == 'm1'
    run(gate.finish(call(), outcome='RESPONSE_RECEIVED', usage=TokenUsage(10, 5, 15, 'REPORTED')))
    value = data(ledger)
    value['status'] = status
    value['allowed_models'][0].pop('model_id')
    value['entries'][0].pop('model_id')
    ledger.write_text(json.dumps(value))
    original = ledger.read_bytes()
    if status == 'OPEN':
        with pytest.raises(ProviderError, match='LOCAL_EXECUTION_INVALID'): LocalExecution(ledger)
        assert ledger.read_bytes() == original
    else:
        LocalExecution(ledger)
        assert data(ledger) == value
        assert 'model_id' not in data(ledger)['allowed_models'][0]



def test_wildcard_request_permission_accepts_uuid_but_keeps_scope(ledger):
    import uuid
    change(ledger, allowed_request_prefixes=['*'])
    gate = LocalExecution(ledger).install_gate(Gate())
    valid = call(str(uuid.uuid4()))
    run(gate.authorize(valid))
    assert gate.calls == 1
    assert data(ledger)['entries'][0]['request_id'] == valid.context.request_id
    invalid = replace(call(str(uuid.uuid4())),
        context=CallContext(str(uuid.uuid4()), TrustedScope('owner', ('other-kb',))))
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_SCOPE_DENIED'): run(gate.authorize(invalid))
    with pytest.raises(ProviderError, match='LOCAL_EXECUTION_SCOPE_DENIED'): run(gate.authorize(call('')))
    assert gate.calls == 1 and len(data(ledger)['entries']) == 1
