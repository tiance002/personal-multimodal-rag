"""Zero-send contracts for isolated ordinary API acceptance; no DB or Provider."""
import asyncio
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest

from scripts import s7_graph_api_check as acceptance
from backend.weknora_port.model_registry import RoleSnapshot
from backend.weknora_port.storage.configuration import ConfiguredRole


def test_formal_templates_read_actual_trusted_assets_with_current_manifest():
    from backend.weknora_port.conversation_configuration import source_prompt_templates
    source_prompt_templates.cache_clear()
    templates = source_prompt_templates()
    assert len(templates['system_prompt']) > 0
    assert templates['system_prompt'][0]['content'].startswith('你是 EviRoute RAG')


def test_sse_error_and_complete_failed_are_not_reported_as_execution_success():
    sse = ('event: message\ndata: ' + json.dumps({'response_type': 'error', 'done': True,
        'data': {'error_code': 'CONVERSATION_TEMPLATE_ASSET_MISMATCH', 'status': 'failed'}}) + '\n\n'
        'event: message\ndata: ' + json.dumps({'response_type': 'complete', 'data': {'status': 'failed'}}) + '\n\n')
    history = [{'role': 'assistant', 'content': '', 'is_completed': False, 'turn_status': 'error',
                'turn_error_code': 'CONVERSATION_TEMPLATE_ASSET_MISMATCH'}]
    actual = acceptance.classify_question(sse, history)
    assert actual['status'] == 'FAILED_ASSISTANT_TURN'
    assert actual['sse_complete_present'] is True and actual['assistant_nonempty'] is False
    assert 'CONVERSATION_TEMPLATE_ASSET_MISMATCH' in actual['error_codes']


def test_empty_or_incomplete_sse_is_not_success_but_completed_answer_is_reviewable():
    complete = 'event: message\ndata: {"response_type":"complete","data":{}}\n\n'
    history = [{'role': 'assistant', 'content': 'Source-supported text', 'is_completed': True,
                'turn_status': 'completed', 'turn_error_code': '', 'finish_reason': 'stop'}]
    assert acceptance.classify_question('', history)['status'] == 'INCOMPLETE_QUESTION'
    assert acceptance.classify_question(complete, [])['status'] == 'INCOMPLETE_QUESTION'
    assert acceptance.classify_question(complete, [dict(history[0], content='  ')])['status'] == 'EMPTY_ASSISTANT_ANSWER'
    assert acceptance.classify_question(complete, history)['status'] == 'EXECUTED_REQUIRES_EVIDENCE_REVIEW'


def test_cheap_overlay_keeps_native_snapshot_gate_dynamic_and_other_roles():
    cheap = ConfiguredRole('cheap', 'siliconflow', 'Xing', 'https://example.invalid')
    expensive = ConfiguredRole('expensive', 'deepseek', 'configured-expensive', 'https://example.invalid')
    candidate = ConfiguredRole('cheap', 'siliconflow', acceptance.QWEN, 'https://example.invalid')
    old = RoleSnapshot(17, (('cheap', 'old-cheap'), ('expensive', 'existing-expensive')),
                       (cheap, expensive), (('cheap', 'old-error'), ('vlm', 'keep-error')))
    calls, gate, dynamic = [], object(), object()

    async def resolve(model_id):
        assert model_id == acceptance.QWEN_ID
        return candidate

    async def native(guard):
        calls.append(guard)
        return old

    runtime = SimpleNamespace(models=SimpleNamespace(resolve=resolve), _snapshot=native,
                              gate=gate, dynamic=dynamic)

    async def check():
        metadata = await acceptance.overlay_cheap(runtime)
        changed = await runtime._snapshot('verified-lease')
        assert calls == ['verified-lease']
        assert changed.revision == 17 and changed.model_id('cheap') == acceptance.QWEN_ID
        assert changed.model_id('expensive') == 'existing-expensive'
        assert changed.configurations == (candidate, expensive)
        assert changed.credential_errors == (('vlm', 'keep-error'),)
        assert runtime.gate is gate and runtime.dynamic is dynamic
        assert old.model_id('cheap') == 'old-cheap'
        assert metadata['database_role_write'] is False
    asyncio.run(check())


