"""S8.8 answer settings: real request rendering; synthetic HTTP/SQL, no egress."""
import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.generation_settings import resolve_settings, apply_settings
from backend.weknora_port.models import provider_config, ProviderModel
from backend.weknora_port.models.types import ChatOptions, Message, ProviderError
from backend.weknora_port.models.generation_capabilities import validate_generation_options
from backend.weknora_port.runtime import ApplicationRuntime, ConversationSettings
from backend.weknora_port.service import OrdinaryTurnRequest
from backend.weknora_port.storage.turns import PostgresTurnRepository
from backend.weknora_port.http_api import create_app
from tests.weknora_port.test_turn_storage import DB, Leases, Cursor, PRINCIPAL
from tests.weknora_port.test_generation import setup, request, event, CTX
from tests.weknora_port.test_http_api import FakeRuntime, PRINCIPAL as HTTP_PRINCIPAL


SUMMARY = {'max_completion_tokens': 0, 'temperature': .7}


def saved(mode='smart', maximum=3072):
    return {'context_config': {'generation': {'reasoning_mode': mode, 'max_output_tokens': maximum, 'revision': 2}}}


@pytest.mark.parametrize('mode', ['quick', 'smart'])
@pytest.mark.parametrize('provider,model,field', [
    ('siliconflow','Qwen/Qwen3.5-397B-A17B','enable_thinking'),
    ('deepseek','deepseek-flash','thinking'),
])
def test_real_provider_renderer_mode_and_cap(provider, model, field, mode):
    snapshot = resolve_settings(SUMMARY, saved(), reasoning_mode=mode, max_output_tokens=1234)
    options = apply_settings(ChatOptions(), snapshot)
    config = provider_config(provider, model)
    body = ProviderModel(config, None).build_request_body((Message('user','synthetic source question'),), options, stream=True)
    assert body['max_tokens'] == 1234
    assert body[field] == (mode == 'smart' if field == 'enable_thinking' else {'type': 'enabled' if mode == 'smart' else 'disabled'})
    mapping = validate_generation_options(config, options, body=body, stream=True)
    assert mapping['parameters']['max_tokens'] == 1234 and mapping['reasoning_mode'] == mode


def test_precedence_default_zero_and_freeze_do_not_mutate_session():
    session = saved(); before = copy.deepcopy(session)
    assert resolve_settings(SUMMARY, session)['effective'] == {'reasoning_mode':'smart','max_output_tokens':3072}
    frozen = resolve_settings(SUMMARY, session, reasoning_mode='quick', max_output_tokens=0)
    assert frozen['effective'] == {'reasoning_mode':'quick','max_output_tokens':2048}
    assert frozen['requested']['max_output_tokens'] == 0
    assert session == before
    session['context_config']['generation']['max_output_tokens'] = 9999
    assert frozen['effective']['max_output_tokens'] == 2048
    assert resolve_settings(SUMMARY, {})['effective'] == {'reasoning_mode':'quick','max_output_tokens':2048}
    assert resolve_settings(SUMMARY, {}, reasoning_mode='smart')['effective']['max_output_tokens'] == 4096


@pytest.mark.parametrize('maximum', [-1, True, 1.5, '100'])
def test_noninteger_or_negative_limits_are_rejected(maximum):
    with pytest.raises(ProviderError, match='OUTPUT_LIMIT_INVALID'):
        resolve_settings(SUMMARY, {}, max_output_tokens=maximum)


def test_conflicting_legacy_thinking_control_is_rejected():
    with pytest.raises(ProviderError, match='SETTINGS_CONFLICT'):
        resolve_settings(SUMMARY, {}, reasoning_mode='quick', reasoning_effort='high')


@pytest.mark.parametrize('semantics,raw', [
    ('openai-responses/v1',{'output_tokens_details':{'reasoning_tokens':77}}),
    ('google-generative-ai/v1',{'thoughtsTokenCount':77}),
])
def test_native_reported_reasoning_is_preserved_and_missing_stays_unknown(semantics,raw):
    from backend.weknora_port.models.usage_facts import capture_usage
    value=capture_usage(raw,semantics=semantics).get('reasoning')
    assert value.present and value.valid and value.value==77
    missing=capture_usage({},semantics=semantics).get('reasoning')
    assert not missing.present and missing.value is None


@pytest.mark.parametrize('change,code', [
    ({'max_output_tokens':0},'CAPACITY_UNKNOWN'),
    ({'context_window':0},'CAPACITY_UNKNOWN'),
    ({'max_output_tokens':100},'OUTPUT_LIMIT_EXCEEDED'),
    ({'context_window':1100},'CONTEXT_LIMIT_EXCEEDED'),
])
def test_declared_model_capacity_is_checked_without_clamping(change, code):
    config = replace(provider_config('deepseek','deepseek-flash'), **change)
    options = apply_settings(ChatOptions(), resolve_settings(SUMMARY, {}, max_output_tokens=1024))
    with pytest.raises(ProviderError, match=code):
        ProviderModel(config, None).build_request_body((Message('user','x'*200),), options)


