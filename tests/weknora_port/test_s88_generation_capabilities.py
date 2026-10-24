"""Read-only capability guidance; fixed catalogue facts are not certification."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import httpx

from backend.weknora_port.models import provider_config
from backend.weknora_port.models.generation_capabilities import describe_generation_capabilities
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.runtime import ApplicationRuntime


def test_unknown_hosted_model_keeps_unknown_thinking_and_uncertified_capacity():
    config = SourceCatalogue().resolve(ConfiguredRole('cheap', 'siliconflow', 'Qwen/Qwen3.5-4B',
        'https://fixture.invalid/v1', source='remote'), 'current-cheap')
    row = describe_generation_capabilities(config, 2048, role='cheap')
    assert row['thinking'] == 'unknown'
    assert row['operating_max_output_tokens'] == 2048
    assert row['certified_max_output_tokens'] is None
    assert row['declared_max_output_tokens'] is None
    assert row['error_code'] == 'MODEL_GENERATION_CAPACITY_UNKNOWN'
    assert row['selected_model'] == {'model_id':'current-cheap', 'provider':'siliconflow',
        'model':'Qwen/Qwen3.5-4B', 'source':'remote', 'role':'cheap'}


def test_uncatalogued_operational_allowance_does_not_turn_unknown_smart_into_unsupported():
    from backend.weknora_port.models import ProviderModel
    from backend.weknora_port.models.types import ChatOptions
    role=ConfiguredRole('cheap','siliconflow','Qwen/Qwen3.5-4B','https://fixture.invalid/v1',
        parameters={'spec_override':{'context_window':32768,'max_output_tokens':2048}},source='remote')
    config=SourceCatalogue().resolve(role,'explicit-operating-only')
    row=describe_generation_capabilities(config,2048,role='cheap')
    assert row['thinking']=='unknown' and row['certified_max_output_tokens'] is None
    assert row['declared_max_output_tokens'] is None
    # The explicit finite allowance preserves Quick's existing contract.
    ProviderModel(config,None).build_request_body((),ChatOptions(reasoning_mode='quick',
        thinking=False,reasoning_effort='off',max_completion_tokens=2048))
    with pytest.raises(ProviderError,match='MODEL_SMART_MODE_UNKNOWN') as failure:
        ProviderModel(config,None).build_request_body((),ChatOptions(reasoning_mode='smart',
            thinking=True,reasoning_effort='auto',max_completion_tokens=2048))
    assert failure.value.sent is False


def test_catalogued_thinking_and_declared_limit_are_not_live_certification():
    config = provider_config('deepseek', 'deepseek-flash')
    row = describe_generation_capabilities(config, 2048, role='expensive')
    assert row['thinking'] == 'supported'
    assert row['declared_max_output_tokens'] == config.max_output_tokens
    assert row['certified_max_output_tokens'] is None
    assert row['capacity_source'] == 'fixed-source-catalog'
    assert row['operating_max_output_tokens'] == 2048


@pytest.mark.parametrize('provider,model',[('openai','gpt-5.4'),
    ('anthropic','claude-sonnet-4-6'),('gemini','gemini-2.5-flash')])
def test_native_capability_projection_uses_the_same_smart_wire_as_generation(provider,model):
    from backend.weknora_port.models import ProviderModel
    from backend.weknora_port.models.types import ChatOptions
    config=provider_config(provider,model)
    assert describe_generation_capabilities(config,2048)['thinking']=='supported'
    ProviderModel(config,None).build_request_body((),ChatOptions(reasoning_mode='smart',
        thinking=True,reasoning_effort='auto',max_completion_tokens=2048))


def test_known_nonreasoning_model_is_unsupported_but_unknown_override_is_not():
    known = replace(provider_config('deepseek','deepseek-flash'), reasoning=False)
    assert describe_generation_capabilities(known,2048)['thinking'] == 'unsupported'
    assert describe_generation_capabilities(replace(known,cataloged=False),2048)['thinking'] == 'unknown'


def test_declared_reasoning_without_supported_wire_is_not_advertised_as_supported():
    config=provider_config('deepseek','deepseek-flash')
    config=replace(config,chat=replace(config.chat,thinking_format='none'))
    row=describe_generation_capabilities(config,2048)
    assert row['thinking']=='unsupported' and row['error_code']=='MODEL_SMART_MODE_UNSUPPORTED'


def test_no_selected_model_is_unknown_and_scope_failure_is_not_softened():
    runtime = ApplicationRuntime.__new__(ApplicationRuntime)
    runtime.policy = SimpleNamespace(mode='OFF')
    async def select(*args): raise ProviderError('CHAT_MODEL_CONFIGURATION_REQUIRED')
    runtime._normal_chat_model_id = select
    row=asyncio.run(runtime._answer_generation_capabilities({},2048))
    assert row['thinking']=='unknown' and row['selected_model'] is None
    assert row['error_code']=='CHAT_MODEL_CONFIGURATION_REQUIRED'
    async def denied(*args): raise ProviderError('MODEL_OWNER_MISMATCH')
    runtime._normal_chat_model_id=denied
    with pytest.raises(ProviderError,match='MODEL_OWNER_MISMATCH'):
        asyncio.run(runtime._answer_generation_capabilities({},2048))


def test_off_uses_the_normal_selected_model_without_model_factory_or_snapshot():
    runtime = ApplicationRuntime.__new__(ApplicationRuntime)
    runtime.policy = SimpleNamespace(mode='OFF')
    runtime.catalogue = SourceCatalogue()
    role=ConfiguredRole('cheap','deepseek','deepseek-flash','https://fixture.invalid/v1',source='remote')
    calls=[]
    async def select(session, requested, kb_ids):
        calls.append((session,requested,kb_ids));return 'selected'
    async def load(model_id):
        assert model_id=='selected';return {},{},role
    runtime._normal_chat_model_id=select
    runtime.models=SimpleNamespace(_load=load)
    session={'knowledge_base_scope':['kb']}
    row=asyncio.run(runtime._answer_generation_capabilities(session,1234))
    assert calls==[(session,'',('kb',))]
    assert row['selected_model']['model_id']=='selected'
    assert row['selected_model']['role']=='cheap' and row['thinking']=='supported'
    assert row['routing_mode']=='OFF' and row['selection_scope']=='selected-model'


@pytest.mark.parametrize('cheap_model,thinking',[('Qwen/Qwen3.5-4B','unknown'),
    ('Qwen/Qwen3.5-397B-A17B','supported')])
def test_dynamic_read_reports_real_role_candidates_without_persisting_or_sending(monkeypatch,cheap_model,thinking):
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.crypto import SecretCipher
    runtime=ApplicationRuntime.__new__(ApplicationRuntime)
    runtime.policy=SimpleNamespace(mode='DYNAMIC');runtime.catalogue=SourceCatalogue()
    runtime.principal=SimpleNamespace(tenant_id=1,subject_id='owner');runtime.cipher=SecretCipher(None)
    roles={role.role:{**role.public(), 'api_key':'synthetic-secret', 'parameters':dict(role.parameters)}
        for role in (ConfiguredRole('cheap','siliconflow',cheap_model,'https://fixture.invalid',source='remote'),
                     ConfiguredRole('expensive','deepseek','deepseek-flash','https://fixture.invalid',source='remote'))}
    for value in roles.values():value.pop('key_configured')
    reads=[]
    async def stored(self):reads.append('read');return 7,roles
    monkeypatch.setattr(PostgresModelRoleStore,'_stored',stored)
    @asynccontextmanager
    async def lease():yield object()
    runtime.leases=SimpleNamespace(lease=lease)
    row=asyncio.run(runtime._answer_generation_capabilities({},2048))
    assert reads==['read'] and row['thinking']==thinking
    assert row['selected_model'] is None and row['configuration_revision']==7
    assert row['routing_mode']=='DYNAMIC' and row['selection_scope']=='configured-routing-roles'
    assert [item['role'] for item in row['candidate_models']]==['cheap','expensive']
    assert row['candidate_models'][0]['model']==cheap_model
    assert all(item['model_id'] is None for item in row['candidate_models'])
    assert row['certified_max_output_tokens'] is None
    assert row['operating_max_output_tokens']==2048 and 'synthetic-secret' not in json.dumps(row)
    assert row['error_code']==('MODEL_GENERATION_CAPACITY_UNKNOWN' if thinking=='unknown' else '')


def test_image_read_reports_explicit_caption_identity_without_inheriting_global_role():
    runtime=ApplicationRuntime.__new__(ApplicationRuntime)
    runtime._owner=lambda principal:None;runtime.catalogue=SourceCatalogue()
    role=ConfiguredRole('vlm','deepseek','deepseek-flash','https://fixture.invalid',source='remote')
    async def load(model_id):
        assert model_id=='caption-selected';return {},{},role
    async def kb(principal,kb_id):return {'image_processing_config':{'caption_model_id':'caption-selected','max_output_tokens':0}}
    runtime.models=SimpleNamespace(_load=load);runtime.documents=SimpleNamespace(_kb=kb)
    row=asyncio.run(runtime.read_image_processing(object(),'kb'))
    assert row['capabilities']['selected_model']['model_id']=='caption-selected'
    assert row['capabilities']['selection_scope']=='explicit-caption-model'
    assert row['capabilities']['operating_max_output_tokens']==5000
    assert row['capabilities']['certified_max_output_tokens'] is None


def test_image_stale_model_can_be_read_as_unknown_for_reconfiguration_but_scope_is_not_softened():
    runtime=ApplicationRuntime.__new__(ApplicationRuntime)
    runtime._owner=lambda principal:None;runtime.catalogue=SourceCatalogue()
    async def kb(principal,kb_id):return {'image_processing_config':{'caption_model_id':'stale'}}
    async def load(model_id):raise ProviderError('MODEL_NOT_FOUND')
    runtime.models=SimpleNamespace(_load=load);runtime.documents=SimpleNamespace(_kb=kb)
    row=asyncio.run(runtime.read_image_processing(object(),'kb'))
    assert row['capabilities']['thinking']=='unknown'
    assert row['capabilities']['error_code']=='MODEL_NOT_FOUND'
    async def denied(model_id):raise ProviderError('MODEL_OWNER_MISMATCH')
    runtime.models._load=denied
    with pytest.raises(ProviderError,match='MODEL_OWNER_MISMATCH'):
        asyncio.run(runtime.read_image_processing(object(),'kb'))


def test_normal_asgi_generation_settings_includes_scoped_model_capabilities():
    from backend.weknora_port.http_api import create_app
    from tests.weknora_port.test_s88_generation_settings import SettingsDB, runtime
    from tests.weknora_port.test_turn_storage import PRINCIPAL
    app=runtime(SettingsDB())
    # Restore the actual capability method: the preferences fixture normally
    # projects no models because its SQL schema deliberately excludes them.
    app._answer_generation_capabilities=ApplicationRuntime._answer_generation_capabilities.__get__(app)
    app.policy=SimpleNamespace(mode='OFF');app.catalogue=SourceCatalogue()
    role=ConfiguredRole('cheap','siliconflow','Qwen/Qwen3.5-4B','https://fixture.invalid',source='remote')
    async def select(session,requested,scope):
        assert scope==('kb',);return 'actual-selected'
    async def load(model_id):return {},{},role
    app._normal_chat_model_id=select;app.models=SimpleNamespace(_load=load)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(app,
                resolve_principal=lambda _:PRINCIPAL,manage_background_workers=False)),
                base_url='http://test',headers={'Origin':'http://test'}) as client:
            response=await client.get('/api/v1/sessions/session/generation-settings')
            assert response.status_code==200
            row=response.json()['data']
            assert row['capabilities']['selected_model']['model_id']=='actual-selected'
            assert row['capabilities']['thinking']=='unknown'
            assert row['capabilities']['operating_max_output_tokens']==3072
            assert row['capabilities']['error_code']=='MODEL_GENERATION_CAPACITY_UNKNOWN'
    asyncio.run(scenario())
