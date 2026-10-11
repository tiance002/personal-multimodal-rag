"""SIMULATED provider, historical ledger and product SQL; never real API/DB."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pytest

from backend.app.application import validation_usd_budget as b
from backend.app.application.provider_usage import CanonicalTokenUsageGuard, BudgetUsageGuard
from backend.app.application.rule_router_v1 import GenerationRole, V6GenerationBinding
from backend.app.application.run_lifecycle import RunLifecycle
from backend.app.adapters.models.deepseek import SimulatedDeepSeekTransport
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from backend.app.ports.run_lifecycle import RunClaim, current_execution, current_attempt, execution_owner, LifecycleDenied
from backend.app.ports.v6_token_budget import V6CanonicalTokenBudgetBinding
from backend.tests.test_v6_token_grant import _ledger
from backend.tests.test_v6_token_grant import _budget
from backend.tests.test_v6_generation_binding import SimulatedCounter, envelope, response

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
SCOPE = Scope(frozenset({'SIMULATED-kb'}), frozenset({'SIMULATED-doc'}))


class SimulatedProductRepository:
    def __init__(self): self.receipts, self.fail_link = [], False
    def link_token_budget(self, receipt):
        if self.fail_link: raise LifecycleDenied('SIMULATED_LINK_DENIED')
        assert receipt.status == receipt.token_state == 'reserved'
        self.receipts.append(receipt)
    def assert_token_budget_current(self, receipt):
        assert self.receipts[-1] == receipt


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',
        lambda *a, **k: pytest.fail('REAL TRANSPORT FORBIDDEN'))


def setup(tmp_path, *, result=None, token_limit=1_000_000):
    path = _ledger(tmp_path)
    grant = replace(b.OWNER_V6_TOKEN_GRANT_R3, execution_kind='SIMULATED',
        baseline_ledger_sha256='SIMULATED', token_limit=token_limit)
    lease = b.V6ExecutionLease('SIMULATED-C3', (NOW-timedelta(minutes=1)).isoformat(),
        (NOW+timedelta(minutes=30)).isoformat(), None)
    gate = b.V6OwnerActivityGate(path, grant=grant, execution_lease=lease, clock=lambda: NOW)
    gate.initialize_grant()
    factory = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_expensive',))
    wires = []
    def send(request, timeout):
        wires.append(request.data)
        if isinstance(result, Exception): raise result
        return response() if result is None else result
    gateway = factory.build_v6_gateway(activity_gate=gate, gate_types=DEEPSEEK_GATE_TYPES,
        api_key='SIMULATED', transport=SimulatedDeepSeekTransport(send))
    typed = V6CanonicalTokenBudgetBinding(gate, grant, gate.authority, lease, str(path.resolve()), SCOPE)
    guard = factory.build_v6_token_usage_guard(gateway, typed)
    binding = V6GenerationBinding(grant, gate, SimulatedCounter(), server_scope=SCOPE, token_budget_binding=typed)
    role = GenerationRole('chat_expensive', gateway, usage_guard=guard, v6_binding=binding)
    return role, gate, path, wires


@contextmanager
def product_context(role, *, lost=False, owner='SIMULATED-owner', attempt='SIMULATED-product-attempt'):
    from threading import Event
    repo = SimulatedProductRepository()
    lifecycle = RunLifecycle(repo, object())
    claim = RunClaim('SIMULATED-run', owner, True, 'IN_PROGRESS')
    loss = Event()
    if lost: loss.set()
    a = current_execution.set((claim, lifecycle, loss, 'SIMULATED-session'))
    c = current_attempt.set(attempt)
    o = execution_owner.set((claim.run_id, claim.owner))
    try: yield repo, loss
    finally:
        current_execution.reset(a); current_attempt.reset(c); execution_owner.reset(o)


def invoke(role, request='SIMULATED-request'):
    return role.invoke(envelope(), request_id=request, run_id='SIMULATED-run', scope=SCOPE)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_typed_normal_gateway_uses_one_canonical_reserve_and_no_currency(tmp_path, monkeypatch):
    role, gate, path, wires = setup(tmp_path)
    before = read(path)
    calls = []
    reserve, finish = gate.reserve, gate.finish_with_usage
    def reserved(**kwargs): calls.append(('reserve', kwargs)); return reserve(**kwargs)
    def finished(*args): calls.append(('finish', args)); return finish(*args)
    monkeypatch.setattr(gate, 'reserve', reserved)
    monkeypatch.setattr(gate, 'finish_with_usage', finished)
    with product_context(role) as (repo, lost):
        assert invoke(role) == 'SIMULATED answer'
        assert len(repo.receipts) == 1
        receipt = repo.receipts[0]
        assert receipt.request_id == 'SIMULATED-request' and receipt.product_attempt_id == 'SIMULATED-product-attempt'
    after = read(path)
    row = after['v6_owner_activity']['attempts'][0]
    assert [name for name, _ in calls] == ['reserve', 'finish']
    assert len(wires) == 1 and row['attempt_id'] == receipt.native_token_attempt_id
    assert row['validation_tokens']['state'] == 'settled'
    assert gate._used(after) == 65584
    assert {k: v for k,v in before.items() if k != 'v6_owner_activity'} == {k:v for k,v in after.items() if k != 'v6_owner_activity'}
    assert role.gateway.last_receipt['native_token_attempt_id'] == receipt.native_token_attempt_id
    assert not hasattr(role.usage_guard, 'reserve') and not hasattr(role.usage_guard, 'settle')
    assert role.usage_guard._requests == {}


@pytest.mark.parametrize('change', ['gate','grant','authority','lease','ledger','scope','duck_guard'])
def test_wrong_typed_binding_never_reserves_or_sends(tmp_path, change):
    role, gate, path, wires = setup(tmp_path)
    original = read(path)
    binding = role.v6_binding.token_budget_binding
    changes = {'gate': {'activity_gate': object()}, 'grant': {'grant': replace(gate.grant)},
        'authority': {'authority': object()}, 'lease': {'execution_lease': replace(gate.execution_lease)},
        'ledger': {'ledger_identity': str(tmp_path/'alternate-ledger')},
        'scope': {'server_scope': Scope(frozenset({'foreign'}), frozenset())}}
    if change == 'duck_guard':
        role = replace(role, usage_guard=object())
    else:
        bad = replace(binding, **changes[change])
        role = replace(role, v6_binding=replace(role.v6_binding, token_budget_binding=bad))
    with product_context(role), pytest.raises((ProviderRequestNotSent, ValueError)):
        invoke(role)
    assert read(path) == original and wires == []


@pytest.mark.parametrize('kind', ['lost','missing_attempt','wrong_run','wrong_scope'])
def test_product_context_binding_denied_before_reserve(tmp_path, kind):
    role, gate, path, wires = setup(tmp_path)
    before = read(path)
    with product_context(role, lost=kind=='lost', attempt=None if kind=='missing_attempt' else 'attempt'):
        with pytest.raises((ProviderRequestNotSent, LifecycleDenied)):
            role.invoke(envelope(), request_id='SIMULATED-q', run_id='wrong' if kind=='wrong_run' else 'SIMULATED-run',
                scope=Scope(frozenset({'wrong'}), frozenset()) if kind=='wrong_scope' else SCOPE)
    assert read(path) == before and wires == []


def test_link_failure_releases_only_proven_not_sent_and_consumes_request(tmp_path):
    role, gate, path, wires = setup(tmp_path)
    with product_context(role) as (repo, _):
        repo.fail_link = True
        with pytest.raises(ProviderRequestNotSent, match='TOKEN_ATTEMPT_LINK_DENIED'): invoke(role)
        with pytest.raises(ProviderRequestNotSent, match='ALREADY_RESERVED'): invoke(role)
    rows = read(path)['v6_owner_activity']['attempts']
    assert len(rows) == 1 and rows[0]['status'] == 'not_sent' and rows[0]['validation_tokens']['state'] == 'released'
    assert wires == [] and gate._used(read(path)) == 65569


@pytest.mark.parametrize('result', [TimeoutError('SIMULATED'), response(usage={}),
    response(model='other-model'), response(usage={'prompt_tokens':999999,'completion_tokens':3})])
def test_unknown_and_overbound_are_retained_and_same_request_never_replayed(tmp_path, result):
    role, gate, path, wires = setup(tmp_path, result=result)
    with product_context(role):
        with pytest.raises(Exception): invoke(role)
        with pytest.raises(Exception): invoke(role)
    rows = read(path)['v6_owner_activity']['attempts']
    assert len(rows) == 1 and len(wires) == 1
    assert rows[0]['validation_tokens']['state'] == 'unknown'
    assert rows[0]['validation_tokens']['reserved_input'] > 0 and rows[0]['validation_tokens']['reserved_output'] == 512


def test_telemetry_failure_finishes_unknown_once_without_resend(tmp_path, monkeypatch):
    role, gate, path, wires = setup(tmp_path)
    def failed(**kwargs): raise RuntimeError('SIMULATED telemetry failure')
    monkeypatch.setattr('backend.app.adapters.models.deepseek.record_call', failed)
    with product_context(role):
        with pytest.raises(ProviderUnavailable): invoke(role)
    row = read(path)['v6_owner_activity']['attempts'][0]
    assert len(wires) == 1 and row['status'] == 'unknown' and row['validation_tokens']['state'] == 'unknown'


def test_native_cap_still_blocks_without_currency_reservation(tmp_path):
    role, gate, path, wires = setup(tmp_path, token_limit=65570)
    with product_context(role), pytest.raises(ProviderRequestNotSent, match='V6_TOKEN_CAP'):
        invoke(role)
    assert wires == [] and read(path)['v6_owner_activity']['attempts'] == []


def test_budget_receipt_drift_is_rejected_and_cannot_refund_unknown(tmp_path):
    role, gate, path, wires = setup(tmp_path, result=TimeoutError('SIMULATED'))
    with product_context(role), pytest.raises(ProviderUnavailable): invoke(role)
    row = read(path)['v6_owner_activity']['attempts'][0]
    budget = role.gateway.prepare_v6_request(envelope().prompt, 10, 512, grant=gate.grant,
        scope=gate.grant.scope, capacity_counter=role.v6_binding.capacity_counter).budget
    before = read(path)
    with pytest.raises(Exception):
        gate.token_receipt(row['attempt_id'], request_id='wrong', budget=budget)
    gate.finish(row['attempt_id'], 'not_sent')
    assert read(path) == before


@pytest.mark.parametrize('kind', ['lost', 'cancelled_fence'])
def test_final_product_fence_failure_releases_not_sent_without_dispatch(tmp_path, kind):
    role, gate, path, wires = setup(tmp_path)
    with product_context(role) as (repo, loss):
        original = repo.link_token_budget
        def linked(receipt):
            original(receipt)
            if kind == 'lost': loss.set()
        repo.link_token_budget = linked
        if kind == 'cancelled_fence':
            def failed(receipt): raise LifecycleDenied('SIMULATED_CANCELLED_FENCE')
            repo.assert_token_budget_current = failed
        with pytest.raises(ProviderRequestNotSent, match='PRODUCT_FENCE_DENIED'): invoke(role)
    row = read(path)['v6_owner_activity']['attempts'][0]
    assert row['status'] == 'not_sent' and row['validation_tokens']['state'] == 'released'
    assert wires == []


def test_existing_unknown_occupancy_and_month_change_do_not_reset_history(tmp_path):
    role, gate, path, wires = setup(tmp_path)
    prior_budget = _budget(gate)
    old_attempt = gate.reserve(request_id='SIMULATED-prior-unknown', budget=prior_budget)
    gate.finish_with_usage(old_attempt, 'unknown', {})
    occupied = 65569 + 100 + 8 + 512
    assert gate._used(read(path)) == occupied
    with product_context(role): assert invoke(role) == 'SIMULATED answer'
    assert gate._used(read(path)) == occupied + 15
    later = NOW + timedelta(days=32)
    lease = b.V6ExecutionLease('SIMULATED-explicit-new-lease', (later-timedelta(minutes=1)).isoformat(),
        (later+timedelta(minutes=20)).isoformat(), None)
    next_gate = b.V6OwnerActivityGate(path, grant=gate.grant, clock=lambda:later, execution_lease=lease)
    before = read(path)
    assert next_gate._used(before) == occupied + 15
    with pytest.raises(Exception, match='ALREADY_RESERVED'):
        next_gate.reserve(request_id='SIMULATED-prior-unknown', budget=prior_budget)
    assert read(path) == before
    assert before['calls_allowed_in_this_task'] is False and len(before['attempts']) == 53
