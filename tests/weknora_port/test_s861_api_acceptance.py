"""S8.6.1 acceptance composition, actual ASGI routes and mock SQL only."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from scripts import s8_6_1_api_acceptance as profile
from scripts.s8_ecs_runtime import PrivateApiGuard
from backend.weknora_port.generation import Evidence, GenerationRequest, RouterPolicy, prepare_generation
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT, SOURCE_DATA_BOUNDARY, SOURCED_ANSWER_OUTPUT
from backend.weknora_port.http_api import MessageCreate, SessionCreate, create_app
from backend.weknora_port.models.types import CallContext, ChatOptions, TrustedScope
from backend.weknora_port.retrieval.context import FinalizedContext
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.service import TurnConfiguration
from test_s86_qu_configuration import assembled_runtime


# Independent literal copied from the Owner's S8.6.1 block, not from the script.
EXPECTED_SUFFIX = (
    'When the supplied evidence contains sufficient information, perform the necessary verifiable calculation or logical inference instead of refusing solely because the result is not stated verbatim.\n'
    'Distinguish derived conclusions from directly stated facts. Do not invent missing inputs, entities, dates, locations, or relationships. If the evidence does not support a required relation, explicitly state the uncertainty.')
EXPECTED_SUFFIX_SHA256 = '4501caef3b4b0d5b0de09e00e51d1ca9d50332637637e52ffc45ee90e2dc726b'


def configuration():
    state=PipelineState(query='Original q0',rewrite_query='Original q0',
        enable_expansion=True,source_evidence_mode='bounded_source_v1',
        chat_model_id='baseline-id',query_understand_model_id='qu-id',
        rerank_model_id='rerank-id',bridge_cheap_model_id='cheap-id',
        max_rounds=3,rerank_threshold=.27,rerank_top_k=9,
        retrieval_strategy_mode='query_structure_v1',query_type='UNKNOWN')
    policy=RouterPolicy(mode='DYNAMIC')
    options=ChatOptions(temperature=.23,max_completion_tokens=777,thinking=False)
    return TurnConfiguration(state,{'system_prompt':'Original resolved system {{contexts}}',
        'citations_enabled':False,'policy':policy,'options':options},
        fallback={'strategy':'model','response':'old response','prompt':'old fallback',
                  'options':ChatOptions(temperature=.11,max_completion_tokens=333)})


def test_prompt_b_is_exact_owner_text_and_hash_without_changing_generation_defaults():
    assert profile.PROMPT_B_SUFFIX == EXPECTED_SUFFIX
    assert hashlib.sha256(profile.PROMPT_B_SUFFIX.encode('utf-8')).hexdigest()==EXPECTED_SUFFIX_SHA256
    assert not profile.PROMPT_B_SUFFIX.endswith('\n')
    assert GenerationRequest.__dataclass_fields__['citations_enabled'].default is False
    assert GenerationRequest.__dataclass_fields__['system_prompt'].default==DEFAULT_SYSTEM_PROMPT


def test_profile_copies_current_configuration_and_changes_only_approved_fields():
    cfg=configuration(); before=deepcopy(vars(cfg.pipeline_state)); original_options=dict(cfg.generation_options)
    result=profile.compose_configuration(cfg)
    assert result is not cfg and result.pipeline_state is not cfg.pipeline_state
    assert result.generation_options is not cfg.generation_options
    assert cfg.generation_options==original_options and vars(cfg.pipeline_state)==before
    expected_state=dict(before);expected_state['enable_expansion']=False
    assert vars(result.pipeline_state)==expected_state
    assert result.generation_options=={**original_options,'citations_enabled':True,
        'system_prompt':original_options['system_prompt']+'\n\n'+EXPECTED_SUFFIX}
    assert result.generation_options['policy'] is cfg.generation_options['policy']
    assert result.generation_options['options'] is cfg.generation_options['options']
    assert result.fallback is cfg.fallback


@pytest.mark.parametrize('mode', ['off','query_structure_v1',None])
def test_actual_asgi_session_save_readback_selects_bounded_arms_without_overriding_mode(monkeypatch, mode):
    runtime,stored,calls=assembled_runtime(monkeypatch)
    original_normal=runtime._configuration
    normal_results=[]
    async def capture_normal(turn):
        value=await original_normal(turn)
        normal_results.append(value)
        return value
    runtime.ordinary=SimpleNamespace(configuration=capture_normal)
    profile.install_profile(runtime,evidence_directory=None)
    assert runtime.ordinary.configuration!=original_normal
    app=PrivateApiGuard(create_app(runtime,resolve_principal=lambda request:runtime.principal,
        manage_background_workers=False),'t'*40)
    body={'knowledge_base_scope':['kb'],'document_scope':['doc'],
          'source_evidence_mode':'bounded_source_v1'}
    if mode is not None:body['retrieval_strategy_mode']=mode
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
            headers={'Origin':'http://test'}) as client:
            denied=await client.post('/api/v1/sessions',json=body)
            assert denied.status_code==401 and calls==[]
            result=await client.post('/api/v1/sessions',json=body,
                headers={'Authorization':'Bearer '+'t'*40})
            assert result.status_code==200
        turn=SimpleNamespace(session_id=stored['id'],run_id='run',
            context=CallContext('run',TrustedScope('synthetic-subject',('kb',),('doc',))),
            request=SimpleNamespace(summary_model_id='',reasoning_effort='off',accept_language='zh-CN',knowledge_base_scope=('kb',)))
        baseline=await original_normal(turn)
        candidate=await runtime.ordinary.configuration(turn)
        return baseline,candidate
    baseline,candidate=asyncio.run(exercise())
    saved=stored['context_config']['conversation']
    assert saved['source_evidence_mode']=='bounded_source_v1'
    assert saved.get('retrieval_strategy_mode','off')==(mode or 'off')
    assert candidate.pipeline_state.retrieval_strategy_mode==(mode or 'off')
    assert candidate.pipeline_state.source_evidence_mode=='bounded_source_v1'
    assert candidate.pipeline_state.enable_expansion is False
    assert baseline.pipeline_state.enable_expansion is True
    assert candidate.generation_options['citations_enabled'] is True
    assert candidate.generation_options['system_prompt']==baseline.generation_options['system_prompt']+'\n\n'+EXPECTED_SUFFIX
    assert candidate.generation_options['policy']==baseline.generation_options['policy']
    assert candidate.generation_options['options']==baseline.generation_options['options']
    assert candidate.pipeline_state.chat_model_id==baseline.pipeline_state.chat_model_id=='cheap-id'
    assert candidate.pipeline_state.rerank_model_id==baseline.pipeline_state.rerank_model_id=='rerank-id'
    assert candidate.pipeline_state.bridge_rescue_mode==baseline.pipeline_state.bridge_rescue_mode=='off'
    # Each ordinary resolution creates a fresh documents closure. Check the
    # exact source result of this composition, then compare stable settings.
    assert candidate.fallback is normal_results[-1].fallback
    assert {k:v for k,v in candidate.fallback.items() if k!='documents'}=={
        k:v for k,v in baseline.fallback.items() if k!='documents'}
    assert callable(candidate.fallback['documents'])
    assert runtime.conversation.retrieval_strategy_mode=='off'
    assert runtime.conversation.enable_query_expansion is True


def test_actual_asgi_illegal_pair_is_rejected_before_lease_or_model_lookup(monkeypatch):
    runtime,stored,calls=assembled_runtime(monkeypatch)
    runtime.ordinary=SimpleNamespace(configuration=runtime._configuration)
    profile.install_profile(runtime,evidence_directory=None)
    app=create_app(runtime,resolve_principal=lambda request:runtime.principal,manage_background_workers=False)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Origin':'http://test'}) as client:
            return await client.post('/api/v1/sessions',json={'knowledge_base_scope':['kb'],
                'retrieval_strategy_mode':'query_structure_v1'})
    response=asyncio.run(exercise())
    assert response.status_code==409
    assert response.json()['error']['code']=='RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE'
    assert calls==[] and stored=={}


@pytest.mark.parametrize('extra',[{'citations_enabled':True},{'system_prompt':'forged prompt'},
    {'generation_options':{'citations_enabled':True}},{'query_type':'SIMPLE_COMPARE'}])
def test_acceptance_profile_does_not_expand_client_configuration_authority(extra):
    with pytest.raises(ValidationError):
        SessionCreate(knowledge_base_scope=['kb'],**extra)
    with pytest.raises(ValidationError):
        MessageCreate(content='q',request_id='req',knowledge_base_scope=['kb'],**extra)


def test_prompt_b_reaches_actual_prepared_system_with_existing_source_protocol():
    cfg=profile.compose_configuration(configuration())
    text='Original current source content.'
    finalized=FinalizedContext('<context id="1">'+text+'</context>\nOriginal q0',
        '<context id="1">'+text+'</context>',('chunk-original',))
    evidence=Evidence('chunk-original',text,'doc','kb',source_content=text)
    request=GenerationRequest('run','session','answer','Original q0',finalized,
        evidence=(evidence,),**cfg.generation_options)
    prepared=prepare_generation(request)
    system=prepared.messages[0].content
    assert EXPECTED_SUFFIX in system and system.count(EXPECTED_SUFFIX)==1
    assert SOURCE_DATA_BOUNDARY in system and SOURCED_ANSWER_OUTPUT in system
    assert 'Source citations are enabled' in system and '<ref id="cN"/>' in system
    assert 'Original resolved system' in system
    assert any(text in message.content for message in prepared.messages)
    snapshot=prepared.registry.snapshot()
    assert any(ref[0]=='chunk-original' for _,ref in snapshot.chunks)
    assert snapshot.citable


def test_unknown_real_qu_output_falls_back_to_one_plain_retrieval_under_profile():
    from test_s86_pipeline_integration import Repo,Models,Registry,state,CTX
    class Unknown(Models):
        async def chat(self,*args):
            response=await super().chat(*args)
            data=json.loads(response.content);data['query_type']='UNSUPPORTED_TYPE'
            return SimpleNamespace(content=json.dumps(data),finish_reason='stop')
    current=state();q0=current.query
    cfg=profile.compose_configuration(TurnConfiguration(current,{'system_prompt':'resolved','policy':RouterPolicy(mode='DYNAMIC')}))
    repository,models=Repo(),Unknown()
    final=asyncio.run(RetrievalPipeline(repository,Registry(models)).run(cfg.pipeline_state,CTX))
    assert cfg.pipeline_state.query_type=='UNKNOWN'
    assert cfg.pipeline_state.strategy_plan.strategy=='PLAIN'
    assert len(models.qu)==len(repository.calls)==len(models.ranks)==1
    assert repository.calls[0][0]==models.ranks[0][0]==q0
    assert cfg.pipeline_state.query==cfg.pipeline_state.rewrite_query==q0
    assert q0 in final.user_content


def test_acceptance_application_delegates_existing_ecs_factory_without_changing_settings_or_principal(monkeypatch):
    import scripts.s8_ecs_runtime as base
    active,principal=object(),object()
    async def normal_configuration(turn):return configuration()
    runtime=SimpleNamespace(ordinary=SimpleNamespace(configuration=normal_configuration),
        graph_enabled=False,conversation=SimpleNamespace(bridge_rescue_mode='off'))
    calls=[]
    @asynccontextmanager
    async def assemble(settings,*,principal):
        calls.append((settings,principal))
        yield runtime
    original_env={'SYNTHETIC_TEST_ONLY':'retained',
        'WEKNORA_PORT_REDIS_PREFIX':'weknora-python-s8:s861:mock-test',
        'WEKNORA_PORT_CLOUD_ALLOWED':'false','WEKNORA_PORT_ROUTER_MODE':'DYNAMIC',
        'WEKNORA_PORT_GRAPH_ENABLED':'false','S8_COMPONENT':'api'}
    sentinel=object();captured={}
    def existing_create_application(environment=None,*,assemble_factory=None):
        captured.update(environment=environment,assemble_factory=assemble_factory)
        return sentinel
    monkeypatch.setattr(base,'create_application',existing_create_application)
    result=profile.create_application(original_env,assemble_factory=assemble)
    assert result is sentinel and captured['environment']==original_env
    async def exercise():
        async with captured['assemble_factory'](active,principal=principal) as seen:
            assert seen is runtime
            cfg=seen.ordinary.configuration(SimpleNamespace(run_id='mock-run'))
            if hasattr(cfg,'__await__'):cfg=await cfg
            assert cfg.generation_options['citations_enabled'] is True
    asyncio.run(exercise())
    assert calls==[(active,principal)]
