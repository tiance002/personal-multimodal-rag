"""Offline configuration checks; all injected counters/keys are SIMULATED."""
import json
from pathlib import Path

import pytest

from backend.app.adapters.models.cloud import EmbeddingAdmission, SimulatedEmbeddingCounter
from backend.app.adapters.models.service_configuration import configured_service_policies
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.model_registry import ModelRegistry


def policy_json():
    return Path('deploy/service-policy.example.json').read_text(encoding='utf-8')


def test_selected_models_share_deepseek_cap_and_exclude_pro():
    policies = configured_service_policies(Settings(service_model_policy_json=policy_json()),
                                          ModelRegistry.frozen_defaults())
    assert policies['vision'] is policies['chat_expensive']
    assert policies['vision'].token_limit == 1_000_000
    assert policies['embedding'].model == 'BAAI/bge-m3'
    assert policies['embedding'].token_limit is None
    assert policies['chat_cheap'].token_limit is None
    assert policies['embedding'].zero_quote and policies['chat_cheap'].zero_quote


def test_ordinary_output_readiness_is_explicit_without_endpoint_probe(monkeypatch):
    from types import SimpleNamespace as NS
    from backend.app.adapters.models.runtime_readiness import configuration_readiness
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-NOT-A-KEY')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'SIMULATED-NOT-A-KEY')
    output = dict(local_reviewed_caps=[512, 1024], endpoint_output_evidence='UNKNOWN',
                  hosted_recipe_equivalence='UNKNOWN')
    factory = NS(registry=ModelRegistry.frozen_defaults(), enabled_roles={'chat_cheap', 'chat_expensive'},
                 service_bindings={role: NS(capacity=NS(output_readiness=output))
                                   for role in ('chat_cheap', 'chat_expensive')})
    container = NS(settings=Settings(service_model_policy_json=policy_json(), rule_router_enabled=True,
                                     redis_url='redis://127.0.0.1:1'),
                   store=NS(embedding_provider=None), quick_chain=NS(context_manager=None),
                   provider_factory=factory, service_capacity_errors={})
    result = configuration_readiness(container)
    for role in ('chat_cheap', 'chat_expensive'):
        assert result['roles'][role]['status'] == 'NOT_READY'
        assert result['roles'][role]['ordinary_output'] == output
        assert 'OUTPUT_CAPACITY_UNVERIFIED' in result['roles'][role]['reasons']
    assert result['runtime_verified'] is False


@pytest.mark.parametrize('change', ['pro', 'duplicate_role', 'zero_unquoted', 'bool_version'])
def test_malformed_or_mismatched_policy_fails_closed(change):
    raw = json.loads(policy_json())
    if change == 'pro':
        raw['policies'][0]['model'] = 'Pro/BAAI/bge-m3'
    elif change == 'duplicate_role':
        raw['policies'].append({**raw['policies'][0], 'policy_id': 'different-id'})
    elif change == 'zero_unquoted':
        raw['policies'][0]['zero_quote'] = False
    else:
        raw['schema_version'] = True
    with pytest.raises(ValueError, match='SERVICE_MODEL_POLICY_CONFIG_INVALID'):
        configured_service_policies(Settings(service_model_policy_json=json.dumps(raw)),
                                    ModelRegistry.frozen_defaults())


@pytest.mark.parametrize('cloud_enabled', [False, True])
def test_global_egress_and_missing_resources_cannot_fall_back(tmp_path, monkeypatch, cloud_enabled):
    from backend.app.adapters.models import factory as factory_module
    from backend.app.adapters.models.cloud import SiliconFlowEmbedding
    class SimulatedTransport:
        execution_kind = 'SIMULATED'
        def __call__(self, request, timeout):
            raise AssertionError('configuration checks must never send')
    monkeypatch.setattr(factory_module, 'SiliconFlowEmbedding',
        lambda spec, **kwargs: SiliconFlowEmbedding(spec, transport=SimulatedTransport(), **kwargs))
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-NOT-A-KEY')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'SIMULATED-NOT-A-KEY')
    admission = EmbeddingAdmission(SimulatedEmbeddingCounter(lambda text: 2),
        tokenizer_identity='SIMULATED', non_truncating_verified=True)
    settings = Settings(database_url='sqlite:///:memory:', storage_root=tmp_path,
        cloud_enabled=cloud_enabled, embedding_egress_enabled=True, chat_egress_enabled=True,
        service_model_policy_json=policy_json(), cloud_cost_estimate_microunits=10)
    container = build_container(settings, embedding_admission=admission)
    try:
        factory = container.provider_factory
        assert container.quick_chain.router_policy.truncated_escalation is True
        assert factory.enabled_roles == (frozenset({'embedding'}) if cloud_enabled else frozenset())
        assert set(factory.usage_guards) == {'embedding'}
        assert container.quick_chain.cloud_answer_gateway is None
        readiness = container.model_readiness()
        assert readiness['status'] == 'NOT_READY'
        assert 'XING_CAPACITY_RESOURCES_MISSING' in readiness['roles']['chat_cheap']['reasons']
        assert 'DEEPSEEK_TOKENIZER_MISSING' in readiness['roles']['chat_expensive']['reasons']
        assert 'SERVICE_ROUTER_REQUIRED' in readiness['roles']['chat']['reasons']
        if not cloud_enabled:
            assert 'MODEL_ROLE_EGRESS_DENIED' in readiness['roles']['embedding']['reasons']
    finally:
        container.engine.dispose()


def test_settings_load_service_paths_and_policy_without_reading_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('RAG_SERVICE_MODEL_POLICY_JSON', policy_json())
    monkeypatch.setenv('RAG_XING_CAPACITY_RESOURCES', str(tmp_path / 'xing'))
    monkeypatch.setenv('RAG_DEEPSEEK_TOKENIZER_PATH', str(tmp_path / 'deepseek.json'))
    settings = Settings.from_env()
    assert settings.xing_capacity_resources == tmp_path / 'xing'
    assert settings.deepseek_tokenizer_path == tmp_path / 'deepseek.json'
    assert settings.service_model_policy_json == policy_json()


def test_compose_empty_optional_registry_keeps_frozen_role_defaults(monkeypatch):
    monkeypatch.setenv('RAG_MODEL_REGISTRY_JSON', '')
    monkeypatch.setenv('RAG_SERVICE_MODEL_POLICY_JSON', '')
    settings = Settings.from_env()
    assert settings.model_registry_json is None
    assert settings.service_model_policy_json is None
    assert settings.model_registry().select('embedding').model_id == 'BAAI/bge-m3'
