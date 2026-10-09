"""SIMULATED only: production provider/gate, fake HTTP, in-memory ledger."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.app.adapters.models.factory import ProviderFactory
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.providers import ProviderRequestNotSent
from scripts import verify_p4_rerank_once as once


def approval():
    return dict(approved=True, request_id=once.REQUEST_ID, provider='siliconflow', model=once.MODEL,
                request_body_bytes=once.BODY_BYTES, request_body_sha256=once.BODY_SHA256,
                return_documents=True, max_requests=1,
                timeout_seconds=30, retries=0, synthetic_only=True, stop_after_success=True,
                stop_after_failure=True, currency='CNY', max_cost='0',
                pricing_source=once.PRICING_SOURCE, free_model_price_verified=True,
                owner_call_authorized=True, account_initial_state='UNKNOWN',
                single_request_account_validation_authorized=True,
                pricing_verified_at=datetime.now(timezone.utc).isoformat(),
                owner_reference='SIMULATED_APPROVAL_NOT_REAL_AUTHORIZATION')


@pytest.fixture
def history(tmp_path):
    path = tmp_path/'original-receipt.json'
    # This explicitly named historical receipt contains no credentials/private text.
    path.write_bytes(once.HISTORY.read_bytes())
    return path


def factory(monkeypatch, outcome='ok', budget=10, enabled=True, credential=True,
            *, gate=None, previous=None):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-ONCE-KEY-SENTINEL')
    if not credential:
        monkeypatch.delenv('SILICONFLOW_API_KEY')
    if gate is None:
        gate = InMemoryBudgetGate(budget)
        previous = gate.reserve(run_id=None, provider='siliconflow', model_name=once.MODEL,
                                capability='rerank', estimate_microunits=1).reservation_id
        gate.mark_unknown(previous)
        for _ in range(2):
            old = gate.reserve(run_id=None, provider="siliconflow", model_name=once.MODEL,
                               capability="rerank", estimate_microunits=1).reservation_id
            gate.mark_unknown(old)
    elif previous not in gate.reservations:
        raise ValueError('SIMULATED_SHARED_HISTORY_REQUIRED')
    calls, opens, closes = [], [], []
    spec = ModelRegistry.frozen_defaults().select('rerank')
    guard = BudgetUsageGuard(gate, spec, 1)
    if outcome == 'settlement':
        def broken(_):
            raise RuntimeError('SIMULATED-ONCE-RAW-SENTINEL')
        gate.mark_unknown = broken
    ranker = ProviderFactory(ModelRegistry.frozen_defaults(),
        enabled_roles=['rerank'] if enabled else [], usage_guards={'rerank': guard}).build('rerank')
    def send(request, timeout):
        calls.append((hashlib.sha256(request.data).hexdigest(), len(request.data), timeout))
        assert request.data == once.BODY and timeout == 30
        if outcome == 'timeout':
            raise TimeoutError('SIMULATED-ONCE-RAW-SENTINEL')
        if outcome in {'http', 'http401', 'http403'}:
            code = {'http': 429, 'http401': 401, 'http403': 403}[outcome]
            raise HTTPError(request.full_url, code, 'SIMULATED-ONCE-RAW-SENTINEL', {}, None)
        if outcome == 'json':
            raise json.JSONDecodeError('SIMULATED-ONCE-RAW-SENTINEL', 'SIMULATED_CONTENT', 0)
        if outcome == 'invalid':
            return dict(results=[None, {}])
        if outcome == 'wrong_model':
            return dict(model='WRONG', results=[])
        result = dict(results=[dict(index=i, relevance_score=score, document={"text": once.DOCUMENTS[i]})
                               for i, score in enumerate((.9, .1))])
        if outcome == 'wrong_top':
            result['results'].reverse()
        return result  # Deliberately missing usage: always UNKNOWN, never zero cost.
    ranker.transport = send
    @contextmanager
    def open_factory():
        opens.append(True)
        try:
            yield ranker, {'verification': 'SIMULATED_NO_DB'}
        finally:
            closes.append(True)
    return open_factory, gate, previous, calls, opens, closes


def record(path):
    return json.loads((path/'live-receipt.json').read_text(encoding='utf-8'))


def test_exact_payload_and_default_no_entry(monkeypatch, capsys):
    assert len(once.LEGACY_BODY) == 227
    assert hashlib.sha256(once.LEGACY_BODY).hexdigest() == once.LEGACY_BODY_SHA256
    assert json.loads(once.BODY)["return_documents"] is True
    assert len(once.BODY) == 226 and hashlib.sha256(once.BODY).hexdigest() == once.BODY_SHA256
    monkeypatch.setattr(once, 'run_once', lambda *a, **k: pytest.fail('default entered live path'))
    assert once.main([]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'NOT_RUN'
    assert once.main(['--execute-owner-authorized-once']) == 1


@pytest.mark.parametrize('field,value', [
    ('return_documents', False), ('approved', False), ('model', 'other'), ('max_requests', 2), ('request_body_bytes', 228),
    ('request_body_sha256', '0'*64), ('timeout_seconds', 31), ('retries', 1),
    ('synthetic_only', False), ('stop_after_success', False), ('stop_after_failure', False),
    ('currency', 'USD'), ('max_cost', '1'), ('free_model_price_verified', False),
    ('owner_call_authorized', False), ('account_initial_state', True),
    ('single_request_account_validation_authorized', False),
    ('pricing_source', 'https://invalid.test'), ('owner_reference', ''), ('max_requests', True),
])
def test_exact_authorization_denies_before_factory(field, value, history, tmp_path):
    grant = approval()
    grant[field] = value
    path = tmp_path/'out'
    with pytest.raises(ValueError):
        once.run_simulated_once(grant, output=path, history=history,
                      factory=lambda: pytest.fail('unauthorized factory entry'))
    assert not path.exists()


@pytest.mark.parametrize('missing', [
    'return_documents', 'free_model_price_verified', 'pricing_source', 'pricing_verified_at',
    'owner_call_authorized', 'owner_reference', 'account_initial_state',
    'single_request_account_validation_authorized',
])
def test_missing_authorization_or_price_evidence_never_enters_factory(missing, history, tmp_path):
    grant = approval()
    del grant[missing]
    path = tmp_path/'out'
    with pytest.raises(ValueError):
        once.run_simulated_once(grant, output=path, history=history,
                      factory=lambda: pytest.fail('missing evidence entered factory'))
    assert not path.exists()


def test_legacy_combined_verified_flag_cannot_claim_unknown_account(history, tmp_path):
    grant = approval()
    for key in ('free_model_price_verified', 'owner_call_authorized', 'account_initial_state',
                'single_request_account_validation_authorized'):
        del grant[key]
    grant['free_model_and_account_verified'] = True
    with pytest.raises(ValueError):
        once.run_simulated_once(grant, output=tmp_path/'out', history=history,
                      factory=lambda: pytest.fail('legacy verification entered factory'))
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('delta', [timedelta(days=-2), timedelta(minutes=1)])
def test_current_price_required(delta):
    grant = approval()
    grant['pricing_verified_at'] = (datetime.now(timezone.utc)+delta).isoformat()
    with pytest.raises(ValueError, match='CURRENT_ZERO_PRICE'):
        once.validate_approval(grant)


def test_history_change_denies_without_factory(history, tmp_path):
    history.write_bytes(history.read_bytes()+b' ')
    with pytest.raises(ValueError, match='HISTORICAL_RECEIPT_CHANGED'):
        once.run_simulated_once(approval(), output=tmp_path/'out', history=history,
                      factory=lambda: pytest.fail('changed history entered factory'))


def test_success_one_send_no_joint_pipeline_or_refund(monkeypatch, history, tmp_path):
    open_factory, gate, previous, calls, opens, closes = factory(monkeypatch)
    historical = history.read_bytes()
    path = tmp_path/'out'
    assert once.run_simulated_once(approval(), output=path, history=history, factory=open_factory) == 0
    receipt = record(path)
    assert receipt['status'] == 'PROBE_PASS' and receipt['actual_cost'] == 'UNKNOWN'
    assert receipt['returned_count'] == 2 and receipt['scores'] == [.9, .1]
    assert receipt['request_id'] == once.REQUEST_ID
    assert receipt['usage_actual'] is None and len(receipt['approval_sha256']) == 64
    assert receipt['account_initial_state'] == 'UNKNOWN'
    assert receipt['authorization'] == dict(free_model_price_verified=True,
        owner_call_authorized=True, account_initial_state='UNKNOWN',
        single_request_account_validation_authorized=True)
    assert receipt['receipts'][0]['settlement'] == 'UNKNOWN'
    assert receipt['cumulative_attempts'] == 4 and receipt['cumulative_body_bytes'] == 907
    assert receipt['real_postgres_pipeline'] == 'NOT_RUN'
    assert len(calls) == len(opens) == len(closes) == 1
    assert len(gate.reservations) == 4 and all(r['state'] == 'unknown' for r in gate.reservations.values())
    assert gate.reservations[previous]['reserved_microunits'] == 1
    assert history.read_bytes() == historical
    saved = (path/'live-receipt.json').read_bytes()
    with pytest.raises(FileExistsError):
        once.run_simulated_once(approval(), output=path, history=history, factory=open_factory)
    assert (path/'live-receipt.json').read_bytes() == saved and len(calls) == 1


@pytest.mark.parametrize('outcome', ['timeout', 'http', 'http401', 'http403', 'json', 'invalid',
                                     'wrong_model', 'wrong_top', 'settlement'])
def test_failure_stops_after_one_and_retains_unknown(monkeypatch, outcome, history, tmp_path):
    open_factory, gate, previous, calls, opens, closes = factory(monkeypatch, outcome)
    path = tmp_path/'out'
    old_rows = {key: dict(row) for key, row in gate.reservations.items()}
    assert once.run_simulated_once(approval(), output=path, history=history, factory=open_factory) == 1
    receipt = record(path)
    assert receipt['status'] == 'BLOCKED' and len(receipt['attempts']) == 1
    assert len(calls) == len(opens) == len(closes) == 1
    assert gate.reservations[previous]['state'] == 'unknown'
    new = [r for id, r in gate.reservations.items() if id not in old_rows]
    assert all(gate.reservations[key] == row for key, row in old_rows.items())
    assert len(new) == 1 and new[0]['state'] == ('reserved' if outcome == 'settlement' else 'unknown')
    assert 'SIMULATED-ONCE-' not in json.dumps(receipt)
    assert receipt['actual_cost'] == 'UNKNOWN' and receipt['real_postgres_pipeline'] == 'NOT_RUN'
    if outcome in {'http401', 'http403'}:
        assert receipt['receipts'][0]['status'] == 'http_' + outcome[4:]
        assert receipt['receipts'][0]['error_code'] == 'HTTP_' + outcome[4:]
    saved = (path/'live-receipt.json').read_bytes()
    with pytest.raises(FileExistsError):
        once.run_simulated_once(approval(), output=path, history=history, factory=open_factory)
    assert (path/'live-receipt.json').read_bytes() == saved and len(calls) == 1


@pytest.mark.parametrize('kind', ['budget', 'egress', 'credential'])
def test_normal_gates_block_with_zero_sends(monkeypatch, kind, history, tmp_path):
    open_factory, gate, previous, calls, _, _ = factory(monkeypatch,
        budget=3 if kind == 'budget' else 10, enabled=kind != 'egress', credential=kind != 'credential')
    path = tmp_path/'out'
    old_rows = {key: dict(row) for key, row in gate.reservations.items()}
    assert once.run_simulated_once(approval(), output=path, history=history, factory=open_factory) == 1
    assert not calls and not record(path)['attempts']
    assert len(gate.reservations) == 3 and gate.reservations[previous]['state'] == 'unknown'


@pytest.mark.parametrize('change', ['body', 'timeout', 'url'])
def test_exact_transport_rejects_without_send(change):
    saved, calls = [], []
    receipt = {'attempts': []}
    transport = once.OnceTransport(receipt, lambda: saved.append(True), lambda *a: calls.append(True))
    request = Request('https://api.siliconflow.cn/v1/rerank', data=once.BODY)
    timeout = 30
    if change == 'body': request.data += b' '
    if change == 'timeout': timeout = 29
    if change == 'url': request.full_url = 'https://invalid.test/rerank'
    with pytest.raises(ProviderRequestNotSent):
        transport(request, timeout)
    assert receipt['attempts'] == saved == calls == []


def test_second_transport_call_rejected_even_after_success():
    receipt, calls, saved = {'attempts': []}, [], []
    transport = once.OnceTransport(receipt, lambda: saved.append(True), lambda *a: calls.append(True))
    request = Request('https://api.siliconflow.cn/v1/rerank', data=once.BODY)
    transport(request, 30)
    with pytest.raises(ProviderRequestNotSent, match='ALREADY_CONSUMED'):
        transport(request, 30)
    assert len(receipt['attempts']) == len(calls) == len(saved) == 1


def test_receipt_save_failure_never_sends():
    calls = []
    def fail_save():
        raise OSError('SIMULATED_RECEIPT_FAILURE')
    transport = once.OnceTransport({'attempts': []}, fail_save, lambda *a: calls.append(True))
    request = Request('https://api.siliconflow.cn/v1/rerank', data=once.BODY)
    with pytest.raises(OSError): transport(request, 30)
    with pytest.raises(ProviderRequestNotSent): transport(request, 30)
    assert not calls
