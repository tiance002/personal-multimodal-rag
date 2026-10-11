"""Offline policy/native-claim meeting; all provider responses are SIMULATED."""
import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.tests.test_v6_xing_generation_binding import (
    counter, no_live_sender, setup as xing_setup, invoke as invoke_xing, PUBLIC_SCOPE, NOW, DEADLINE,
    calibration, invoke_calibration, XING_CALIBRATION_REQUEST_ID,
)
from backend.tests.test_p5_router_generation import make_chain, invoke as invoke_chain, GOOD
from backend.tests.test_v6_generation_binding import setup_binding as deepseek_setup, envelope
from backend.app.application.rule_router_v1 import GenerationRole, RouterPolicy
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT
from backend.app.domain.scope import Scope
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.ports.providers import ProviderUnavailable
from eval_center.paired_state import (
    register_run, begin_attempt, SQLiteProviderSendClaims, CurrentProviderSendClaims,
    provider_send_claim_scope, provider_send_claim_record,
)


def claims(tmp_path, arm='CHEAP_ONLY'):
    path = tmp_path / 'existing-center.sqlite3'
    register_run(path, run_id='SIMULATED-meet', cohort_sha256='a' * 64,
        source_manifest_sha256='b' * 64, code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',
        deadline_utc=DEADLINE, now_utc=NOW.isoformat(), expected_qids=['SIMULATED-q01'])
    begin_attempt(path, run_id='SIMULATED-meet', qid='SIMULATED-q01', arm=arm,
        input_identity_sha256='c' * 64, now_utc=NOW.isoformat())
    return SQLiteProviderSendClaims(path, 'SIMULATED-meet', 'SIMULATED-q01', arm, 'c' * 64), path


@pytest.mark.parametrize('purpose', ['answer', 'understand', 'rewrite', 'expansion'])
def test_native_current_claim_is_consumed_and_settled_once(counter, tmp_path, purpose):
    callback, path = claims(tmp_path)
    role, transport, ledger, wires, _ = xing_setup(counter, purposes=(purpose,))
    transport.send_claim_callback = CurrentProviderSendClaims()
    request_id = callback.request_id(purpose=purpose)
    with provider_send_claim_scope(callback):
        if purpose == 'answer':
            invoke_xing(role, request_id=request_id)
        else:
            role.invoke_auxiliary([{'role': 'user', 'content': 'SIMULATED public query'}],
                purpose=purpose, request_id=request_id, run_id='SIMULATED-meet', scope=PUBLIC_SCOPE,
                timeout_seconds=10, max_tokens=256)
    row = provider_send_claim_record(path, request_id=request_id)
    assert row['status'] == 'simulated' and row['purpose'] == purpose
    saved = row['result']['dispatch_contract']
    assert saved['wire_request_sha256'] == role.gateway.receipts[0]['wire_request_sha256']
    assert saved['scope_sha256'] == role.gateway.receipts[0]['scope_sha256']
    assert saved['purpose'] == purpose and saved['model'] == 'XingChenAGI/Xing4.0-29B'
    assert len(wires) == len(ledger.reservations) == 1
    with provider_send_claim_scope(callback):
        with pytest.raises(ProviderRequestNotSent):
            if purpose == 'answer':
                invoke_xing(role, request_id=request_id)
            else:
                role.invoke_auxiliary([{'role': 'user', 'content': 'SIMULATED public query'}],
                    purpose=purpose, request_id=request_id, run_id='SIMULATED-meet', scope=PUBLIC_SCOPE,
                    timeout_seconds=10, max_tokens=256)
    assert len(wires) == 1
    assert provider_send_claim_record(path, request_id=request_id) == row


def test_native_current_claim_without_bound_scope_is_zero_send(counter):
    role, transport, ledger, wires, _ = xing_setup(counter)
    transport.send_claim_callback = CurrentProviderSendClaims()
    with pytest.raises(ProviderRequestNotSent, match='XING_DURABLE_CLAIM_DENIED'):
        invoke_xing(role)
    assert not wires
    assert next(iter(ledger.reservations.values()))['state'] == 'released'


