"""Offline S8.8 envelope guards on the original HTTP acceptance composition."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.generation_settings import resolve_settings,apply_settings
from backend.weknora_port.models.types import ChatOptions,CallContext,TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.service import TurnConfiguration
from scripts import s8_6_1_api_acceptance as original
from scripts import s8_8_acceptance_profile as profile


def fixture(mode='quick', maximum=2048):
    frozen=resolve_settings({'max_completion_tokens':0},{},reasoning_mode=mode,max_output_tokens=maximum)
    options=apply_settings(ChatOptions(temperature=.7),frozen)
    cfg=TurnConfiguration(PipelineState(language='English',query='synthetic question',
        retrieval_strategy_mode='query_structure_v1',source_evidence_mode='bounded_source_v1',
        targets=(SearchTarget('kb','knowledge_base'),),query_understand_model_id='qu-id',
        bridge_cheap_model_id='cheap-id',rerank_model_id='rerank-id',rerank_top1_floorless=True),
        {'options':options,'generation_settings':deepcopy(frozen),'policy':RouterPolicy(mode='DYNAMIC'),
         'system_prompt':DEFAULT_SYSTEM_PROMPT},fallback={'prompt':'','options':options})
    turn=SimpleNamespace(run_id='server-run',client_request_id='client',
        request=SimpleNamespace(content='synthetic question',generation_settings=frozen,accept_language='en-US'),
        context=CallContext('server-run',TrustedScope('owner',('kb',))))
    approved={'request_id':'client','question':'synthetic question','mode':'query_structure_v1',
              'reasoning_mode':mode,'max_output_tokens':maximum}
    batch={'turns':[approved],'qu_model_id':'qu-id','model_ids':{'cheap':'cheap-id','rerank':'rerank-id'}}
    async def configuration(_turn):return cfg
    runtime=SimpleNamespace(ordinary=SimpleNamespace(configuration=configuration))
    return runtime,cfg,turn,batch


def install(runtime,batch,*,legacy=False,**kwargs):
    function=original.install_profile if legacy else profile.install_profile
    return function(runtime,approved_batch=batch,approved_scope={'knowledge_base_ids':['kb'],'document_ids':[]},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),**kwargs)


@pytest.mark.parametrize('mode,maximum',[('quick',64),('quick',2048),('smart',64),('smart',4096)])
def test_existing_approved_path_accepts_exact_frozen_modes_and_caps_without_option_override(mode,maximum):
    runtime,cfg,turn,batch=fixture(mode,maximum)
    before=deepcopy(cfg.generation_options)
    install(runtime,batch,shadow_routing_mode='shadow_structure_v1')
    result=asyncio.run(runtime.ordinary.configuration(turn))
    assert result.generation_options['options'] is cfg.generation_options['options']
    assert result.generation_options['policy'] is cfg.generation_options['policy']
    assert result.fallback is cfg.fallback
    assert cfg.generation_options==before
    assert result.generation_options['shadow_routing_mode']=='shadow_structure_v1'
    assert result.generation_options['citations_enabled'] is True
    assert result.generation_options['system_prompt']==DEFAULT_SYSTEM_PROMPT+'\n\n'+original.PROMPT_B_SUFFIX
    assert result.pipeline_state.retrieval_strategy_mode=='query_structure_v1'
    assert result.pipeline_state.source_evidence_mode=='bounded_source_v1'
    assert not result.pipeline_state.enable_expansion


@pytest.mark.parametrize('changes',[
    {'reasoning_mode':'agent'},{'reasoning_mode':None},{'max_output_tokens':True},
    {'max_output_tokens':0},{'max_output_tokens':-1},{'max_output_tokens':1.5},
    {'max_output_tokens':'2048'},
])
def test_invalid_approved_settings_refuse_without_configuration_mutation(changes):
    runtime,cfg,turn,batch=fixture()
    batch['turns'][0].update(changes)
    before=deepcopy(cfg.generation_options)
    install(runtime,batch)
    with pytest.raises(ValueError,match='S88_APPROVED_GENERATION_SETTINGS_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert cfg.generation_options==before


@pytest.mark.parametrize('target',['approval','frozen','resolved','options','thinking','effort'])
def test_mismatch_at_each_trust_boundary_is_rejected(target):
    runtime,cfg,turn,batch=fixture('smart',1000)
    if target=='approval':batch['turns'][0]['max_output_tokens']=1001
    elif target=='frozen':turn.request.generation_settings={}
    elif target=='resolved':cfg.generation_options['generation_settings']['effective']['max_output_tokens']=1001
    elif target=='options':cfg.generation_options['options']=replace(cfg.generation_options['options'],max_completion_tokens=1001)
    elif target=='thinking':cfg.generation_options['options']=replace(cfg.generation_options['options'],thinking=False)
    elif target=='effort':cfg.generation_options['options']=replace(cfg.generation_options['options'],reasoning_effort='off')
    install(runtime,batch)
    with pytest.raises(ValueError,match='S88_.*(MISMATCH|REQUIRED)'):
        asyncio.run(runtime.ordinary.configuration(turn))


def test_legacy_approved_contract_keeps_fixed_quick_2048_defaults():
    runtime,cfg,turn,batch=fixture()
    install(runtime,batch,legacy=True)
    result=asyncio.run(runtime.ordinary.configuration(turn))
    assert result.generation_options['options'] is cfg.generation_options['options']
    for mode,maximum in [('smart',2048),('quick',64)]:
        runtime,cfg,turn,batch=fixture(mode,maximum)
        install(runtime,batch,legacy=True)
        with pytest.raises(ValueError,match='S861_PREFLIGHT_GENERATION_OPTIONS_CHANGED'):
            asyncio.run(runtime.ordinary.configuration(turn))


@pytest.mark.parametrize('change',['request','scope','qu','expiry'])
def test_original_admission_guards_still_apply(change):
    runtime,cfg,turn,batch=fixture('smart',200)
    if change=='request':turn.request.content='unapproved question'
    elif change=='scope':turn.context=replace(turn.context,scope=TrustedScope('owner',('different-kb',)))
    elif change=='qu':cfg.pipeline_state.query_understand_model_id='other-qu'
    if change=='expiry':
        profile.install_profile(runtime,approved_batch=batch,approved_scope={'knowledge_base_ids':['kb'],'document_ids':[]},
            expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    else:install(runtime,batch)
    with pytest.raises(ValueError,match='S861_'):
        asyncio.run(runtime.ordinary.configuration(turn))


def test_profile_refuses_changed_production_boundaries_and_unknown_contract():
    _,cfg,_,_=fixture()
    with pytest.raises(ValueError,match='S88_SHADOW_ROUTING_MODE_INVALID'):
        profile.compose_configuration(cfg,shadow_routing_mode='active_dispatch')
    cfg.generation_options['policy']=RouterPolicy(mode='EXPENSIVE_ONLY')
    with pytest.raises(ValueError,match='S88_ORIGINAL_P5_REQUIRED'):
        profile.compose_configuration(cfg)
    runtime,*_=fixture()
    with pytest.raises(ValueError,match='S88_GENERATION_CONTRACT_INVALID'):
        original.install_profile(runtime,generation_contract='unknown')


@pytest.mark.parametrize('target',['snapshot','options'])
def test_boolean_token_values_do_not_match_integer_one(target):
    runtime,cfg,turn,batch=fixture('quick',1)
    if target=='snapshot':
        turn.request.generation_settings['effective']['max_output_tokens']=True
        cfg.generation_options['generation_settings']=deepcopy(turn.request.generation_settings)
    else:
        cfg.generation_options['options']=replace(cfg.generation_options['options'],max_completion_tokens=True)
    install(runtime,batch)
    with pytest.raises(ValueError,match='S88_.*MISMATCH'):
        asyncio.run(runtime.ordinary.configuration(turn))
