"""Usage-only production send path: offline HTTP and SQL doubles, no cloud."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.runtime import LeasedSendGate
from backend.weknora_port.models import (provider_config, ProviderModel, AsyncHTTPTransport,
    CallContext, TrustedScope, Message, ChatOptions, ProviderError)
from backend.weknora_port.models.transport import endpoint_urls


class DB:
    def __init__(self):
        self.calls = {}; self.usage = []; self.cloud = True; self.scope = True
        self.old_slot = False; self.fail_usage = False; self.model = True

    @asynccontextmanager
    async def transaction(self): yield

    async def execute(self, sql, args=()):
        result = []
        if sql.startswith('SELECT id,cloud_allowed'):
            result = [{'id': 'kb', 'cloud_allowed': self.cloud}] if self.scope else []
        elif sql.startswith('SELECT source FROM models'):
            result = [{'source': 'remote'}] if self.model else []
        elif sql.startswith('SELECT to_regclass'):
            result = [{'slots': 'port_call_slots'}]
        elif sql.startswith('SELECT call_id FROM port_call_slots'):
            result = [{'call_id': args[0]}] if self.old_slot else []
        elif sql.startswith('INSERT INTO port_provider_calls'):
            key = args[:3]
            if key not in self.calls:
                self.calls[key] = args
                result = [{'request_id': args[3]}]
        elif sql.startswith('UPDATE port_provider_calls'):
            if self.fail_usage: raise RuntimeError('disk unavailable')
            self.usage.append(args)
        async def fetchall(): return result
        return SimpleNamespace(fetchall=fetchall)


def setup(db, *, broken=False):
    @asynccontextmanager
    async def lease(): yield SimpleNamespace(connection=db)
    app = SimpleNamespace(principal=SimpleNamespace(tenant_id=1, subject_id='owner'),
        leases=SimpleNamespace(lease=lease), cloud_allowed=True)
    gate = LeasedSendGate(app)
    cfg = provider_config('siliconflow', 'test')
    sent = []
    def handler(req):
        sent.append(req)
        if broken: raise httpx.ReadError('unknown result', request=req)
        return httpx.Response(200, json={'model': cfg.model,
            'choices': [{'message': {'content': 'answer'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    transport = AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg), gate=gate,
        credential=lambda _: 'SYNTHETIC-NOT-A-KEY', http_transport=httpx.MockTransport(handler))
    return ProviderModel(cfg, transport), transport, app, sent


def test_normal_call_without_authority_records_usage_and_deduplicates():
    async def run():
        db = DB(); model, transport, app, sent = setup(db)
        ctx = CallContext('new', TrustedScope('owner', ('kb',)))
        try:
            out = await model.chat((Message('user', 'q'),), ChatOptions(max_tokens=64), ctx)
            assert out.content == 'answer' and out.usage.total_tokens == 15
            assert len(db.calls) == len(db.usage) == len(sent) == 1
            assert db.usage[0][0] == 'RESPONSE_RECEIVED'
            assert json.loads(db.usage[0][1])['total_tokens'] == 15
            with pytest.raises(ProviderError, match='MODEL_CALL_ID_REUSE_DENIED'):
                await model.chat((Message('user', 'q'),), ChatOptions(max_tokens=64), ctx)
            assert len(sent) == 1
        finally: await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('condition', ['global', 'kb', 'tenant', 'model', 'old_unknown', 'no_cap'])
def test_permissions_old_same_operation_and_output_cap_remain(condition):
    async def run():
        db = DB(); model, transport, app, sent = setup(db)
        if condition == 'global': app.cloud_allowed = False
        if condition == 'kb': db.cloud = False
        if condition == 'tenant': db.scope = False
        if condition == 'model': db.model = False
        if condition == 'old_unknown': db.old_slot = True
        try:
            with pytest.raises(ProviderError):
                await model.chat((Message('user', 'q'),),
                    ChatOptions(max_tokens=0 if condition == 'no_cap' else 64),
                    CallContext('new', TrustedScope('owner', ('kb',))))
            assert not sent and not db.calls
        finally: await transport.aclose()
    asyncio.run(run())


def test_unknown_send_is_not_repeated_and_usage_write_failure_does_not_gate_answer(caplog):
    async def run():
        for broken in (True, False):
            db = DB(); db.fail_usage = not broken
            model, transport, app, sent = setup(db, broken=broken)
            ctx = CallContext('new', TrustedScope('owner', ('kb',)))
            try:
                if broken:
                    with pytest.raises(ProviderError, match='PROVIDER_TRANSPORT_ERROR'):
                        await model.chat((Message('user', 'q'),), ChatOptions(max_tokens=64), ctx)
                    assert db.usage[0][0] == 'UNKNOWN'
                else:
                    assert (await model.chat((Message('user', 'q'),), ChatOptions(max_tokens=64), ctx)).content == 'answer'
                with pytest.raises(ProviderError, match='MODEL_CALL_ID_REUSE_DENIED'):
                    await model.chat((Message('user', 'q'),), ChatOptions(max_tokens=64), ctx)
                assert len(sent) == 1
            finally: await transport.aclose()
    asyncio.run(run())
    assert 'PROVIDER_USAGE_RECORD_FAILED' in caplog.text