def test_deadline_rechecked_after_successful_claim_wait(counter, tmp_path):
    callback, path = claims(tmp_path)
    role, transport, ledger, wires, _ = xing_setup(counter)
    class DelayedClaim:
        def __call__(self, **kwargs):
            claim = callback(**kwargs)
            transport.clock = lambda: datetime(2026, 10, 10, 4, 25, 40, tzinfo=timezone.utc)
            return claim
        def settle(self, **kwargs):
            callback.settle(**kwargs)
        def check_before_send(self, **kwargs):
            callback.check_before_send(**kwargs)
    transport.send_claim_callback = DelayedClaim()
    request_id = callback.request_id(purpose='answer')
    with pytest.raises(ProviderRequestNotSent, match='XING_DEADLINE_OR_TIMEOUT_INVALID'):
        invoke_xing(role, request_id=request_id)
    assert not wires
    assert next(iter(ledger.reservations.values()))['state'] == 'released'
    assert provider_send_claim_record(path, request_id=request_id)['status'] == 'blocked'


def test_boolean_claim_receipt_cannot_authorize_wire(counter):
    role, transport, ledger, wires, _ = xing_setup(counter)
    class BadClaim:
        def __call__(self, **kwargs): return True
        def settle(self, **kwargs): pytest.fail('no valid owned claim')
        def check_before_send(self, **kwargs): pytest.fail('no valid owned claim')
    transport.send_claim_callback = BadClaim()
    with pytest.raises(ProviderRequestNotSent, match='XING_DURABLE_CLAIM_DENIED'):
        invoke_xing(role)
    assert not wires


def fixed(mode):
    return RouterPolicy(mode=mode, quality_escalation=False)


def test_real_cheap_policy_admission_is_typed_and_does_not_send(counter):
    role, transport, ledger, wires, _ = xing_setup(counter, execution='REAL')
    transport.send_claim_callback = CurrentProviderSendClaims()
    chain = LangChainQuickChain(None, generation_roles={'chat_cheap': role})
    chain.validate_generation_policy(fixed('CHEAP_ONLY'))
    assert role.execution_kind == 'REAL'
    with pytest.raises(ProviderRequestNotSent, match='XING_HOSTED_CAPACITY_AND_THINKING_UNVERIFIED'):
        invoke_xing(role)
    assert not wires and not ledger.reservations


@pytest.mark.parametrize('mutation', ['no_binding', 'no_callback', 'fallback', 'threshold', 'purpose', 'model', 'expired', 'extended_deadline'])
def test_real_fixed_policy_rejects_unfrozen_or_mismatched_roles(counter, mutation):
    role, transport, ledger, wires, _ = xing_setup(counter, execution='REAL')
    transport.send_claim_callback = CurrentProviderSendClaims()
    policy = fixed('CHEAP_ONLY')
    if mutation == 'no_binding': role = replace(role, xing_binding=None)
    elif mutation == 'no_callback': transport.send_claim_callback = None
    elif mutation == 'fallback': policy = replace(policy, provider_fallback=True)
    elif mutation == 'threshold':
        from decimal import Decimal
        policy = replace(policy, threshold=Decimal('0.36'))
    elif mutation == 'purpose': object.__setattr__(role.xing_binding.grant, 'allowed_purposes', ('calibration',))
    elif mutation == 'model': role.gateway.spec = SimpleNamespace(model_id='deepseek-flash')
    elif mutation == 'expired': transport.clock = lambda: datetime(2026, 10, 11, tzinfo=timezone.utc)
    elif mutation == 'extended_deadline': object.__setattr__(role.xing_binding.grant, 'deadline_utc', '2050-01-01T00:00:00Z')
    chain = LangChainQuickChain(None, generation_roles={'chat_cheap': role})
    with pytest.raises(ProviderRequestNotSent): chain.validate_generation_policy(policy)
    assert not wires and not ledger.reservations


