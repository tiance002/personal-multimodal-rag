import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest


def ledger_file(tmp_path, *, enabled=True, limit=50):
    p = tmp_path / 'synthetic-ledger.json'
    p.write_text(json.dumps({'schema_version': 1, 'provider': 'deepseek',
        'authorized_limit': limit, 'consumed_attempts': 0, 'reserved_attempts': 0,
        'calls_allowed_in_this_task': enabled, 'attempts': []}), encoding='utf-8')
    return p


def test_persistent_gate_rejects_51st_attempt_across_instances(tmp_path):
    from backend.app.application.session_attempts import SessionAttemptGate, AttemptDenied
    p = ledger_file(tmp_path)
    for _ in range(50):
        SessionAttemptGate(p).reserve()
    with pytest.raises(AttemptDenied, match='SESSION_CALL_LIMIT'):
        SessionAttemptGate(p).reserve()
    assert json.loads(p.read_text())['consumed_attempts'] == 50


def test_concurrent_reservations_cannot_overspend(tmp_path):
    from backend.app.application.session_attempts import SessionAttemptGate, AttemptDenied
    p = ledger_file(tmp_path, limit=7)
    def reserve(_):
        try:
            SessionAttemptGate(p).reserve()
            return True
        except AttemptDenied:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(24)))
    assert sum(results) == 7
    data = json.loads(p.read_text())
    assert data['consumed_attempts'] == data['reserved_attempts'] == 7
    assert len({row['attempt_id'] for row in data['attempts']}) == 7


@pytest.mark.parametrize('mode', ['disabled', 'missing', 'corrupt', 'inconsistent'])
def test_invalid_or_disabled_ledger_fails_closed(tmp_path, mode):
    from backend.app.application.session_attempts import SessionAttemptGate, AttemptDenied
    p = ledger_file(tmp_path, enabled=mode != 'disabled')
    if mode == 'missing':
        p.unlink()
    elif mode == 'corrupt':
        p.write_text('{')
    elif mode == 'inconsistent':
        data = json.loads(p.read_text())
        data['consumed_attempts'] = 1
        p.write_text(json.dumps(data))
    with pytest.raises(AttemptDenied):
        SessionAttemptGate(p).reserve()


def test_failed_sent_attempt_is_never_refunded(tmp_path):
    from backend.app.application.session_attempts import SessionAttemptGate, AttemptDenied
    p = ledger_file(tmp_path, limit=1)
    gate = SessionAttemptGate(p)
    attempt = gate.reserve()
    gate.finish(attempt, 'unknown')
    with pytest.raises(AttemptDenied, match='SESSION_CALL_LIMIT'):
        gate.reserve()
    data = json.loads(p.read_text())
    assert data['consumed_attempts'] == 1
    assert data['reserved_attempts'] == 0
    assert data['attempts'][0]['status'] == 'unknown'


def test_backend_env_loader_preserves_process_values_and_never_loads_vite_secrets(tmp_path, monkeypatch):
    from backend.app.env_loader import load_backend_env
    p = tmp_path / '.env'
    p.write_text('RAG_PORT=18088\nDEEPSEEK_API_KEY="synthetic-only"\nVITE_DEEPSEEK_API_KEY=never\nLANGFUSE_BASE_URL=https://us.cloud.langfuse.com\n')
    monkeypatch.setenv('RAG_PORT', '19000')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('VITE_DEEPSEEK_API_KEY', raising=False)
    import os
    names = load_backend_env(p)
    assert os.environ['RAG_PORT'] == '19000'
    assert os.environ['DEEPSEEK_API_KEY'] == 'synthetic-only'
    assert 'VITE_DEEPSEEK_API_KEY' not in os.environ
    assert 'synthetic-only' not in repr(names)
    monkeypatch.delenv('DEEPSEEK_API_KEY')


def test_env_errors_do_not_echo_secret_values(tmp_path):
    from backend.app.env_loader import load_backend_env
    p = tmp_path / '.env'
    p.write_text('DEEPSEEK_API_KEY="synthetic-secret-without-closing-quote\n')
    with pytest.raises(ValueError) as result:
        load_backend_env(p)
    assert 'synthetic-secret' not in str(result.value)


class Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return json.dumps(self.payload).encode()


def gateway(tmp_path, monkeypatch, *, finish_reason='stop', error=None):
    from backend.app.adapters.models import deepseek
    from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
    from backend.app.application.session_attempts import SessionAttemptGate
    p = ledger_file(tmp_path)
    requests = []
    class Opener:
        def open(self, request, timeout):
            # Catch transport occurring before durable quota consumption.
            assert json.loads(p.read_text())['consumed_attempts'] == len(requests) + 1
            requests.append(request)
            if error:
                raise error
            payload = json.loads(request.data)
            assert payload['stream'] is False
            assert payload['max_tokens'] == 512
            assert payload['thinking'] == {'type': 'disabled'}
            return Response({'id': 'synthetic-response', 'model': 'deepseek-flash',
                'choices': [{'index': 0, 'finish_reason': finish_reason,
                             'message': {'role': 'assistant', 'content': 'Synthetic blue box [E1]'}}],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 6, 'total_tokens': 36}})
    monkeypatch.setattr(deepseek, 'build_opener', lambda *args: Opener())
    model = deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='synthetic-credential', model='deepseek-flash',
                                    cloud_enabled=True, attempt_gate=SessionAttemptGate(p))
    return model, p, requests


def test_adapter_requires_explicit_scope_authorization_before_counting(tmp_path, monkeypatch):
    from backend.app.ports.providers import ProviderUnavailable
    model, p, requests = gateway(tmp_path, monkeypatch)
    with pytest.raises(ProviderUnavailable, match='CLOUD_EGRESS_DISABLED'):
        model.answer('private question', 1)
    assert json.loads(p.read_text())['consumed_attempts'] == 0
    assert requests == []


def test_adapter_sends_one_bounded_request_and_records_actual_usage(tmp_path, monkeypatch):
    from backend.app.application.model_usage import capture_usage
    model, p, requests = gateway(tmp_path, monkeypatch)
    with capture_usage() as capture:
        answer = model.answer_with_budget('synthetic public prompt', 1, 512, cloud_authorized=True)
    assert answer == 'Synthetic blue box [E1]'
    assert len(requests) == 1
    assert json.loads(p.read_text())['consumed_attempts'] == 1
    assert capture.summary()['answer']['input_tokens'] == 30
    assert capture.summary()['answer']['output_tokens'] == 6


def test_transport_failure_is_sanitized_and_no_automatic_retry_occurs(tmp_path, monkeypatch):
    from backend.app.ports.providers import ProviderUnavailable
    model, p, requests = gateway(tmp_path, monkeypatch, error=RuntimeError('synthetic-credential sensitive body'))
    with pytest.raises(ProviderUnavailable) as result:
        model.answer_with_budget('synthetic', 1, 512, cloud_authorized=True)
    assert 'synthetic-credential' not in str(result.value)
    assert len(requests) == 1
    assert json.loads(p.read_text())['consumed_attempts'] == 1


def test_length_finish_is_rejected_without_releasing_the_attempt(tmp_path, monkeypatch):
    from backend.app.ports.providers import TruncatedAnswer
    model, p, requests = gateway(tmp_path, monkeypatch, finish_reason='length')
    with pytest.raises(TruncatedAnswer) as result:
        model.answer_with_budget('synthetic', 1, 512, cloud_authorized=True)
    assert str(result.value) == 'MODEL_OUTPUT_TRUNCATED'
    assert result.value.candidate == 'Synthetic blue box [E1]'
    assert json.loads(p.read_text())['consumed_attempts'] == 1


def test_adapter_gate_denial_prevents_transport(tmp_path, monkeypatch):
    from backend.app.ports.providers import ProviderUnavailable
    model, p, requests = gateway(tmp_path, monkeypatch)
    data = json.loads(p.read_text())
    data['calls_allowed_in_this_task'] = False
    p.write_text(json.dumps(data))
    with pytest.raises(ProviderUnavailable, match='SESSION_CALLS_DISABLED'):
        model.answer_with_budget('synthetic', 1, 512, cloud_authorized=True)
    assert requests == []


