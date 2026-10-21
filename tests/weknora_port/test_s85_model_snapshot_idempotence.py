"""Snapshot readback must not rotate unchanged ciphertext; synthetic keys only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from backend.weknora_port.crypto import SecretCipher, SecretCipherError
from test_model_store import ModelDB, ROLE, row_for, store


class CountingCipher(SecretCipher):
    def __init__(self):
        super().__init__(b'a' * 32)
        self.encryptions = 0

    def encrypt(self, value):
        self.encryptions += 1
        return super().encrypt(value)


def existing(cipher):
    db = ModelDB()
    repo = store(db, cipher)
    row = row_for(repo)
    for name in ('api_key', 'app_secret'):
        row['parameters'][name] = cipher.encrypt(row['parameters'][name])
    cipher.encryptions = 0
    return db, repo, row


@pytest.mark.parametrize('json_text', [False, True])
def test_unchanged_snapshot_preserves_exact_ciphertext_and_model_row(json_text):
    async def run():
        cipher = CountingCipher()
        db, repo, row = existing(cipher)
        if json_text:
            row['parameters'] = json.dumps(row['parameters'])
        before = deepcopy(row)
        db.answers = [[row], [row]]
        assert await repo.persist(ROLE) == row['id']
        assert await repo.persist(ROLE) == row['id']
        assert row == before
        assert not any(sql.startswith(('INSERT INTO models', 'UPDATE models')) for sql, _ in db.calls)
        assert cipher.encryptions == 0
        assert db.tx == ['BEGIN', 'COMMIT', 'BEGIN', 'COMMIT']
    asyncio.run(run())


def test_explicit_credential_rotation_keeps_physical_identity_and_other_ciphertext():
    async def run():
        cipher = CountingCipher()
        db, repo, row = existing(cipher)
        unchanged_app_secret = row['parameters']['app_secret']
        db.answers = [[row]]
        assert await repo.persist(replace(ROLE, api_key='new-synthetic-key')) == row['id']
        sql, args = next(call for call in db.calls if call[0].startswith('UPDATE models'))
        updated = json.loads(args[0])
        assert cipher.decrypt(updated['api_key']) == 'new-synthetic-key'
        assert updated['app_secret'] == unchanged_app_secret
        assert cipher.encryptions == 1
        assert 'identity_sha256=' not in sql and 'name=' not in sql
    asyncio.run(run())


def test_unchanged_nested_encrypted_secret_from_role_snapshot_is_idempotent():
    async def run():
        cipher = CountingCipher()
        db, repo, row = existing(cipher)
        role = replace(ROLE, parameters={**ROLE.parameters, 'app_secret': row['parameters']['app_secret']})
        db.answers = [[row]]
        assert await repo.persist(role) == row['id']
        assert not any(sql.startswith(('INSERT INTO models', 'UPDATE models')) for sql, _ in db.calls)
        assert cipher.encryptions == 0
    asyncio.run(run())


def test_unreadable_old_secret_never_silently_repaired_by_snapshot():
    async def run():
        cipher = CountingCipher()
        db, repo, row = existing(cipher)
        row['parameters']['api_key'] = 'enc:v1:invalid'
        db.answers = [[row]]
        with pytest.raises(SecretCipherError, match='STORED_SECRET_DECRYPT_FAILED'):
            await repo.persist(ROLE)
        assert not any(sql.startswith(('INSERT INTO models', 'UPDATE models')) for sql, _ in db.calls)
    asyncio.run(run())
