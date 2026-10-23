"""S8.8 opt-in composition of the existing bounded HTTP acceptance profile.

No runner, Provider adapter, grant, send gate or settings override. Approved
output caps are finite effective values, resolved before manifest approval.
"""
from dataclasses import replace
from collections.abc import Mapping

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.generation_settings import SETTINGS_VERSION
from backend.weknora_port.models.types import ChatOptions
from scripts import s8_6_1_api_acceptance as existing

GENERATION_CONTRACT = 's88-answer-settings/v1'


def validate_approved_generation(turn, configuration, approved):
    """Match the new approved envelope to trusted admission and resolved values."""
    require=existing.require
    mode, maximum=approved.get('reasoning_mode'),approved.get('max_output_tokens')
    require(mode in ('quick','smart') and type(maximum) is int and maximum>0,
            'S88_APPROVED_GENERATION_SETTINGS_REQUIRED')
    frozen=getattr(turn.request,'generation_settings',None)
    resolved=configuration.generation_options.get('generation_settings')
    require(isinstance(frozen,Mapping) and frozen.get('schema_version')==SETTINGS_VERSION
            and isinstance(frozen.get('configuration_version'),str)
            and bool(frozen['configuration_version']) and resolved==frozen,
            'S88_FROZEN_GENERATION_SETTINGS_REQUIRED')
    effective=frozen.get('effective')
    require(isinstance(effective,Mapping) and type(effective.get('max_output_tokens')) is int
            and effective=={'reasoning_mode':mode,'max_output_tokens':maximum},
            'S88_APPROVED_GENERATION_SETTINGS_MISMATCH')
    chat=configuration.generation_options.get('options')
    require(isinstance(chat,ChatOptions) and chat.reasoning_mode==mode
            and chat.thinking is (mode=='smart')
            and type(chat.max_tokens) is int and type(chat.max_completion_tokens) is int
            and chat.completion_budget()==maximum
            and chat.max_tokens in (0,maximum)
            and chat.max_completion_tokens in (0,maximum),
            'S88_RESOLVED_GENERATION_OPTIONS_MISMATCH')
    level,requested=chat.reasoning()
    require(requested and (level=='off' if mode=='quick' else level not in ('','off')),
            'S88_RESOLVED_GENERATION_OPTIONS_MISMATCH')
    return frozen


def _compose_resolved(configuration, *, shadow_routing_mode='off'):
    require=existing.require
    state=configuration.pipeline_state
    require(state.retrieval_strategy_mode=='query_structure_v1'
            and state.source_evidence_mode=='bounded_source_v1'
            and state.enable_expansion is False and state.graph_enabled is False
            and state.bridge_rescue_mode=='off', 'S88_SAVED_PROFILE_BOUNDARIES_REQUIRED')
    require(shadow_routing_mode in ('off','shadow_structure_v1'), 'S88_SHADOW_ROUTING_MODE_INVALID')
    options=dict(configuration.generation_options)
    require(options.get('policy')==RouterPolicy(mode='DYNAMIC'), 'S88_ORIGINAL_P5_REQUIRED')
    require(options.get('citations_enabled') is True
            and options.get('system_prompt','').count(existing.PROMPT_B_SUFFIX)==1,
            'S88_PROMPT_B_PROFILE_REQUIRED')
    options['shadow_routing_mode']=shadow_routing_mode
    return replace(configuration,generation_options=options)


def compose_configuration(configuration, *, shadow_routing_mode='off'):
    """Pure offline companion: Prompt B once, original options/policy preserved."""
    return _compose_resolved(existing.compose_configuration(configuration),shadow_routing_mode=shadow_routing_mode)


def install_profile(runtime, evidence_directory=None, *, shadow_routing_mode='off', **kwargs):
    """Retain original request/scope/model/expiry checks before S8.8 composition."""
    existing.require('generation_contract' not in kwargs, 'S88_GENERATION_CONTRACT_OWNED')
    existing.require(kwargs.get('bounded_query_profile',True) is True, 'S88_BOUNDED_QUERY_PROFILE_REQUIRED')
    kwargs['bounded_query_profile']=True
    existing.install_profile(runtime,evidence_directory,generation_contract=GENERATION_CONTRACT,**kwargs)
    original=runtime.ordinary.configuration
    async def configuration(turn):
        return _compose_resolved(await original(turn),shadow_routing_mode=shadow_routing_mode)
    runtime.ordinary.configuration=configuration
    return runtime
