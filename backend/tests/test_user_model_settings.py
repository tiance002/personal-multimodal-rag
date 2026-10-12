"""SIMULATED local credentials; no real keys, DB writes or provider calls."""
import json
import socket
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.adapters.models.user_settings import (
    public_config, prepare_config, apply_config, write_config, config_path,
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: pytest.fail('network forbidden'))
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED_ENV_KEY')
    return Settings(storage_root=tmp_path, database_url='sqlite+pysqlite:///:memory:')


def payload(settings):
    config = public_config(settings)
    for row in config['roles']:
        row.pop('key_configured')
    return config


def test_api_persists_roles_and_never_returns_secret(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        config = client.get('/api/v1/settings/model-configuration').json()['data']
        for row in config['roles']:
            row.pop('key_configured')
        config['roles'][0]['api_key'] = 'SIMULATED_PRIVATE_KEY'
        result = client.put('/api/v1/settings/model-configuration', json=config)
        assert result.status_code == 200, result.text
        assert 'SIMULATED_PRIVATE_KEY' not in result.text
        assert result.json()['data']['roles'][0]['key_configured'] is True
        assert 'SIMULATED_PRIVATE_KEY' not in repr(app.state.settings)
    restarted = create_app(settings)
    assert restarted.state.settings.model_api_keys['embedding'] == 'SIMULATED_PRIVATE_KEY'
    assert restarted.state.container.store.embedding_provider.credential_present()
    assert config_path(settings).exists()


def test_blank_key_preserved_but_destination_change_clears_it(settings):
    value = payload(settings)
    value['roles'][0]['api_key'] = 'SIMULATED_KEY'
    write_config(settings, prepare_config(settings, value))
    value = payload(settings)
    saved = prepare_config(settings, value)
    assert saved['roles']['embedding']['api_key'] == 'SIMULATED_KEY'
    value['roles'][0]['base_url'] = 'https://example.invalid/v1'
    saved = prepare_config(settings, value)
    configured = apply_config(settings, saved)
    assert configured.model_api_keys['embedding'] == ''
    assert saved['roles']['embedding']['api_key'] == ''
    assert configured.model_registry().select('embedding').resolved_revision.startswith('endpoint/')


def test_stale_revision_does_not_overwrite(settings):
    original = payload(settings)
    write_config(settings, prepare_config(settings, original))
    with pytest.raises(ValueError, match='CONFLICT'):
        prepare_config(settings, original)


@pytest.mark.parametrize('url', ['http://example.invalid/v1', 'https://user:password@example.invalid/v1',
                                'https://example.invalid/v1?key=secret', 'file:///private'])
def test_unsafe_urls_rejected_without_sending(settings, url):
    value = payload(settings)
    value['roles'][0]['base_url'] = url
    with pytest.raises(ValueError):
        prepare_config(settings, value)


def test_pro_is_rejected(settings):
    value = payload(settings)
    value['roles'][0]['model'] = 'Pro/BAAI/bge-m3'
    with pytest.raises(ValueError, match='PRO_DISABLED'):
        prepare_config(settings, value)


def test_custom_model_saved_but_no_quota_or_protocol_assumed(settings):
    value = payload(settings)
    value['roles'][0].update(provider='example', model='custom-embedding', base_url='https://example.invalid/v1')
    configured = apply_config(settings, prepare_config(settings, value))
    app = create_app(configured)
    provider = app.state.container.store.embedding_provider
    assert provider.spec.model_id == 'custom-embedding'
    assert not provider.enabled
    assert not app.state.container.provider_factory.service_bindings
    assert not provider.credential_present()


def test_openapi_marks_key_as_write_only_and_lists_all_roles(settings):
    schema=create_app(settings).openapi()['paths']['/api/v1/settings/model-configuration']['put']['requestBody']['content']['application/json']['schema']
    role=schema['properties']['roles']['items']['properties']
    assert role['api_key']['writeOnly'] is True
    assert set(role['role']['enum']) == {'embedding','rerank','chat_cheap','chat_expensive','vision'}