@pytest.mark.parametrize('mode',['quick','smart'])
def test_actual_current_qwen35_4b_source_catalogue_refuses_unknown_capacity_without_send(mode):
    from contextlib import asynccontextmanager
    from backend.weknora_port.model_registry import SourceCatalogue
    from backend.weknora_port.storage.configuration import ConfiguredRole
    role=ConfiguredRole('cheap','siliconflow','Qwen/Qwen3.5-4B',
                        'https://api.siliconflow.cn/v1','',{},source='remote')
    config=SourceCatalogue().resolve(role,'actual-current-cheap')
    assert config.model=='Qwen/Qwen3.5-4B'
    assert (config.context_window,config.max_output_tokens,config.reasoning,config.cataloged)==(0,0,False,False)
    class Transport:
        opens=0
        @asynccontextmanager
        async def open(self,*args,**kwargs):
            self.opens+=1
            raise AssertionError('unknown hosted capacity must refuse before any send')
            yield
    transport=Transport()
    options=apply_settings(ChatOptions(),resolve_settings(SUMMARY,{},reasoning_mode=mode,max_output_tokens=2048))
    with pytest.raises(ProviderError,match='MODEL_GENERATION_CAPACITY_UNKNOWN') as failure:
        asyncio.run(ProviderModel(config,transport).chat((Message('user','synthetic capacity check'),),options,CTX))
    assert failure.value.sent is False and transport.opens==0


def test_smart_and_quick_refuse_unsupported_wire_without_send():
    config = provider_config('deepseek','deepseek-flash')
    options = apply_settings(ChatOptions(), resolve_settings(SUMMARY, {}, reasoning_mode='smart'))
    with pytest.raises(ProviderError, match='SMART_MODE_UNSUPPORTED'):
        ProviderModel(replace(config, reasoning=False), None).build_request_body((), options)
    options = replace(options, reasoning_mode='quick', thinking=False, reasoning_effort='off')
    with pytest.raises(ProviderError, match='QUICK_MODE_UNSUPPORTED'):
        ProviderModel(replace(config, chat=replace(config.chat,thinking_format='none')), None).build_request_body((), options)


@pytest.mark.parametrize('cap,reasoning,code',[(0,True,'CAPACITY_UNKNOWN'),(8192,False,'SMART_MODE_UNSUPPORTED')])
def test_local_vision_validates_before_ensure_or_any_send(cap,reasoning,code):
    config=replace(provider_config('deepseek','deepseek-flash'),source='local',
                   supports_vision=True,max_output_tokens=cap,reasoning=reasoning)
    class Service:
        base_url='http://127.0.0.1:11434';allow_pull=False
        async def ensure_model_available(self,*args,**kwargs): raise AssertionError('must validate before service I/O')
    model=ProviderModel(config,None,local_service=Service())
    options=apply_settings(ChatOptions(),resolve_settings(SUMMARY,{},reasoning_mode='smart'))
    with pytest.raises(ProviderError,match=code):
        asyncio.run(model.vision((Message('user','caption',images=('data:image/png;base64,eA==',)),),options,CTX))


def test_vision_context_uses_documented_image_bound_and_refuses_unknown_recipe():
    import io,base64
    from PIL import Image
    raw=io.BytesIO();Image.new('RGB',(1,1)).save(raw,format='PNG')
    image='data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()
    messages=(Message('user','caption',images=(image,)),)
    config=provider_config('deepseek','deepseek-flash')
    options=apply_settings(ChatOptions(),resolve_settings(SUMMARY,{},max_output_tokens=2048))
    body=ProviderModel(config,None).build_request_body(messages,options)
    audit=validate_generation_options(config,options,body=body,messages=messages)
    assert audit['input_bound_recipe']=='deepseek-inline-image-context-1024/v1'
    assert audit['input_token_upper_bound']>=1024
    with pytest.raises(ProviderError,match='CONTEXT_LIMIT_EXCEEDED'):
        ProviderModel(replace(config,context_window=2600),None).build_request_body(messages,options)
    with pytest.raises(ProviderError,match='INPUT_BOUND_NOT_APPLICABLE'):
        ProviderModel(replace(config,provider='unknown-provider'),None).build_request_body(messages,options)


def test_large_valid_image_does_not_count_base64_as_context_but_long_text_still_refuses():
    import io,base64,random
    from PIL import Image
    raw=io.BytesIO();Image.frombytes('RGB',(256,256),random.Random(88).randbytes(256*256*3)).save(raw,format='PNG')
    image='data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()
    config=replace(provider_config('deepseek','deepseek-flash'),context_window=12000)
    options=apply_settings(ChatOptions(),resolve_settings(SUMMARY,{},max_output_tokens=5000))
    assert len(image)>config.context_window
    model=ProviderModel(config,None)
    body=model.build_request_body((Message('user','caption',images=(image,)),),options)
    audit=validate_generation_options(config,options,body=body,messages=(Message('user','caption',images=(image,)),))
    assert audit['input_token_upper_bound']<2000
    with pytest.raises(ProviderError,match='CONTEXT_LIMIT_EXCEEDED'):
        model.build_request_body((Message('user','x'*12000,images=(image,)),),options)


