import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import multiprocessing
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
    async def finish(self, call, **kwargs): pass


def call(identity='run/1'):
    return PreparedCall(CallContext(identity, TrustedScope('owner', ('kb',))),
        'provider', 'model', 'm1', 'chat', 'https://example.invalid', 'a'*64, 100, 50)


@pytest.fixture
def ledger():
    root = Path(__file__).resolve().parents[2] / 'var'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as folder:
        path = Path(folder) / 'owner.json'
        path.write_text(json.dumps({'status': 'OPEN', 'tenant_id': 1, 'subject_id': 'owner',
            'allowed_kbs': ['kb'], 'allowed_models': [{'provider': 'provider', 'model': 'model', 'capability': 'chat'}],
            'allowed_request_prefixes': ['run/'], 'max_calls': 12, 'max_tokens': 100000, 'entries': []}))
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
    state = SimpleNamespace(search_results=[], rerank_results=[], merge_results=[],
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


@pytest.mark.parametrize('alter', ['tenant', 'owner', 'scope', 'origin', 'model', 'prefix', 'closed'])
def test_scope_before_original(ledger, alter):
    gate = LocalExecution(ledger).install_gate(Gate())
    c = call()
    if alter == 'tenant': gate.application.principal.tenant_id = 2
    if alter == 'owner': c = replace(c, context=replace(c.context, scope=TrustedScope('other', ('kb',))))
    if alter == 'scope': c = replace(c, context=replace(c.context, scope=TrustedScope('owner', ('other',))))
    if alter == 'origin': c = replace(c, context=replace(c.context, required_origin_kbs=('other',)))
    if alter == 'model': c = replace(c, model='other')
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


def test_restart_with_inflight_stops_new_calls(ledger):
    gate = LocalExecution(ledger).install_gate(Gate())
    run(gate.authorize(call()))
    restarted = LocalExecution(ledger).install_gate(Gate())
    assert data(ledger)['status'] == 'STOP'
    with pytest.raises(ProviderError): run(restarted.authorize(call('run/2')))
    assert restarted.calls == 0


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
    config = SimpleNamespace(provider='provider', model='model', model_id='m1', custom_headers={'Authorization': 'secret'})
    async def consume():
        async with model.transport.open(config, '/', {'prompt': 'hello', 'api_key': 'secret', 'nested': {'Authorization': 'secret'}}, call().context, capability='chat') as handle:
            if stream: return b''.join([chunk async for chunk in handle.response.aiter_bytes()])
            return await handle.response.aread()
    assert run(consume()) == raw
    assert next(fence.evidence.glob('*.response.body')).read_bytes() == raw
    request = next(fence.evidence.glob('*.request.json')).read_text()
    assert 'secret' not in request and 'Authorization' not in request
    assert json.loads(next(fence.evidence.glob('*.response.json')).read_text())['complete'] is True
