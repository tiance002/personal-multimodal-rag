import json
from pathlib import Path
import pytest
from backend.weknora_port.crypto import SecretCipher


def test_exact_fixed_source_ciphertext_and_lenient_strict_missing_key(monkeypatch):
    fixture = json.loads(Path(__file__).with_name('fixtures').joinpath('crypto_oracle.json').read_text())
    assert fixture['oracle'] == 'EXECUTED_FIXED_SOURCE_STDLIB_GO' and fixture['exit_code'] == 0
    monkeypatch.setattr('backend.weknora_port.crypto.os.urandom', lambda size: b'012345678901')
    cipher = SecretCipher(b'01234567890123456789012345678901')
    expected = fixture['case']['ciphertext']
    assert cipher.encrypt('synthetic-secret') == expected
    assert cipher.decrypt(expected) == 'synthetic-secret'
    assert SecretCipher(None).decrypt(expected,strict=False) == fixture['case']['missing_key_lenient'] == ''
    with pytest.raises(ValueError,match='STORED_SECRET_DECRYPT_FAILED'):
        SecretCipher(None).decrypt(expected)


def test_rotated_key_and_corrupt_ciphertext_do_not_become_api_keys():
    cipher = SecretCipher(b'a'*32)
    value = cipher.encrypt('synthetic-secret')
    for stored in (value,'enc:v1:short','enc:v1:%%%%'):
        with pytest.raises(ValueError): SecretCipher(b'b'*32).decrypt(stored)
        assert SecretCipher(b'b'*32).decrypt(stored,strict=False) == ''
    assert cipher.encrypt(value) == value
    assert SecretCipher(None).encrypt('synthetic-legacy') == 'synthetic-legacy'


def test_role_store_encryption_at_rest_and_public_safe_rotation():
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.storage.history import LocalPrincipal
    from tests.weknora_port.test_model_configuration import ConfigurationConnection,Catalogue,values
    from tests.weknora_port.test_history_storage import adapter
    import asyncio
    async def run():
        connection = ConfigurationConnection()
        guard = adapter(connection).repository
        store = PostgresModelRoleStore(guard,LocalPrincipal(10000,'local'),Catalogue(),cipher=SecretCipher(b'a'*32))
        await store.update(0,values())
        assert all(role['api_key'].startswith('enc:v1:') for role in connection.saved['roles'].values())
        assert 'synthetic-secret' not in json.dumps(connection.saved)
        _,roles = await store.read()
        assert all(role.api_key == 'synthetic-secret' for role in roles)
        rotated = PostgresModelRoleStore(guard,store.principal,Catalogue(),cipher=SecretCipher(b'b'*32))
        assert all(not role['key_configured'] for role in (await rotated.read_public())['roles'])
        with pytest.raises(ValueError): await rotated.read()
    asyncio.run(run())
