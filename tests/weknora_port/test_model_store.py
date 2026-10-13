"""Model store SQL doubles; no PostgreSQL or provider/model calls."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_turn_storage import DB, Cursor, Leases, PRINCIPAL
from backend.weknora_port.storage.models import PostgresModelStore
from backend.weknora_port.storage.configuration import ConfiguredRole, ModelConfigurationError
from backend.weknora_port.crypto import SecretCipher


ROLE = ConfiguredRole('embedding', 'fixture', 'fixture-model', 'https://fixture.invalid/v1',
    'synthetic-key', {'embedding_parameters': {'dimension': 3}, 'spec': {'api': 'openai-embeddings'}, 'app_secret': 'synthetic-app-secret'})
CATALOGUE = SimpleNamespace(validate=lambda role: None)


class ModelDB(DB):
    async def execute(self, sql, params=()):
        if sql.startswith('SELECT id FROM tenants'):
            self.calls.append((sql, params)); return Cursor([{'id': PRINCIPAL.tenant_id}])
        if sql.startswith('INSERT INTO models'):
            self.calls.append((sql, params)); return Cursor([{'id': params[0]}])
        return await super().execute(sql, params)


class FixtureCipher:
    def encrypt(self, value): return 'fixture-cipher:' + value if value else ''
    def decrypt(self, value, *, strict=True):
        assert strict
        if value == 'broken': raise ValueError('STORED_SECRET_DECRYPT_FAILED')
        return value.removeprefix('fixture-cipher:')


def store(db=None, cipher=None):
    return PostgresModelStore(Leases(db or ModelDB()), PRINCIPAL, CATALOGUE,
                              cipher=cipher if cipher is not None else FixtureCipher())


def row_for(repo, role=ROLE):
    id, identity, physical, parameters = repo._identity(role)
    return {'id': id, 'tenant_id': PRINCIPAL.tenant_id, 'subject_id': PRINCIPAL.subject_id,
            'name': role.model, 'type': 'Embedding', 'source': role.provider, 'role': role.role,
            'identity_sha256': identity, 'physical_sha256': physical, 'parameters': parameters,
            'deleted_at': None, 'status': 'active'}


def test_fingerprint_excludes_credentials_but_pins_all_physical_parameters():
    repo = store()
    identity = repo._identity(ROLE)
    changed_key = replace(ROLE, api_key='different-key', parameters={**ROLE.parameters, 'app_secret': 'different-secret'})
    assert repo._identity(changed_key)[:3] == identity[:3]
    assert len(identity[0]) == 64
    for changed in (replace(ROLE, provider='other'), replace(ROLE, model='other'), replace(ROLE, base_url='https://other.invalid'),
                    replace(ROLE, parameters={'embedding_parameters': {'dimension': 4}})):
        assert repo._identity(changed)[0] != identity[0]
    cheap = replace(ROLE, role='cheap'); expensive = replace(ROLE, role='expensive')
    assert repo._identity(cheap)[0] != repo._identity(expensive)[0]
    assert repo._identity(cheap)[2] == repo._identity(expensive)[2]


def test_insert_encrypts_both_source_secrets_and_never_overwrites_role_rows():
    async def run():
        db = ModelDB([[]]); repo = store(db)
        id = await repo.persist(ROLE)
        sql, args = next(call for call in db.calls if call[0].startswith('INSERT INTO models'))
        params = json.loads(args[6])
        assert params['api_key'] == 'fixture-cipher:synthetic-key'
        assert params['app_secret'] == 'fixture-cipher:synthetic-app-secret'
        assert args[0] == id and 'ON CONFLICT(id) DO NOTHING' in sql
        assert not any('DELETE ' in query or 'port_model_configuration' in query for query, _ in db.calls)
        assert db.tx == ['BEGIN', 'COMMIT']
    asyncio.run(run())


def test_same_identity_updates_only_credentials_and_keeps_model_id():
    async def run():
        db = ModelDB(); repo = store(db); row = row_for(repo)
        db.answers = [[row]]
        id = await repo.persist(replace(ROLE, api_key='replacement-key'))
        assert id == row['id']
        sql, args = next(call for call in db.calls if call[0].startswith('UPDATE models'))
        assert 'name=' not in sql and 'identity_sha256=' not in sql
        updated = json.loads(args[0])
        assert updated['embedding_parameters'] == row['parameters']['embedding_parameters']
        assert updated['api_key'] == 'fixture-cipher:replacement-key'
    asyncio.run(run())


@pytest.mark.parametrize('corruption', ['hash', 'parameters', 'owner', 'deleted'])
def test_collision_or_unavailable_row_cannot_mutate(corruption):
    async def run():
        db = ModelDB(); repo = store(db); row = row_for(repo)
        if corruption == 'hash': row['identity_sha256'] = 'wrong'
        elif corruption == 'parameters': row['parameters']['embedding_parameters'] = {'dimension': 100}
        elif corruption == 'owner': row['subject_id'] = 'foreign'
        else: row['deleted_at'] = 'deleted'
        db.answers = [[row]]
        with pytest.raises(ModelConfigurationError): await repo.persist(ROLE)
        assert not any(sql.startswith('UPDATE') or sql.startswith('INSERT') for sql, _ in db.calls)
        assert db.tx == ['BEGIN', 'ROLLBACK']
    asyncio.run(run())


def test_resolve_strict_and_public_projection_cannot_echo_secrets():
    async def run():
        db = ModelDB(); repo = store(db); row = row_for(repo)
        row['parameters']['api_key'] = 'broken'
        db.answers = [[row]]
        with pytest.raises(ValueError, match='STORED_SECRET_DECRYPT_FAILED'): await repo.resolve(row['id'])
        db.answers = [[row]]
        public = await repo.public(row['id'])
        assert public['key_configured'] and 'api_key' not in public and 'parameters' not in public
        assert 'synthetic' not in repr(public)
        row['parameters']['api_key'] = 'fixture-cipher:synthetic-key'
        row['parameters']['app_secret'] = 'fixture-cipher:synthetic-app-secret'
        db.answers = [[row], [row]]
        resolved = await repo.resolve(row['id'])
        assert resolved.api_key == ROLE.api_key and resolved.parameters['app_secret'] == ROLE.parameters['app_secret']
        assert await repo.physical_key(row['id']) == row['physical_sha256']
        assert 'synthetic-key' not in repr(resolved)
    asyncio.run(run())


def test_unset_aes_source_plaintext_fallback_is_explicit_and_parameter_conflicts_deny():
    async def run():
        db = ModelDB([[]]); repo = store(db, SecretCipher(None))
        await repo.persist(ROLE)
        params = json.loads(next(args for sql, args in db.calls if sql.startswith('INSERT INTO models'))[6])
        assert params['api_key'] == ROLE.api_key  # Source unset-key behavior only.
        with pytest.raises(ModelConfigurationError, match='MODEL_PARAMETER_IDENTITY_CONFLICT'):
            repo._identity(replace(ROLE, parameters={'base_url': 'https://foreign.invalid'}))
    asyncio.run(run())