def test_real_expensive_policy_requires_exact_owner_and_server_scope(tmp_path, monkeypatch):
    role, gate, path, counter, guard, wires = deepseek_setup(tmp_path)
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener', lambda *a: pytest.fail('real send'))
    gate.grant = OWNER_V6_TOKEN_GRANT  # metadata-only fixture; no canonical access
    role.gateway.send_claim_callback = CurrentProviderSendClaims()
    role.gateway._simulated_transport = None
    role.gateway.execution_kind = 'REAL'
    binding = replace(role.v6_binding, grant=OWNER_V6_TOKEN_GRANT, server_scope=PUBLIC_SCOPE)
    real_role = replace(role, v6_binding=binding)
    chain = LangChainQuickChain(None, generation_roles={'chat_expensive': real_role})
    chain.validate_generation_policy(fixed('EXPENSIVE_ONLY'))
    assert real_role.execution_kind == 'REAL' and not wires and not guard.reservations
    chain.generation_roles['chat_expensive'] = replace(real_role, v6_binding=replace(binding, server_scope=None))
    with pytest.raises(ProviderRequestNotSent): chain.validate_generation_policy(fixed('EXPENSIVE_ONLY'))


def test_expensive_server_scope_bridge_is_exact_and_preserves_final_wire(tmp_path):
    role, gate, path, counter, guard, wires = deepseek_setup(tmp_path)
    role = replace(role, v6_binding=replace(role.v6_binding, server_scope=PUBLIC_SCOPE))
    assert role.invoke(envelope(), request_id='SIMULATED-server-bridge', run_id='SIMULATED',
        scope=PUBLIC_SCOPE) == 'SIMULATED answer'
    assert len(wires) == 1 and wires == counter.wires
    with pytest.raises(ProviderRequestNotSent, match='V6_EVALUATION_SCOPE_REQUIRED'):
        role.invoke(envelope(), request_id='SIMULATED-server-other', run_id='SIMULATED',
            scope=Scope.from_ids(['other']))
    assert len(wires) == 1


def test_quick_prepare_and_trace_use_transport_identity_not_simulated_bool(counter, monkeypatch):
    chain, _, _, _ = make_chain(monkeypatch, policy=fixed('CHEAP_ONLY'))
    from backend.tests.test_v6_xing_generation_binding import response
    role, _, ledger, wires, _ = xing_setup(counter, scope=Scope.from_ids(['kb']),
        result=response(choices=[{'message': {'content': GOOD}, 'finish_reason': 'stop'}]))
    assert role.simulated is False and role.execution_kind == 'SIMULATED'
    chain.generation_roles['chat_cheap'] = role
    result, metrics = invoke_chain(chain)
    assert result.error_code is None
    assert metrics['rule_router']['attempts'][0]['execution_kind'] == 'SIMULATED'
    assert len(wires) == 1


def test_backend_claim_boundary_has_no_evaluator_import():
    workspace = Path(__file__).resolve().parents[2]
    for name in ('backend/app/ports/provider_send_claims.py', 'backend/app/adapters/models/cloud.py',
                 'backend/app/adapters/models/deepseek.py'):
        tree = ast.parse((workspace / name).read_text(encoding='utf-8'))
        assert not any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith('eval_center')
                       for node in ast.walk(tree))


class ForwardClaims:
    def __init__(self, callback, before=None): self.callback, self.before = callback, before
    def __call__(self, **kwargs): return self.callback(**kwargs)
    def settle(self, **kwargs): self.callback.settle(**kwargs)
    def check_before_send(self, **kwargs):
        if self.before is not None: self.before()
        self.callback.check_before_send(**kwargs)


def expire_cohort_clock(monkeypatch):
    class ExpiredDatetime(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 10, 11, tzinfo=timezone.utc)
    monkeypatch.setattr('eval_center.paired_state.datetime', ExpiredDatetime)