@pytest.mark.parametrize('mode',['quick','smart'])
def test_upgrade_keeps_frozen_mode_and_limit_for_both_roles(mode):
    coordinator, cheap, expensive, store, _, _ = setup([event('partial',done=True,finish='length',final=True)])
    cheap.config = provider_config('siliconflow','Qwen/Qwen3.5-397B-A17B')
    from backend.weknora_port.generation.types import RoleBinding
    coordinator.bindings['cheap'] = RoleBinding('cheap',cheap.config.provider,cheap.config.model,cheap.config.base_url,cheap)
    snapshot = resolve_settings(SUMMARY, {}, reasoning_mode=mode,max_output_tokens=1536)
    options = apply_settings(ChatOptions(),snapshot)
    output = asyncio.run(coordinator.run(request(options=options,generation_settings=snapshot),CTX))
    assert output.upgraded
    assert len(cheap.calls)==len(expensive.calls)==1
    assert cheap.calls[0][1] is expensive.calls[0][1] is options
    settings_events=[e for e in store.events if e.type=='generation_settings']
    assert len(settings_events)==2
    assert all(e.data['effective']==snapshot['effective'] for e in settings_events)
    final=next(e for e in store.events if e.type=='complete')
    assert len(final.data['provider_parameter_mappings'])==2


class SettingsDB(DB):
    def __init__(self):
        super().__init__([[],[]])
        self.session={'id':'session','knowledge_base_scope':['kb'],'document_scope':[],**saved()}
    async def execute(self, sql, params=()):
        if sql.startswith('SELECT * FROM sessions'):
            return Cursor([copy.deepcopy(self.session)])
        if sql.startswith('UPDATE sessions SET context_config'):
            self.session['context_config']=json.loads(params[0])
            return Cursor([])
        return await super().execute(sql,params)


def runtime(db):
    value=object.__new__(ApplicationRuntime)
    value.principal=PRINCIPAL;value.leases=Leases(db)
    value.turns=PostgresTurnRepository(value.leases,PRINCIPAL,generation_defaults=SUMMARY)
    value.conversation=ConversationSettings()
    return value


def test_actual_settings_service_roundtrip_reset_and_admission_snapshot():
    async def scenario():
        db=SettingsDB(); app=runtime(db)
        row=await app.update_generation_settings(PRINCIPAL,'session',{'reasoning_mode':'quick','max_output_tokens':1250})
        assert row['session_revision']==3 and row['effective']['max_output_tokens']==1250
        # A new runtime facade proves storage, rather than an object cache, owns preferences.
        app=runtime(db)
        assert (await app.read_generation_settings(PRINCIPAL,'session'))['effective']==row['effective']
        accepted=await app.turns.accept(OrdinaryTurnRequest('session','synthetic question','client'),PRINCIPAL)
        frozen=accepted.request.generation_settings
        assert frozen['effective']==row['effective']
        db.answers=[]
        reset=await app.update_generation_settings(PRINCIPAL,'session',{'reset':True})
        assert reset['effective']=={'reasoning_mode':'quick','max_output_tokens':2048}
        assert frozen['effective']['max_output_tokens']==1250
        assert (await app.read_generation_settings(PRINCIPAL,'session'))['session_revision']==4
    asyncio.run(scenario())


def test_asgi_request_to_admission_to_actual_provider_body_and_legacy_agent_stays_separate():
    async def scenario():
        fake=FakeRuntime()
        app=create_app(fake,resolve_principal=lambda _: HTTP_PRINCIPAL)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Origin':'http://test'}) as client:
            body={'content':'synthetic source question','request_id':'client','mode':'quick',
                  'knowledge_base_scope':['kb'],'reasoning_mode':'smart','max_output_tokens':1600}
            response=await client.post('/api/v1/sessions/session/messages',json=body)
            assert response.status_code==200
            raw=fake.accept_calls[-1][1]
            accepted=await runtime(SettingsDB()).turns.accept(raw,PRINCIPAL)
            options=apply_settings(ChatOptions(),accepted.request.generation_settings)
            wire=ProviderModel(provider_config('deepseek','deepseek-flash'),None).build_request_body((Message('user',raw.content),),options)
            assert wire['thinking']=={'type':'enabled'} and wire['max_tokens']==1600
            body['mode']='smart'
            assert (await client.post('/api/v1/sessions/session/messages',json=body)).status_code==501
            for bad in (True,-1,1.2,'300'):
                body.update(mode='quick',max_output_tokens=bad)
                assert (await client.post('/api/v1/sessions/session/messages',json=body)).status_code==422
    asyncio.run(scenario())