def test_frozen_question_uses_uuid36_and_formal_message_contract_no_gold():
    from backend.weknora_port.http_api import MessageCreate
    frozen = {'id': 'franklin-mixed', 'request': {'content': 'Original engineering question?',
        'knowledge_base_scope': ['kb'], 'document_scope': [], 'mode': 'quick', 'reasoning_effort': 'off'},
        'scoring_only': {'answer': 'do not send'}}
    body = acceptance.question(frozen, 'kb')
    parsed = MessageCreate.model_validate(body)
    assert str(uuid.UUID(parsed.request_id)) == parsed.request_id and len(parsed.request_id) == 36
    assert parsed.summary_model_id == acceptance.QWEN_ID and parsed.reasoning_effort == 'off'
    assert 'do not send' not in json.dumps(body)
    assert 'request_id' not in frozen['request']
    frozen['request']['document_scope'] = ['gold-only-doc']
    with pytest.raises(ValueError, match='S7_API_CASE_SCOPE_INVALID'):
        acceptance.question(frozen, 'kb')


def test_real_formal_api_write_origin_and_guard_all_business_routes():
    from scripts.m4_tenant332_api import BearerTokenGuard
    from backend.weknora_port.http_api import create_app
    principal, calls = object(), []

    class Runtime:
        async def update_knowledge_base_consent(self, who, kb, allowed):
            assert who is principal
            calls.append((kb, allowed))
            return {'id': kb, 'cloud_allowed': allowed}

    async def check():
        token = 'ephemeral-token-not-persisted-123456'
        api = create_app(Runtime(), resolve_principal=lambda _: principal,
                         allowed_origins=('http://127.0.0.1:56426',), manage_background_workers=False)
        app = BearerTokenGuard(api, token, 9_999_999_999)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:56789') as client:
            path = '/api/v1/knowledge-bases/kb/external-processing'
            assert (await client.patch(path, json={'cloud_allowed': True})).status_code == 401
            assert (await client.get('/api/v1/sessions/s/messages', headers={'Authorization': 'Bearer wrong'})).status_code == 401
            headers = {'Authorization': 'Bearer ' + token}
            assert (await client.patch(path, json={'cloud_allowed': True}, headers=headers)).status_code == 403
            assert not calls
            headers['Origin'] = 'http://127.0.0.1:56426'
            assert (await client.patch(path, json={'cloud_allowed': True}, headers=headers)).status_code == 200
            assert calls == [('kb', True)]
    asyncio.run(check())


def test_owned_process_graceful_stop_requires_no_inflight_and_no_new_send(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text(json.dumps({'entries': []}), encoding='utf-8')
    actions = []

    class Process:
        def poll(self):
            return None
        def wait(self, timeout):
            actions.append(timeout)
        def terminate(self):
            pytest.fail('Graceful private-file stop should be used')

    result = asyncio.run(acceptance.stop_owned_api(Process(), ledger, tmp_path))
    assert result == {'process_stopped': True} and actions == [20]
    assert json.loads((tmp_path / 'stop.request').read_text()) == {'no_inflight_at_request': True}
    assert json.loads(ledger.read_text()) == {'entries': []}


def test_inflight_owned_child_is_not_killed_and_phase_stops(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from backend.weknora_port.execution import LocalExecution
    ledger = tmp_path / 'ledger.json'
    ledger.write_text(json.dumps({'entries': [{'status': 'INFLIGHT'}]}), encoding='utf-8')
    current = {'status': 'OPEN'}

    @contextmanager
    def locked(self):
        yield current
    monkeypatch.setattr(LocalExecution, '__init__', lambda self, path: None)
    monkeypatch.setattr(LocalExecution, '_ledger', locked)

    class Process:
        pid = 123
        def poll(self):
            return None
        def wait(self, timeout):
            pytest.fail('Inflight process must remain alive')
        def terminate(self):
            pytest.fail('Inflight process must remain alive')

    result = asyncio.run(acceptance.stop_owned_api(Process(), ledger, tmp_path))
    assert result == {'process_kept_alive_due_inflight': 123}
    assert current['status'] == 'STOP' and not (tmp_path / 'stop.request').exists()
