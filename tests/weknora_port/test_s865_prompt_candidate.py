"""Pure A/B/C rendering with frozen source messages and no scoring input."""
import copy
import json
from pathlib import Path

import pytest

from backend.weknora_port.generation.prompt_candidate import comparison_messages, render_comparison_arms, candidate_profile
from backend.weknora_port.models.types import ChatOptions
from backend.weknora_port.models import provider_config


ROOT = Path(__file__).resolve().parents[2]
FIXED = ROOT / 'docs/evaluation/evidence/s8_6_4/prompt_diagnostics/generation_requests/01-old.json'


def test_candidate_is_new_version_and_baseline_prompt_b_remains_byte_identical():
    raw = json.loads(FIXED.read_text(encoding='utf8'))['messages']
    saved = copy.deepcopy(raw)
    arms = comparison_messages(raw)
    assert raw == saved == arms['A']
    assert arms['B'] == arms['C'] and arms['B'][1:] == raw[1:]
    profile = candidate_profile()
    assert profile['version'] == 's8.6.5-generation-candidate/v1'
    assert arms['B'][0]['content'] == raw[0]['content'] + '\n\n' + profile['suffix']
    assert profile['baseline_prompt_b_suffix'] in arms['A'][0]['content']
    assert 'conditions' in profile['suffix'] and 'step order' in profile['suffix']
    assert 'units' in profile['suffix'] and 'claim' in profile['suffix']
    for marker in ('辐射', '22,000', 'Studio Deen', 'Cat vs Dog', '5726f5'):
        assert marker not in profile['suffix']


def test_abc_existing_adapters_keep_user_history_output_limit_and_thinking_contract():
    old = json.loads(FIXED.read_text(encoding='utf8'))
    arms = render_comparison_arms(old['messages'], ChatOptions(temperature=old['temperature'],
        send_temperature=True, max_completion_tokens=old['max_tokens'], thinking=False),
        cheap_config=provider_config('siliconflow', 'Qwen/Qwen3.5-4B'),
        expensive_config=provider_config('deepseek', 'deepseek-flash'))
    assert arms['A']['model'] == arms['B']['model'] == 'Qwen/Qwen3.5-4B'
    assert arms['C']['model'] == 'deepseek-flash'
    assert arms['B']['messages'] == arms['C']['messages']
    for arm in arms.values():
        assert arm['messages'][1:] == old['messages'][1:]
        assert arm['max_tokens'] == old['max_tokens'] == 2048
        assert arm['temperature'] == old['temperature']
    assert arms['A']['enable_thinking'] is arms['B']['enable_thinking'] is False
    assert arms['C']['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('messages', [[], [{'role': 'user', 'content': 'source'}],
    [{'role': 'system', 'content': 'unfrozen prompt'}]])
def test_unknown_baselines_are_rejected(messages):
    with pytest.raises(ValueError):
        comparison_messages(messages)


def test_already_modified_candidate_cannot_accumulate_suffixes():
    old = json.loads(FIXED.read_text(encoding='utf8'))['messages']
    with pytest.raises(ValueError, match='ALREADY_APPLIED'):
        comparison_messages(comparison_messages(old)['B'])


def test_renderer_requires_explicit_role_configs_instead_of_default_models():
    with pytest.raises(TypeError):
        render_comparison_arms([], ChatOptions())
