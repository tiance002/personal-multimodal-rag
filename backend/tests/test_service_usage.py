"""SQLite/SIMULATED quota mechanism; no PostgreSQL or provider calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select

from backend.app.adapters.postgres.service_usage import (
    PostgresServiceTokenLedger, ServiceQuotaDenied, metadata, calls, policies,
)
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.service_usage import ServiceUsageGuard
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.service_policy import ServiceModelPolicy


def policy(limit=100):
    return ServiceModelPolicy('SIMULATED-owner-policy', 'SIMULATED owner authorization',
        'deepseek', 'deepseek-flash', ('chat_expensive', 'vision'), limit, 1, 1)


@pytest.fixture
def ledger(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'quota.db'))
    metadata.create_all(engine)
    yield PostgresServiceTokenLedger(engine)
    engine.dispose()


def reserve(ledger, p, identity, i=30, o=10, role='chat_expensive'):
    return ledger.reserve(p, request_id=identity, role=role, scope_sha256='a' * 64,
                          wire_sha256='b' * 64, input_tokens=i, output_tokens=o)


def test_shared_cap_unknown_restart_and_no_duplicate_send(ledger):
    first = reserve(ledger, policy(), 'a')
    ledger.finish(first, None, sent=True)
    restarted = PostgresServiceTokenLedger(ledger.engine)
    second = reserve(restarted, policy(), 'b', role='vision')
    assert restarted.finish(second, {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}, sent=True) == 'settled'
    with pytest.raises(ServiceQuotaDenied, match='SERVICE_TOKEN_LIMIT_EXCEEDED'):
        reserve(restarted, policy(), 'c')
    with pytest.raises(ServiceQuotaDenied, match='SERVICE_REQUEST_ALREADY_RESERVED'):
        reserve(restarted, policy(), 'a')
    assert restarted.finish(first, None, sent=False) == 'unknown'


def test_eleven_free_requests_are_allowed_by_explicit_service_policy(ledger):
    p = ServiceModelPolicy('SIMULATED-free', 'SIMULATED owner free quote',
        'siliconflow', 'BAAI/bge-m3', ('embedding',), None, 0, 0, True)
    spec = ModelRegistry.frozen_defaults().select('embedding')
    budget = InMemoryBudgetGate(0)
    guard = ServiceUsageGuard(budget, ledger, spec, p)
    for n in range(11):
        with model_access('embedding', allowed=True):
            call = guard.reserve_prepared(request_id=str(n), scope=Scope.from_ids(['kb']),
                                         wire_sha256='c' * 64, input_tokens=2, output_tokens=0)
            guard.check_before_send(call)
        guard.settle(call, {'prompt_tokens': 2, 'completion_tokens': 0, 'total_tokens': 2}, sent=True)
    assert len(budget.reservations) == 11
    assert all(row['state'] == 'unknown' for row in budget.reservations.values())


def test_cap_cannot_be_enlarged_by_config_or_released_after_unknown(ledger):
    identity = reserve(ledger, policy(), 'first')
    ledger.finish(identity, None, sent=True)
    with pytest.raises(ServiceQuotaDenied, match='NEW_AUTHORIZATION'):
        reserve(ledger, replace(policy(), token_limit=101), 'second')


def test_usage_bound_breach_fences_other_roles_persistently(ledger):
    identity = reserve(ledger, policy(), 'first')
    waiting = reserve(ledger, policy(), 'waiting', role='vision')
    ledger.link_cost(waiting, 'SIMULATED-cost')
    with pytest.raises(ServiceQuotaDenied, match='EXCEEDED_RESERVATION'):
        ledger.finish(identity, {'prompt_tokens': 35, 'completion_tokens': 10, 'total_tokens': 45}, sent=True)
    with pytest.raises(ServiceQuotaDenied, match='SERVICE_POLICY_SUSPENDED'):
        reserve(PostgresServiceTokenLedger(ledger.engine), policy(), 'second', role='vision')
    with pytest.raises(ServiceQuotaDenied, match='SERVICE_DISPATCH_FENCE_DENIED'):
        ledger.check_before_send(policy(), waiting)


def test_parallel_reservations_cannot_oversubscribe(ledger):
    def attempt(n):
        try:
            reserve(ledger, policy(), str(n))
            return True
        except ServiceQuotaDenied:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(attempt, range(4)))
    assert sum(result) == 2


def test_scope_and_monthly_denials_do_not_send_or_consume_token_quota(ledger):
    p = ServiceModelPolicy('SIMULATED-paid', 'SIMULATED quote',
        'siliconflow', 'BAAI/bge-m3', ('embedding',), 100, 1_000_000, 0)
    guard = ServiceUsageGuard(InMemoryBudgetGate(0), ledger, ModelRegistry.frozen_defaults().select('embedding'), p)
    with pytest.raises(ValueError, match='EGRESS_DENIED'):
        guard.reserve_prepared(request_id='a', scope=Scope.from_ids(['kb']),
                               wire_sha256='c' * 64, input_tokens=2, output_tokens=0)
    with model_access('embedding', allowed=True), pytest.raises(Exception, match='MONTHLY_BUDGET_EXCEEDED'):
        guard.reserve_prepared(request_id='b', scope=Scope.from_ids(['kb']),
                               wire_sha256='c' * 64, input_tokens=2, output_tokens=0)
    with ledger.engine.connect() as connection:
        assert list(connection.execute(select(calls.c.state)).scalars()) == ['released']


@pytest.mark.parametrize('lost_lease', [False, True])
def test_normal_adapter_dispatch_and_ingestion_lease_fence(ledger, monkeypatch, lost_lease):
    """SIMULATED final wire dispatch through the ordinary adapter and ledger."""
    from backend.app.adapters.models.cloud import (
        EmbeddingAdmission, SiliconFlowEmbedding, SimulatedEmbeddingCounter,
    )
    from backend.app.adapters.models.service_capacity import ServiceCapacity
    from backend.app.domain.adaptive_chunking import ChunkingConfig
    from backend.app.ports.service_policy import ServiceModelBinding
    from backend.app.ports.providers import ProviderRequestNotSent

    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-NOT-A-KEY')
    spec = ModelRegistry.frozen_defaults().select('embedding')
    p = ServiceModelPolicy('SIMULATED-adapter', 'SIMULATED owner quote',
        spec.provider, spec.model_id, ('embedding',), None, 0, 0, True)
    admission = EmbeddingAdmission(SimulatedEmbeddingCounter(lambda text: 2),
        tokenizer_identity='SIMULATED', non_truncating_verified=True)
    capacity = ServiceCapacity(spec.provider, spec.model_id, admission, kind='embedding')
    guard = ServiceUsageGuard(InMemoryBudgetGate(0), ledger, spec, p)
    sent = []

    class SimulatedTransport:
        execution_kind = 'SIMULATED'

        def __call__(self, request, timeout):
            sent.append(request.data)
            return dict(model=spec.model_id, data=[dict(index=0, embedding=[1.0] * 1024)],
                usage=dict(prompt_tokens=2, completion_tokens=0, total_tokens=2))

    adapter = SiliconFlowEmbedding(spec, enabled=True, usage_guard=guard,
        chunking_index_identity=ChunkingConfig().identity,
        admission=admission, transport=SimulatedTransport(),
        service_binding=ServiceModelBinding(p, capacity, guard))
    scope = Scope.from_ids(['SIMULATED-kb'])
    if lost_lease:
        with model_access('embedding', allowed=True, scope=scope), pytest.raises(
                ProviderRequestNotSent, match='INGESTION_LEASE_LOST'):
            adapter.embed(['synthetic'], 1, batch_id='SIMULATED-lost', lease_check=lambda: False)
        assert sent == []
        with ledger.engine.connect() as connection:
            assert list(connection.execute(select(calls.c.state)).scalars()) == ['released']
    else:
        for n in range(11):
            with model_access('embedding', allowed=True, scope=scope):
                adapter.embed(['synthetic'], 1, batch_id=f'SIMULATED-{n}', lease_check=lambda: True)
        assert len(sent) == 11
        with ledger.engine.connect() as connection:
            assert list(connection.execute(select(calls.c.state)).scalars()) == ['settled'] * 11
