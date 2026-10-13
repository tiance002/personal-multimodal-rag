"""Actual isolated PostgreSQL plus real AES checks; no provider/model calls."""
import asyncio
import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_memory_storage_pg import approved_dsn, actual_pg_run, connection
from test_turn_storage_pg import factory
from backend.weknora_port.crypto import SecretCipher
from backend.weknora_port.storage.models import PostgresModelStore
from backend.weknora_port.storage.models_migrate import apply_model_schema
from backend.weknora_port.storage.configuration import ConfiguredRole, ModelConfigurationError
from backend.weknora_port.storage.history import LocalPrincipal

CATALOGUE = SimpleNamespace(validate=lambda role: None)
ROLE = ConfiguredRole('embedding', 'fixture', 'synthetic-embedding', 'https://fixture.invalid/v1',
                      'synthetic-fixture-key', {'embedding_parameters': {'dimension': 3},
                      'app_secret': 'synthetic-fixture-app-secret', 'spec': {'api': 'openai-embeddings'}})


def save(name, value):
    path = Path(__file__).resolve().parents[2] / 'docs/weknora-port/P1_MODEL_STORE_EVIDENCE.json'
    content = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'kind': 'actual-isolated-pg-real-aes-synthetic-records', 'system_identifier': '7696038373595934758',
        'database': 'weknora_python_port_20261013', 'model_calls': 0, 'old_database_writes': 0, 'cases': {}}
    content['cases'][name] = value
    path.write_text(json.dumps(content, indent=2, default=str) + '\n', encoding='utf-8')


async def setup(dsn):
    conn, guard = await connection(dsn)
    try:
        digest = await apply_model_schema(guard)
        tenant = (await (await conn.execute("INSERT INTO tenants(name,business) VALUES('Synthetic model catalog fixture','isolated-test') RETURNING id")).fetchall())[0]['id']
        principal = LocalPrincipal(tenant, 'web_user:model-fixture:' + str(uuid.uuid4()))
        repo = PostgresModelStore(factory(dsn), principal, CATALOGUE, cipher=SecretCipher(bytes(range(32))))
        return conn, principal, repo, digest
    except BaseException:
        await conn.close(); raise


def test_actual_pg_old_embedding_pin_survives_configuration_change_and_key_update():
    dsn = approved_dsn()
    async def run():
        conn, principal, repo, digest = await setup(dsn)
        try:
            original = await repo.persist(ROLE)
            kb = str(uuid.uuid4())
            await conn.execute("INSERT INTO knowledge_bases(id,tenant_id,name,embedding_model_id,summary_model_id,rerank_model_id) VALUES(%s,%s,'Synthetic pinned KB',%s,'','')", (kb, principal.tenant_id, original))
            different = replace(ROLE, model='synthetic-new-embedding', parameters={'embedding_parameters': {'dimension': 4}})
            current = await repo.persist(different)
            assert current != original
            replacement = replace(ROLE, api_key='synthetic-replacement-key')
            assert await repo.persist(replacement) == original
            restored = await repo.resolve(original)
            assert restored.model == ROLE.model and restored.parameters['embedding_parameters']['dimension'] == 3
            assert restored.api_key == replacement.api_key and restored.parameters['app_secret'] == ROLE.parameters['app_secret']
            pinned = (await (await conn.execute('SELECT embedding_model_id FROM knowledge_bases WHERE id=%s', (kb,))).fetchall())[0]['embedding_model_id']
            assert pinned == original
            rows = await (await conn.execute('SELECT id,name,type,parameters,identity_sha256,physical_sha256 FROM models WHERE tenant_id=%s AND subject_id=%s ORDER BY id', (principal.tenant_id, principal.subject_id))).fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row['parameters']['api_key'].startswith('enc:v1:')
                if row['parameters'].get('app_secret'): assert row['parameters']['app_secret'].startswith('enc:v1:')
            public = await repo.public(original)
            assert 'parameters' not in public and 'api_key' not in public and 'synthetic-replacement-key' not in repr(public)
            save('old_pin', {'migration_sha256': digest, 'tenant_id': principal.tenant_id, 'knowledge_base_id': kb,
                'pinned_model_id': pinned, 'new_model_id': current, 'retained_row_count': len(rows),
                'stored_secrets_encrypted': True, 'credential_update_preserved_id': True,
                'public': public, 'physical_keys': [row['physical_sha256'] for row in rows]})
        finally: await conn.close()
    actual_pg_run(run())


def test_actual_pg_concurrent_catalog_idempotency_and_physical_role_grouping():
    dsn = approved_dsn()
    async def run():
        conn, principal, repo, _ = await setup(dsn)
        try:
            second = PostgresModelStore(factory(dsn), principal, CATALOGUE, cipher=SecretCipher(bytes(range(32))))
            ids = await asyncio.gather(repo.persist(ROLE), second.persist(ROLE))
            assert ids[0] == ids[1]
            assert (await (await conn.execute('SELECT count(*) AS n FROM models WHERE tenant_id=%s', (principal.tenant_id,))).fetchall())[0]['n'] == 1
            cheap = await repo.persist(replace(ROLE, role='cheap'))
            expensive = await repo.persist(replace(ROLE, role='expensive'))
            assert cheap != expensive and await repo.physical_key(cheap) == await repo.physical_key(expensive)
            save('concurrent_physical', {'tenant_id': principal.tenant_id, 'concurrent_same_id': ids[0],
                'concurrent_row_count': 1, 'cheap_id': cheap, 'expensive_id': expensive,
                'same_physical_key': await repo.physical_key(cheap)})
        finally: await conn.close()
    actual_pg_run(run())


def test_actual_pg_wrong_key_strict_decrypt_public_and_owner_scope():
    dsn = approved_dsn()
    async def run():
        conn, principal, repo, _ = await setup(dsn)
        try:
            id = await repo.persist(ROLE)
            wrong = PostgresModelStore(factory(dsn), principal, CATALOGUE, cipher=SecretCipher(bytes(reversed(range(32)))))
            with pytest.raises(ValueError, match='STORED_SECRET_DECRYPT_FAILED'): await wrong.resolve(id)
            assert (await wrong.public(id))['key_configured']
            foreign = PostgresModelStore(factory(dsn), LocalPrincipal(principal.tenant_id, 'foreign-owner'), CATALOGUE, cipher=SecretCipher(bytes(range(32))))
            with pytest.raises(ModelConfigurationError, match='MODEL_NOT_FOUND'): await foreign.resolve(id)
            assert (await repo.resolve(id)).api_key == ROLE.api_key
            save('strict_scope', {'tenant_id': principal.tenant_id, 'model_id': id, 'wrong_key_denied': True,
                'foreign_owner_denied': True, 'public_available_without_decrypt': True, 'original_key_readback_ok': True})
        finally: await conn.close()
    actual_pg_run(run())
