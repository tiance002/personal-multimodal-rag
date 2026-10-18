"""Actual daily send gate + HTTP transport, with synthetic SQL and HTTP only."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import inspect
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.models.transport import AsyncHTTPTransport
from backend.weknora_port.models.types import (
    CallContext, ModelConfig, PreparedCall, ProviderError, TokenUsage, TrustedScope,
)
from backend.weknora_port.runtime import ApplicationRuntime, LeasedSendGate


class Rows:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class SyntheticSQL:
    """Closed SQL fixture: reject unknown statements; retain claims between gates."""
    def __init__(self):
        self.kbs = {'kb': True, 'origin': True}
        self.model_active = True
        self.claims = {}
        self.legacy = set()
        self.settlement = 'normal'

    @asynccontextmanager
    async def lease(self):
        yield SimpleNamespace(connection=self)

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, args=()):
        if sql.startswith('SELECT id,cloud_allowed FROM knowledge_bases '):
            tenant, ids = args
            return Rows([{'id': kb, 'cloud_allowed': self.kbs[kb]}
                         for kb in ids if tenant == 1 and kb in self.kbs])
        if sql.startswith('SELECT source FROM models '):
            assert "status='active'" in sql and 'deleted_at IS NULL' in sql
            return Rows([{'source': 'remote'}] if self.model_active and args == (1, 'owner', 'm1') else [])
        if sql.startswith('SELECT pg_advisory_xact_lock('):
            return Rows([])
        if sql == "SELECT to_regclass('port_call_slots') AS slots":
            return Rows([{'slots': 'port_call_slots'}])
        if sql == 'SELECT call_id FROM port_call_slots WHERE call_id=%s':
            return Rows([{'call_id': args[0]}] if args[0] in self.legacy else [])
        if sql.startswith('INSERT INTO port_provider_calls('):
            assert 'ON CONFLICT DO NOTHING RETURNING request_id' in sql
            tenant, owner, call_id, request, capability, session, provider, model, model_id, wire = args
            if call_id in self.claims:
                return Rows([])
            self.claims[call_id] = dict(tenant=tenant, owner=owner, request=request,
                capability=capability, wire=wire, outcome='UNKNOWN', finished=False)
            return Rows([{'request_id': request}])
        if sql.startswith('UPDATE port_provider_calls SET outcome='):
            assert sql.endswith('AND finished_at IS NULL RETURNING request_id')
            if self.settlement == 'error':
                raise RuntimeError('secret-api-key private-prompt vendor-body')
            if self.settlement == 'missing':
                return Rows([])
            outcome, usage, tenant, owner, request, capability, wire = args
            rows = []
            for row in self.claims.values():
                if (row['tenant'], row['owner'], row['request'], row['capability'], row['wire'], row['finished']) == (
                        tenant, owner, request, capability, wire, False):
                    row.update(outcome=outcome, finished=True, usage=usage)
                    rows.append({'request_id': request})
            return Rows(rows)
        raise AssertionError(f'Unexpected SQL: {sql}')


def application(db, *, enabled=None):
    default = inspect.signature(ApplicationRuntime).parameters['cloud_allowed'].default
    assert default is False
    return SimpleNamespace(leases=db, principal=SimpleNamespace(tenant_id=1, subject_id='owner'),
                           cloud_allowed=default if enabled is None else enabled)


def context(request='daily/1'):
    return CallContext(request, TrustedScope('owner', ('kb',)))


def prepared():
    return PreparedCall(context(), 'provider', 'model', 'm1', 'chat',
                        'https://example.invalid/chat/completions', 'a' * 64, 100, 50)


async def send(gate, sent, ctx=None, config=None, *, observed=True):
    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={'answer': 'synthetic'})
    transport = AsyncHTTPTransport(
        allowed_endpoints=['https://example.invalid/chat/completions'], gate=gate,
        credential=lambda _: 'secret-api-key', http_transport=httpx.MockTransport(respond))
    try:
        async with transport.open(config or ModelConfig('provider', 'model', 'm1', 'https://example.invalid', source='remote'),
                '/chat/completions', {'messages': [{'role': 'user', 'content': 'private-prompt'}], 'max_tokens': 50},
                ctx or context(), capability='chat') as handle:
            assert await handle.response.aread()
            if observed:
                handle.observed(TokenUsage(10, 5, 15, 'REPORTED'))
    finally:
        await transport.aclose()


def test_default_cloud_disabled_prevents_transport_send():
    db, sent = SyntheticSQL(), []
    with pytest.raises(ProviderError, match='MODEL_CLOUD_DISABLED'):
        asyncio.run(send(LeasedSendGate(application(db)), sent))
    assert sent == [] and db.claims == {}


@pytest.mark.parametrize('revoke', ['global', 'kb', 'origin'])
def test_explicit_consent_sends_then_revocation_blocks_next_request(revoke):
    db, sent = SyntheticSQL(), []
    app = application(db, enabled=True)
    gate = LeasedSendGate(app)
    asyncio.run(send(gate, sent))
    assert len(sent) == 1
    assert next(iter(db.claims.values()))['outcome'] == 'RESPONSE_RECEIVED'
    ctx = context('daily/2')
    if revoke == 'global':
        app.cloud_allowed = False
    else:
        db.kbs[revoke] = False
        if revoke == 'origin':
            ctx = replace(ctx, required_origin_kbs=('origin',))
    code = 'MODEL_CLOUD_DISABLED' if revoke == 'global' else 'MODEL_ORIGIN_KB_EGRESS_DENIED'
    with pytest.raises(ProviderError, match=code):
        asyncio.run(send(gate, sent, ctx))
    assert len(sent) == 1 and len(db.claims) == 1


@pytest.mark.parametrize('wrong', ['kb', 'owner', 'model', 'inactive_model'])
def test_scope_and_active_owner_model_checked_before_transport(wrong):
    db, sent, ctx = SyntheticSQL(), [], context()
    config = ModelConfig('provider', 'model', 'm1', 'https://example.invalid', source='remote')
    if wrong == 'kb':
        ctx = replace(ctx, scope=TrustedScope('owner', ('other',)))
    if wrong == 'owner':
        ctx = replace(ctx, scope=TrustedScope('other', ('kb',)))
    if wrong == 'model':
        config = replace(config, model_id='foreign-model')
    if wrong == 'inactive_model':
        db.model_active = False
    with pytest.raises(ProviderError):
        asyncio.run(send(LeasedSendGate(application(db, enabled=True)), sent, ctx, config))
    assert sent == [] and db.claims == {}


@pytest.mark.parametrize('prior', ['received', 'unknown', 'unsettled'])
def test_durable_duplicate_denied_after_gate_recreation_including_unknown(prior):
    db, sent = SyntheticSQL(), []
    if prior == 'unsettled':
        db.settlement = 'error'
    asyncio.run(send(LeasedSendGate(application(db, enabled=True)), sent, observed=prior == 'received'))
    row = next(iter(db.claims.values()))
    assert row['outcome'] == ('RESPONSE_RECEIVED' if prior == 'received' else 'UNKNOWN')
    assert row['finished'] is (prior != 'unsettled')
    with pytest.raises(ProviderError, match='MODEL_CALL_ID_REUSE_DENIED'):
        asyncio.run(send(LeasedSendGate(application(db, enabled=True)), sent))
    assert len(sent) == 1 and len(db.claims) == 1


@pytest.mark.parametrize('settlement', ['normal', 'missing', 'error', 'wrong_wire'])
def test_finish_requires_verified_updated_row_and_does_not_log_secrets(settlement, caplog):
    db = SyntheticSQL()
    gate, call = LeasedSendGate(application(db, enabled=True)), prepared()
    asyncio.run(gate.authorize(call))
    db.settlement = settlement
    finish_call = replace(call, wire_sha256='b' * 64) if settlement == 'wrong_wire' else call
    result = asyncio.run(gate.finish(finish_call, outcome='RESPONSE_RECEIVED', usage=TokenUsage()))
    assert result is (settlement == 'normal')
    row = next(iter(db.claims.values()))
    assert row['outcome'] == ('RESPONSE_RECEIVED' if settlement == 'normal' else 'UNKNOWN')
    assert row['finished'] is (settlement == 'normal')
    if settlement == 'normal':
        assert asyncio.run(gate.finish(call, outcome='RESPONSE_RECEIVED', usage=TokenUsage())) is False
    if settlement == 'error':
        assert 'PROVIDER_USAGE_RECORD_FAILED' in caplog.text
    assert all(secret not in caplog.text for secret in ('secret-api-key', 'private-prompt', 'vendor-body'))