@pytest.mark.parametrize('purpose', ['answer', 'understand', 'rewrite', 'expansion'])
def test_native_final_send_check_after_claim_expiry_is_zero_send(counter, tmp_path, monkeypatch, purpose):
    callback, path = claims(tmp_path)
    role, transport, ledger, wires, _ = xing_setup(counter, purposes=(purpose,))
    transport.send_claim_callback = ForwardClaims(callback, lambda: expire_cohort_clock(monkeypatch))
    request_id = callback.request_id(purpose=purpose)
    with pytest.raises(ProviderRequestNotSent, match='XING_DURABLE_SEND_CHECK_DENIED'):
        if purpose == 'answer':
            invoke_xing(role, request_id=request_id)
        else:
            role.invoke_auxiliary([{'role': 'user', 'content': 'SIMULATED public query'}],
                purpose=purpose, request_id=request_id, run_id='SIMULATED-meet', scope=PUBLIC_SCOPE,
                timeout_seconds=10, max_tokens=256)
    row = provider_send_claim_record(path, request_id=request_id)
    assert row['status'] == 'blocked' and not wires
    assert row['result']['provider_dispatch']['send_status'] == 'NOT_SENT'
    assert next(iter(ledger.reservations.values()))['state'] == 'released'
    with pytest.raises(ProviderRequestNotSent):
        invoke_xing(role, request_id=request_id)
    assert not wires and provider_send_claim_record(path, request_id=request_id) == row


def test_calibration_native_final_send_check_is_not_cached(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    callback = transport.send_claim_callback
    transport.send_claim_callback = ForwardClaims(callback, lambda: expire_cohort_clock(monkeypatch))
    with pytest.raises(ProviderRequestNotSent, match='XING_DURABLE_SEND_CHECK_DENIED'):
        invoke_calibration(gateway)
    assert not wires
    assert provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)['status'] == 'blocked'
    assert next(iter(ledger.reservations.values()))['state'] == 'released'


@pytest.mark.parametrize('result_kind', ['ok', 'timeout', 'bad_model', 'missing_usage', 'expired', 'finish_failure'])
def test_deepseek_native_child_final_check_settlement_and_no_replay(tmp_path, monkeypatch, result_kind):
    from backend.tests.test_v6_generation_binding import response, activity
    callback, path = claims(tmp_path, 'EXPENSIVE_ONLY')
    result = (TimeoutError('SIMULATED timeout') if result_kind == 'timeout' else
              response(model='other') if result_kind == 'bad_model' else
              response(usage={}) if result_kind == 'missing_usage' else None)
    role, gate, _, counter, guard, wires = deepseek_setup(tmp_path, result=result)
    checked = []
    def before():
        checked.append(len(wires))
        if result_kind == 'expired': expire_cohort_clock(monkeypatch)
    native = ForwardClaims(callback, before)
    if result_kind == 'finish_failure':
        native.settle = lambda **kwargs: (_ for _ in ()).throw(OSError('SIMULATED settle failure'))
    role.gateway.send_claim_callback = native
    assert role.gateway.transport.send_claim_callback is native
    request_id = callback.request_id(purpose='answer')
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener', lambda *a: pytest.fail('real send'))
    if result_kind == 'ok':
        assert role.invoke(envelope(), request_id=request_id, run_id='SIMULATED-meet',
            scope=gate.grant.scope) == 'SIMULATED answer'
    else:
        error = ProviderRequestNotSent if result_kind == 'expired' else ProviderUnavailable
        with pytest.raises(error):
            role.invoke(envelope(), request_id=request_id, run_id='SIMULATED-meet', scope=gate.grant.scope)
    row = provider_send_claim_record(path, request_id=request_id)
    assert checked == [0]
    if result_kind == 'expired':
        assert row['status'] == 'blocked' and not wires and guard.settlements[0][2] is False
        assert role.gateway.last_receipt['send_status'] == 'NOT_SENT'
    else:
        assert len(wires) == 1 and wires == counter.wires and guard.settlements[0][2] is True
        assert row['status'] == ('simulated' if result_kind == 'ok' else
                                'pending' if result_kind in ('missing_usage', 'finish_failure') else 'unknown')
    if row['result'] is not None:
        assert row['result']['dispatch_contract']['wire_request_sha256'] == role.gateway.last_receipt['wire_request_sha256']
    with pytest.raises(ProviderRequestNotSent):
        role.invoke(envelope(), request_id=request_id, run_id='SIMULATED-meet', scope=gate.grant.scope)
    assert provider_send_claim_record(path, request_id=request_id) == row
