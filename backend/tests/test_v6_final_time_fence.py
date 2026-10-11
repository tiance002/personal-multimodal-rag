"""SIMULATED late blocking/cancellation boundaries; normal DS adapter, no services."""
import pytest

from backend.tests.test_v6_canonical_token_usage import setup, product_context, invoke, read
from backend.app.adapters.models.deepseek import SimulatedDeepSeekTransport
from backend.app.ports.providers import ProviderRequestNotSent


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',
        lambda *a, **k: pytest.fail('REAL TRANSPORT FORBIDDEN'))


def terminal_counts(gate, monkeypatch):
    calls = []
    for name in ('reserve', 'finish', 'finish_with_usage'):
        method = getattr(gate, name)
        def observed(*args, _name=name, _method=method, **kwargs):
            calls.append(_name)
            return _method(*args, **kwargs)
        monkeypatch.setattr(gate, name, observed)
    return calls


def test_normal_final_send_checks_follow_pg_and_send_settles_once(tmp_path, monkeypatch):
    role, gate, path, wires = setup(tmp_path)
    calls = terminal_counts(gate, monkeypatch)
    events = []
    before = gate.validate_before_send
    claim_check = role.gateway._check_send_claim
    profile_check = role.gateway._validate_v6_profile
    execution_check = role.usage_guard.check_current_execution
    send = role.gateway._simulated_transport.send
    def native(*args): events.append('native_gate'); return before(*args)
    def claim(*args): events.append('native_claim'); return claim_check(*args)
    def profile(*args, **kwargs):
        if kwargs.get('timeout_seconds') is not None: events.append('profile_and_timeout')
        return profile_check(*args, **kwargs)
    def execution(*args): events.append('cancel'); return execution_check(*args)
    def dispatched(*args): events.append('transport'); return send(*args)
    monkeypatch.setattr(gate, 'validate_before_send', native)
    monkeypatch.setattr(role.gateway, '_check_send_claim', claim)
    monkeypatch.setattr(role.gateway, '_validate_v6_profile', profile)
    monkeypatch.setattr(role.usage_guard, 'check_current_execution', execution)
    role.gateway._simulated_transport = SimulatedDeepSeekTransport(dispatched)
    with product_context(role) as (repo, _):
        repo.assert_token_budget_current = lambda receipt: events.append('pg')
        assert invoke(role, 'SIMULATED-final-order') == 'SIMULATED answer'
    assert events == ['pg', 'native_gate', 'native_claim', 'profile_and_timeout', 'cancel', 'transport']
    assert calls == ['reserve', 'finish_with_usage'] and len(wires) == 1
    row = read(path)['v6_owner_activity']['attempts'][0]
    assert row['status'] == 'ok' and row['validation_tokens']['state'] == 'settled'
    assert gate._used(read(path)) == 65584


@pytest.mark.parametrize('stage', ['pg', 'native_claim', 'profile'])
def test_lost_or_cancelled_execution_after_pg_cannot_reach_transport(tmp_path, monkeypatch, stage):
    role, gate, path, wires = setup(tmp_path)
    calls = terminal_counts(gate, monkeypatch)
    request_id = 'SIMULATED-late-loss-' + stage
    with product_context(role) as (repo, lost):
        if stage == 'pg':
            repo.assert_token_budget_current = lambda receipt: lost.set()
        elif stage == 'native_claim':
            original = role.gateway._check_send_claim
            def claim(*args): original(*args); lost.set()
            monkeypatch.setattr(role.gateway, '_check_send_claim', claim)
        else:
            original = role.gateway._validate_v6_profile
            def profile(*args, **kwargs):
                original(*args, **kwargs)
                if kwargs.get('timeout_seconds') is not None: lost.set()
            monkeypatch.setattr(role.gateway, '_validate_v6_profile', profile)
        with pytest.raises(ProviderRequestNotSent, match='V6_TOKEN_PRODUCT_FENCE_DENIED'):
            invoke(role, request_id)
        assert len(repo.receipts) == 1
    row = read(path)['v6_owner_activity']['attempts'][0]
    assert calls == ['reserve', 'finish'] and wires == []
    assert row['status'] == 'not_sent' and row['validation_tokens']['state'] == 'released'
    assert gate._used(read(path)) == 65569
    with product_context(role):
        with pytest.raises(ProviderRequestNotSent): invoke(role, request_id)
    assert len(read(path)['v6_owner_activity']['attempts']) == 1 and wires == []


def test_sent_timeout_keeps_unknown_bound_and_never_replays(tmp_path, monkeypatch):
    role, gate, path, wires = setup(tmp_path, result=TimeoutError('SIMULATED after send'))
    calls = terminal_counts(gate, monkeypatch)
    with product_context(role):
        with pytest.raises(Exception): invoke(role, 'SIMULATED-final-timeout')
    row = read(path)['v6_owner_activity']['attempts'][0]
    assert calls == ['reserve', 'finish_with_usage'] and len(wires) == 1
    assert row['status'] == 'unknown' and row['validation_tokens']['state'] == 'unknown'
    assert gate._used(read(path)) == 65569 + row['validation_tokens']['reserved_input'] + row['validation_tokens']['reserved_output']
    with product_context(role):
        with pytest.raises(ProviderRequestNotSent): invoke(role, 'SIMULATED-final-timeout')
    assert len(read(path)['v6_owner_activity']['attempts']) == 1 and len(wires) == 1