def test_quick_cloud_path_keeps_local_default_and_denies_private_scope(tmp_path, monkeypatch):
    from backend.app.application.budget import InMemoryBudgetGate
    from backend.app.application.knowledge_gateway import KnowledgeGateway
    from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
    from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
    from backend.app.domain.models import ChunkRecord
    from backend.app.domain.scope import Scope
    cloud, p, requests = gateway(tmp_path, monkeypatch)
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord('c1', 'kb', 'doc', 'ver', 'Synthetic blue box.', {'start': 0}))
    chain = LangChainQuickChain(KnowledgeGateway(HybridRetriever(repository)),
                               cloud_answer_gateway=cloud, budget_gate=InMemoryBudgetGate(100))
    scope = Scope.from_ids(['kb'])
    local = chain.invoke('Synthetic', scope)
    assert local.error_code is None
    assert requests == []
    denied = chain.invoke('Synthetic', scope, settings=QuickSettings(cloud_enabled=True, prefer_cloud=True))
    assert denied.error_code == 'CLOUD_EGRESS_DISABLED'
    assert requests == []
    allowed = chain.invoke('Synthetic', scope, settings=QuickSettings(cloud_enabled=True, prefer_cloud=True),
                           cloud_allowed_by_kb={'kb': True})
    assert allowed.error_code is None
    assert allowed.answer == 'Synthetic blue box [E1]'
    assert len(requests) == 1
    assert json.loads(p.read_text())['consumed_attempts'] == 1


def test_malformed_usage_never_breaks_settlement_or_leaks_errors(tmp_path, monkeypatch):
    from backend.app.adapters.models import deepseek
    from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
    from backend.app.ports.providers import ProviderUnavailable
    model, p, requests = gateway(tmp_path, monkeypatch)
    class Opener:
        def open(self, request, timeout):
            return Response({'choices': [], 'usage': ['synthetic-secret']})
    monkeypatch.setattr(deepseek, 'build_opener', lambda *args: Opener())
    with pytest.raises(ProviderUnavailable, match='DEEPSEEK_REQUEST_FAILED'):
        model.answer_with_budget('synthetic', 1, 512, cloud_authorized=True)
    data = json.loads(p.read_text())
    assert data['consumed_attempts'] == 1
    assert data['reserved_attempts'] == 0


def test_same_validation_request_cannot_be_sent_again_after_restart(tmp_path):
    from backend.app.application.session_attempts import SessionAttemptGate, AttemptDenied
    p = ledger_file(tmp_path)
    attempt = SessionAttemptGate(p).reserve(request_id='synthetic-smoke-01')
    SessionAttemptGate(p).finish(attempt, 'unknown')
    with pytest.raises(AttemptDenied, match='SESSION_REQUEST_ALREADY_RESERVED'):
        SessionAttemptGate(p).reserve(request_id='synthetic-smoke-01')
    assert json.loads(p.read_text())['consumed_attempts'] == 1


def test_separate_processes_continue_persistent_count(tmp_path):
    p = ledger_file(tmp_path, limit=2)
    script = ('import sys; from pathlib import Path; '
              'from backend.app.application.session_attempts import SessionAttemptGate; '
              'SessionAttemptGate(Path(sys.argv[1])).reserve()')
    for _ in range(2):
        completed = subprocess.run([sys.executable, '-c', script, str(p)], capture_output=True, timeout=15)
        assert completed.returncode == 0
    completed = subprocess.run([sys.executable, '-c', script, str(p)], capture_output=True, timeout=15)
    assert completed.returncode != 0
    assert b'SESSION_CALL_LIMIT' in completed.stderr
    assert json.loads(p.read_text())['consumed_attempts'] == 2


def test_ledger_cannot_raise_global_hard_cap(tmp_path):
    from backend.app.application.session_attempts import SessionAttemptGate, AttemptDenied
    p = ledger_file(tmp_path, limit=51)
    # Historical parsing no longer grants admission; a larger limit needs
    # separate reviewed authorization before any atomic reservation.
    with pytest.raises(AttemptDenied, match='SESSION_ATTEMPT_AUTHORIZATION_REQUIRED'):
        SessionAttemptGate(p).reserve()
    assert json.loads(p.read_text())['consumed_attempts'] == 0
