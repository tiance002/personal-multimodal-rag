"""No sockets or Provider sends: the new composition preserves formal contracts."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.service import TurnConfiguration
from scripts import s8_6_5_http_acceptance as profile
from scripts.s8_6_1_api_acceptance import PROMPT_B_SUFFIX


def test_candidate_profile_changes_only_generation_text_and_keeps_p5_and_sources():
    policy = RouterPolicy(mode='DYNAMIC')
    base = TurnConfiguration(PipelineState(retrieval_strategy_mode='query_structure_v1',source_evidence_mode='bounded_source_v1'),
        {'system_prompt':DEFAULT_SYSTEM_PROMPT,'policy':policy},fallback={'strategy':'model'})
    changed = profile.compose_configuration(base)
    assert changed.generation_options['system_prompt'] == (
        DEFAULT_SYSTEM_PROMPT+'\n\n'+PROMPT_B_SUFFIX+'\n\n'+profile.candidate_profile()['suffix'])
    assert changed.generation_options['citations_enabled'] is True
    assert changed.generation_options['policy'] is policy and changed.fallback == base.fallback
    assert changed.pipeline_state.retrieval_strategy_mode=='query_structure_v1'
    assert changed.pipeline_state.source_evidence_mode=='bounded_source_v1'
    assert not changed.pipeline_state.enable_expansion
    assert base.generation_options['system_prompt']==DEFAULT_SYSTEM_PROMPT


def test_wrapper_reuses_original_profile_before_candidate(monkeypatch):
    calls=[]
    base=TurnConfiguration(PipelineState(retrieval_strategy_mode='query_structure_v1',source_evidence_mode='bounded_source_v1'),{'system_prompt':DEFAULT_SYSTEM_PROMPT})
    async def original(turn):
        calls.append('original')
        return base
    runtime=SimpleNamespace(ordinary=SimpleNamespace(configuration=original))
    def install(runtime,directory,**kwargs):
        calls.append('existing-install')
        previous=runtime.ordinary.configuration
        async def configured(turn):
            return profile.compose_prompt_b(await previous(turn))
        runtime.ordinary.configuration=configured
    monkeypatch.setattr(profile,'install_existing_profile',install)
    profile.install_profile(runtime)
    result=asyncio.run(runtime.ordinary.configuration(SimpleNamespace(run_id='new')))
    assert calls==['existing-install','original']
    assert result.generation_options['system_prompt'].count(profile.candidate_profile()['suffix'])==1


def test_formal_factory_rejects_nonisolated_prefix_before_runtime_or_send():
    with pytest.raises(ValueError,match='S865_ISOLATED_REDIS_PREFIX_REQUIRED'):
        profile.create_application({'WEKNORA_PORT_REDIS_PREFIX':'production'})


def test_http_grant_must_bind_exact_child_ledger_envelope_before_send():
    grant={'max_calls':12,'max_tokens':100000}
    profile.validate_limits(grant,grant)
    for malformed in ({},{'max_calls':True,'max_tokens':100000},
                      {'max_calls':13,'max_tokens':100000},{'max_calls':12,'max_tokens':0}):
        with pytest.raises(ValueError,match='S865_EXPLICIT_GRANT_LIMITS_REQUIRED'):
            profile.validate_limits(malformed)
    for larger in ({'max_calls':12,'max_tokens':500000},{'max_calls':24,'max_tokens':100000}):
        with pytest.raises(ValueError,match='S865_GRANT_LEDGER_LIMIT_MISMATCH'):
            profile.validate_limits(grant,larger)
