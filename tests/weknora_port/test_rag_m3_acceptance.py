"""M3 execution safety contracts: offline only, no provider or database."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import rag_m3_acceptance as m3


def plan():
    return {'schema': 'rag-m3-plan/v1', 'cases': [
        {'case_id': 'first', 'tenant_id': 332, 'knowledge_base_id': 'kb', 'question': '原问题 0959', 'session_group': 'g', 'expected_answer': 'GOLD_SECRET'},
        {'case_id': 'next', 'tenant_id': 332, 'knowledge_base_id': 'kb', 'question': '它呢？', 'session_group': 'g', 'parent_case_id': 'first'}],
        'schedule': [{'case_id': 'first', 'variant': 'A'}, {'case_id': 'first', 'variant': 'B'},
                     {'case_id': 'next', 'variant': 'B'}, {'case_id': 'next', 'variant': 'A'}],
        'limits': dict(m3.MAXIMUM), 'admission': {'projected_tokens_per_case': 40000}}


def frozen(tmp_path, value):
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return m3.freeze_plan(path)


def test_freeze_preserves_real_history_order_and_excludes_gold(tmp_path):
    value = frozen(tmp_path, plan())
    assert value['cases'][0]['question'] == '原问题 0959'
    assert value['cases'][1]['parent_case_id'] == 'first'
    assert value['expected_tokens_per_case'] == 40000
    assert 'GOLD_SECRET' not in json.dumps(value)
    assert value['gold_loaded'] is False


@pytest.mark.parametrize('change', ['duplicate', 'missing-b', 'parent-before-first', 'cross-kb', 'shared-without-parent', 'limit'])
def test_invalid_or_unpaired_plans_cannot_execute(tmp_path, change):
    value = plan()
    if change == 'duplicate':
        value['schedule'].append(value['schedule'][0])
    elif change == 'missing-b':
        value['schedule'].pop()
    elif change == 'parent-before-first':
        value['schedule'].reverse()
    elif change == 'cross-kb':
        value['cases'][1]['knowledge_base_id'] = 'different'
    elif change == 'shared-without-parent':
        value['cases'][1].pop('parent_case_id')
    else:
        value['limits']['end_to_end'] = 25
    with pytest.raises(RuntimeError):
        frozen(tmp_path, value)


@pytest.mark.parametrize('usage,attempts,match', [
    ({'unknown_outcomes': 1}, 0, 'UNCERTAIN'), ({'missing_usage': 1}, 0, 'UNCERTAIN'),
    ({'provider_calls': 120}, 0, 'CAP'), ({'reported_tokens': 450000}, 0, 'CAP'),
    ({'reported_tokens': 410001}, 0, 'ALLOWANCE'), ({}, 24, 'ALLOWANCE')])
def test_cumulative_limits_and_unknown_are_terminal(usage, attempts, match):
    defaults = dict(provider_calls=0, reported_tokens=0, unknown_outcomes=0, missing_usage=0)
    defaults.update(usage)
    value = {'plan': {'limits': dict(m3.MAXIMUM), 'expected_tokens_per_case': 40000}, 'end_to_end_attempts': attempts}
    with pytest.raises(RuntimeError, match=match):
        m3.budget_check(value, defaults, new_case=True)


def test_sse_decoder_handles_utf8_and_crlf_splits_and_done():
    decoder = m3.SSEClock()
    raw = 'event: message\r\ndata: {"response_type":"answer","content":"中文"}\r\n\r\ndata: [DONE]\n\n'.encode()
    emitted = []
    for byte in raw:
        emitted += decoder.feed(bytes([byte]))
    assert emitted == [{'response_type': 'answer', 'content': '中文'}]
    assert decoder.pending == b'' and decoder.malformed == 0


def test_stream_saves_times_and_never_resends_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(m3, 'OUT', tmp_path)
    original_client = httpx.AsyncClient
    events = [{'id': 'run', 'response_type': 'progress', 'data': {'stage': 'understand', 'status': 'started'}},
              {'id': 'run', 'response_type': 'answer', 'content': '真实文本'},
              {'id': 'run', 'response_type': 'complete', 'data': {}}]
    raw = ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events).encode()
    sent = []
    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=raw, headers={'content-type': 'text/event-stream'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    body = {'request_id': 'one', 'content': 'question'}
    timing, _, answer, complete = asyncio.run(m3.stream_query('http://fixture.invalid/messages', body, 'query-one', tmp_path / 'one'))
    assert answer == '真实文本' and complete['id'] == 'run'
    assert timing['first_progress_seconds'] <= timing['first_answer_seconds'] <= timing['complete_seconds'] <= timing['post_seconds']
    assert (tmp_path / 'one' / 'answer.sse').read_bytes() == raw
    with pytest.raises(RuntimeError, match='NO_RETRY'):
        asyncio.run(m3.stream_query('http://fixture.invalid/messages', body, 'query-one', tmp_path / 'retry'))
    assert len(sent) == 1


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_terminal_sse_status_cannot_succeed_without_error_event(tmp_path, monkeypatch, status):
    monkeypatch.setattr(m3, 'OUT', tmp_path)
    original_client = httpx.AsyncClient
    raw = ('data: ' + json.dumps({'id': 'run', 'response_type': 'complete', 'data': {'status': status}}) + '\n\n').encode()
    sent = []
    def handle(request):
        sent.append(request)
        return httpx.Response(200, content=raw, headers={'content-type': 'text/event-stream'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(RuntimeError, match='FAILED_OR_CANCELLED_SSE'):
        asyncio.run(m3.stream_query('http://fixture.invalid/messages', {'request_id': 'one'}, 'terminal-query', tmp_path / 'terminal'))
    # A fully received terminal failure remains a known HTTP response, never UNKNOWN.
    operation = json.loads((tmp_path / 'operations.json').read_text())['terminal-query']
    assert operation['status'] == 'RESPONSE_RECEIVED' and len(sent) == 1
    assert (tmp_path / 'terminal' / 'answer.sse').read_bytes() == raw


@pytest.mark.parametrize('status', ['error', 'cancelled'])
def test_history_terminal_status_overrides_completed_flag(status):
    row = {'role': 'assistant', 'request_id': 'run', 'is_completed': True, 'content': 'same answer', 'turn_status': status}
    with pytest.raises(RuntimeError, match='FAILED_OR_CANCELLED_HISTORY'):
        m3.confirm_history({'data': [row]}, 'run', 'same answer')


@pytest.mark.parametrize('status', [None, 'pending', 'completed'])
def test_history_success_retains_pending_or_absent_status_compatibility(status):
    row = {'role': 'assistant', 'request_id': 'run', 'is_completed': True, 'content': 'same answer'}
    if status is not None:
        row['turn_status'] = status
    m3.confirm_history({'data': [row]}, 'run', 'same answer')


def test_server_gate_blocks_settled_unknown_even_in_same_session(tmp_path, monkeypatch):
    calls = []
    class Original:
        async def authorize(self, call):
            calls.append(call)
    value = {'cases': {'first-A': {'session_id': 's', 'status': 'STARTED_NO_RETRY'}}, 'plan': {'limits': dict(m3.MAXIMUM)}}
    monkeypatch.setattr(m3, 'state', lambda: value)
    async def usage(value):
        return {'calls': [{'outcome': 'UNKNOWN', 'session_id': 's', 'finished_at': 'settled'}], 'provider_calls': 1, 'reported_tokens': 0}
    monkeypatch.setattr(m3, 'usage_snapshot', usage)
    async def run():
        with pytest.raises(Exception, match='M3_EXPERIMENT_LIMIT_OR_UNKNOWN_STOP'):
            await m3.LimitedGate(Original(), asyncio.Lock()).authorize(SimpleNamespace(context=SimpleNamespace(session_id='s')))
    asyncio.run(run())
    assert calls == []


def test_passive_provider_timing_preserves_nonstream_response_and_omits_headers(tmp_path):
    from backend.weknora_port.models import Message, ChatOptions
    from tests.weknora_port.test_provider_transport import setup, response, context
    async def run():
        model, transport, gate = setup(lambda request: httpx.Response(200, json=response(model.config, '{"intent":"kb_search"}')))
        m3.install_timing(model, tmp_path)
        try:
            answer = await model.chat((Message('user', 'q'),), ChatOptions(max_tokens=150), context('rewrite'))
            assert answer.content == '{"intent":"kb_search"}'
            assert len(gate.calls) == len(gate.finishes) == 1
        finally:
            await transport.aclose()
    asyncio.run(run())
    record = json.loads(next((tmp_path / 'provider-timing').glob('*.json')).read_text())
    assert record['http_send_to_last_body_seconds'] >= 0
    assert record['first_valid_output_seconds'] is None
    assert record['effective_output_basis'] == 'NOT_AVAILABLE_NON_STREAM'
    assert 'SIMULATED-NOT-A-KEY' not in json.dumps(record)


def test_batch_is_bounded_and_keeps_exact_schedule_order(monkeypatch):
    schedule = [{'case_id': str(i), 'variant': 'A'} for i in range(10)]
    monkeypatch.setattr(m3, 'state', lambda: {'plan': {'schedule': schedule}, 'cases': {'old': {}}})
    calls = []
    async def query(case_id, variant):
        calls.append((case_id, variant))
    monkeypatch.setattr(m3, 'query_case', query)
    asyncio.run(m3.batch(4))
    assert calls == [('1', 'A'), ('2', 'A'), ('3', 'A'), ('4', 'A')]
    with pytest.raises(RuntimeError, match='BATCH_COUNT'):
        asyncio.run(m3.batch(20))


def test_gate_wrapper_preserves_runtime_identity_and_bound_authorize_once(monkeypatch):
    from backend.weknora_port.runtime import ApplicationRuntime
    from backend.weknora_port.models import provider_config
    value = {'cases': {'first-A': {'session_id': 's', 'status': 'STARTED_NO_RETRY'}}, 'plan': {'limits': dict(m3.MAXIMUM)}}
    monkeypatch.setattr(m3, 'state', lambda: value)
    async def usage(value):
        return {'calls': [], 'provider_calls': 0, 'reported_tokens': 0}
    monkeypatch.setattr(m3, 'usage_snapshot', usage)
    calls = []
    class Gate:
        async def authorize(self, call):
            calls.append(call)
        async def finish(self, call, **kwargs):
            pass
    async def run():
        gate, lock = Gate(), asyncio.Lock()
        runtime = object.__new__(ApplicationRuntime)
        async def factory(config, key, supplied_gate):
            m3.install_gate_limit(supplied_gate, lock)
            return SimpleNamespace(config=config, transport=SimpleNamespace(gate=supplied_gate))
        runtime.model_factory = factory
        cfg = provider_config('openai-compatible', 'fixture', base_url='https://fixture.invalid/v1')
        model = await runtime._make_model(cfg, 'SIMULATED', gate)
        assert model.transport.gate is gate
        # A second registry model must not double-wrap or recursively call itself.
        await runtime._make_model(cfg, 'SIMULATED', gate)
        await gate.authorize(SimpleNamespace(context=SimpleNamespace(session_id='s')))
    asyncio.run(run())
    assert len(calls) == 1


def test_recovery_preserves_failed_request_and_consumed_slot_without_http(tmp_path, monkeypatch):
    monkeypatch.setattr(m3, 'OUT', tmp_path)
    monkeypatch.setattr(m3, 'code_sha', lambda: 'baseline')
    monkeypatch.setattr(m3, 'source_fingerprint', lambda: 'source')
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text('{}')
    folder = tmp_path / 'cases' / 'scifact-339-A'
    folder.mkdir(parents=True)
    events = [{'id': 'failed-run', 'response_type': 'error', 'data': {'error_code': 'RUNTIME_DURABLE_GATE_REQUIRED'}},
              {'id': 'failed-run', 'response_type': 'complete', 'data': {'status': 'failed'}}]
    raw = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()
    (folder / 'answer.sse').write_bytes(raw)
    old_executor = b'original executor'
    old_sha = m3.base.sha(old_executor)
    (tmp_path / 'executor-revisions').mkdir()
    (tmp_path / 'executor-revisions' / (old_sha + '.py')).write_bytes(old_executor)
    m3.save(tmp_path / 'operations.json', {'query-scifact-339-A': {'status': 'RESPONSE_RECEIVED', 'response_sha256': m3.base.sha(raw)}})
    value = {'source_commit': 'baseline', 'source_fingerprint': 'source', 'executor_sha256': old_sha,
             'plan': {'path': str(plan_path), 'sha256': m3.base.sha(plan_path.read_bytes())},
             'processes': {'api': {'status': 'STOPPED'}}, 'end_to_end_attempts': 1,
             'cases': {'scifact-339-A': {'session_id': 'failed-session'}}, 'groups': {'g-A': 'failed-session'},
             'primary_tenant': {'subject_id': 'owner'}}
    monkeypatch.setattr(m3, 'state', lambda: value)
    async def usage(value):
        return {'provider_calls': 0, 'reported_tokens': 0, 'unknown_outcomes': 0, 'calls': []}
    monkeypatch.setattr(m3, 'usage_snapshot', usage)
    class DB:
        async def execute(self, sql, params):
            return self
        async def fetchone(self):
            return {'status': 'error', 'error_code': 'RUNTIME_DURABLE_GATE_REQUIRED'}
        async def close(self):
            pass
    async def pg():
        return DB()
    monkeypatch.setattr(m3.base, 'pg', pg)
    asyncio.run(m3.resume_observer_fix())
    assert value['cases'] == {} and value['end_to_end_attempts'] == 1
    assert value['failed_attempts'][0]['run_id'] == 'failed-run'
    assert value['attempt_numbers'] == {'scifact-339-A': 2}
    assert (folder / 'answer.sse').read_bytes() == raw
    assert (tmp_path / 'recovery' / 'first-failure-state.json').exists()
    assert plan_path.read_text() == '{}'
    with pytest.raises(RuntimeError, match='RECOVERY_ALREADY'):
        asyncio.run(m3.resume_observer_fix())
