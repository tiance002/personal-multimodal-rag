"""Explicit fixed-role HTTP profile expectation; synthetic, zero Provider calls."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.generation.router import decide
from backend.weknora_port.generation_settings import apply_settings, resolve_settings
from backend.weknora_port.models.types import CallContext, ChatOptions, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.service import TurnConfiguration
from scripts import s8_8_acceptance_profile as profile


def fixture(mode='quick', router_mode='EXPENSIVE_ONLY'):
    snapshot = resolve_settings({}, {'context_config': {'generation': {'reasoning_mode': mode,
        'max_output_tokens': 2048, 'revision': 2}}})
    options = apply_settings(ChatOptions(temperature=.7), snapshot)
    policy = RouterPolicy(mode=router_mode)
    state = PipelineState(query='Synthetic factual question.', language='English',
        targets=(SearchTarget('synthetic-kb'),), query_understand_model_id='qu',
        bridge_cheap_model_id='cheap', rerank_model_id='rerank',
        retrieval_strategy_mode='query_structure_v1', source_evidence_mode='bounded_source_v1',
        rerank_top1_floorless=True)
    configuration = TurnConfiguration(state, {'options': options, 'policy': policy,
        'generation_settings': snapshot, 'system_prompt': DEFAULT_SYSTEM_PROMPT},
        fallback={'prompt': '', 'options': options})
    turn = SimpleNamespace(run_id='synthetic-server', client_request_id='synthetic-client',
        request=SimpleNamespace(content=state.query, generation_settings=snapshot,
            accept_language='en-US'),
        context=CallContext('synthetic-server', TrustedScope('synthetic-owner', ('synthetic-kb',))))
    batch = {'qu_model_id': 'qu', 'model_ids': {'cheap': 'cheap', 'rerank': 'rerank'},
        'turns': [{'request_id': 'synthetic-client', 'question': state.query,
            'mode': 'query_structure_v1', 'reasoning_mode': mode, 'max_output_tokens': 2048}]}
    async def resolve(_turn):
        return configuration
    runtime = SimpleNamespace(ordinary=SimpleNamespace(configuration=resolve))
    return runtime, configuration, turn, batch


def install(runtime, batch, **kwargs):
    return profile.install_profile(runtime, approved_batch=batch,
        approved_scope={'knowledge_base_ids': ['synthetic-kb'], 'document_ids': []},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), **kwargs)


@pytest.mark.parametrize('mode', ['quick', 'smart'])
def test_explicit_fixed_role_preserves_saved_settings_options_and_native_policy(mode):
    runtime, original, turn, batch = fixture(mode)
    install(runtime, batch, expected_router_mode='EXPENSIVE_ONLY')
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert result.generation_options['options'] is original.generation_options['options']
    assert result.generation_options['policy'] is original.generation_options['policy']
    assert result.generation_options['generation_settings'] is original.generation_options['generation_settings']
    assert result.fallback is original.fallback
    assert result.generation_options['options'].thinking is (mode == 'smart')
    assert result.generation_options['options'].completion_budget() == 2048
    assert turn.request.generation_settings['requested']['reasoning_mode'] is None
    assert turn.request.generation_settings['requested']['max_output_tokens'] is None
    assert turn.request.generation_settings['saved'] == {'reasoning_mode': mode, 'max_output_tokens': 2048}
    decision = decide(turn.request.content, 'Same synthetic context.', result.generation_options['policy'])
    assert decision.role == 'expensive' and decision.reason == 'OFFLINE_ARM'
    assert result.generation_options['citations_enabled'] is True


def test_old_default_still_requires_dynamic_and_accepts_dynamic_unchanged():
    runtime, original, turn, batch = fixture(router_mode='DYNAMIC')
    install(runtime, batch)
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert result.generation_options['policy'] is original.generation_options['policy']
    runtime, _, turn, batch = fixture()
    install(runtime, batch)
    with pytest.raises(ValueError, match='S88_ORIGINAL_P5_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))


@pytest.mark.parametrize('actual,expected', [
    ('DYNAMIC', 'EXPENSIVE_ONLY'), ('EXPENSIVE_ONLY', 'DYNAMIC')])
def test_expected_policy_cannot_force_or_replace_actual_policy(actual, expected):
    runtime, original, turn, batch = fixture(router_mode=actual)
    install(runtime, batch, expected_router_mode=expected)
    with pytest.raises(ValueError, match='S88_ORIGINAL_P5_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert original.generation_options['policy'].mode == actual


@pytest.mark.parametrize('mode', [None, False, 'OFF', 'CHEAP_ONLY', 'EXPENSIVE', 'dynamic'])
def test_unapproved_mode_rejected_before_profile_installation(mode):
    runtime, _, _, batch = fixture()
    original = runtime.ordinary.configuration
    with pytest.raises(ValueError, match='S88_APPROVED_ROUTER_MODE_INVALID'):
        install(runtime, batch, expected_router_mode=mode)
    assert runtime.ordinary.configuration is original
    assert not getattr(runtime, '_s861_profile_installed', False)


def test_fixed_role_keeps_existing_generation_mismatch_guard():
    runtime, original, turn, batch = fixture('smart')
    original.generation_options['options'] = replace(original.generation_options['options'], thinking=False)
    install(runtime, batch, expected_router_mode='EXPENSIVE_ONLY')
    with pytest.raises(ValueError, match='S88_RESOLVED_GENERATION_OPTIONS_MISMATCH'):
        asyncio.run(runtime.ordinary.configuration(turn))


@pytest.mark.parametrize('mode', ['quick', 'smart'])
def test_pure_companion_accepts_only_explicit_matching_fixed_policy(mode):
    _, original, _, _ = fixture(mode)
    result = profile.compose_configuration(original, expected_router_mode='EXPENSIVE_ONLY')
    assert result.generation_options['options'] is original.generation_options['options']
    assert result.generation_options['policy'] is original.generation_options['policy']
    with pytest.raises(ValueError, match='S88_ORIGINAL_P5_REQUIRED'):
        profile.compose_configuration(original)
