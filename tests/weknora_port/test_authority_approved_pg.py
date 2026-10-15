"""Approved-mode assembly wiring on real isolated PG/Redis; no provider request.

Proves the standard assembly builds an ApprovedTokenAuthority + ServerPermissionResolver
and that the approved model_factory refuses models outside the config, while a closed
transport is used (SafeHTTPTransport is never dialed here).
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from backend.weknora_port.authority import (
    ApprovedTokenAuthority, ServerPermissionResolver, load_authority_settings)
from backend.weknora_port.development import DevelopmentConfigurationError, DevelopmentSettings, assemble
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.types import ModelConfig
from backend.weknora_port.storage.image_tasks_migrate import apply_image_task_schema
from backend.weknora_port.storage.token_migrate import apply_token_schema
from backend.weknora_port.storage.turns_migrate import apply_turn_schema
from tests.weknora_port.redis_fixture_identity import expected_redis_run_id
from tests.weknora_port.test_memory_storage_pg import EXPECTED, actual_pg_run, approved_dsn, connection


def test_approved_assembly_wires_real_authority_without_provider_calls():
    async def scenario():
        run_id = expected_redis_run_id()
        conn, guard = await connection(approved_dsn())
        try:
            for apply in (apply_turn_schema, apply_image_task_schema, apply_token_schema):
                await apply(guard)
            tenant = (await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                ('approved-wiring-' + uuid.uuid4().hex, 'isolated-test'))).fetchone())['id']
        finally:
            await conn.close()
        root = Path('E:/WK/run/approved-wiring-' + uuid.uuid4().hex[:12]); root.mkdir()
        key = root/'secret.key'; key.write_bytes(b'k'*32)
        meter = root/'meter.bin'; meter.write_bytes(b'approved-meter-asset')
        subject = 'owner-approved-' + uuid.uuid4().hex[:8]
        cfg = dict(schema='weknora-token-authority/v1', mode='approved', tenant_id=tenant,
            subject_id=subject, account_id='deepseek-account-main', provider='deepseek',
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            approval_source='OWNER:20261015-approved-token-meter',
            month=datetime.now(timezone.utc).date().replace(day=1).isoformat(),
            carry_in_tokens=0, carry_status='KNOWN', carry_source='LEDGER:port_token_calls:prior-month',
            limit_tokens=1_000_000, allowed_models=[['deepseek', 'deepseek-flash']],
            input_limit=200000, output_limit=8192,
            meter={'recipe': 'wire-bytes-upper-bound/v1', 'path': str(meter),
                   'sha256': hashlib.sha256(meter.read_bytes()).hexdigest()})
        config = root/'authority.json'; config.write_text(json.dumps(cfg), encoding='utf-8')
        settings = DevelopmentSettings(approved_dsn(), EXPECTED, 'weknora_python_port_20261013',
            'redis://127.0.0.1:56424/15', run_id, 'weknora-python-approved:' + uuid.uuid4().hex[:8],
            root/'storage', key, None, origins=('http://127.0.0.1:56425',))
        async with assemble(settings, authority_settings=load_authority_settings(config)) as runtime:
            assert isinstance(runtime.token_authority, ApprovedTokenAuthority)
            assert isinstance(runtime.permissions, ServerPermissionResolver)
            assert runtime.principal.tenant_id == tenant and runtime.principal.subject_id == subject
            allowed = ModelConfig(provider='deepseek', model='deepseek-flash', model_id='mid',
                base_url='https://api.deepseek.com')
            assert isinstance(runtime.model_factory(allowed, b'k'*32, None), ProviderModel)
            denied = ModelConfig(provider='deepseek', model='deepseek-v4-pro', model_id='mid',
                base_url='https://api.deepseek.com')
            with pytest.raises(DevelopmentConfigurationError, match='AUTHORITY_MODEL_NOT_ALLOWED'):
                runtime.model_factory(denied, b'k'*32, None)
    actual_pg_run(scenario())
