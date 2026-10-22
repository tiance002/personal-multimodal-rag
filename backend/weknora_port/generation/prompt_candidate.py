"""Versioned experimental prompt rendering. No transport or scoring inputs.

A preserves the caller's frozen Prompt B. B appends the candidate to Cheap;
C gives Expensive the identical candidate messages. No runtime default changes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ..models.types import Message


def candidate_profile():
    value = json.loads((Path(__file__).parent / 'assets/s865_candidate_v1.json').read_text(encoding='utf8'))
    if hashlib.sha256(value['baseline_prompt_b_suffix'].encode('utf8')).hexdigest() != value['baseline_prompt_b_suffix_sha256']:
        raise ValueError('S865_BASELINE_PROMPT_B_HASH_MISMATCH')
    value['suffix_sha256'] = hashlib.sha256(value['suffix'].encode('utf8')).hexdigest()
    return value


def comparison_messages(raw_messages):
    """Return independent message copies; never read a dataset or Gold sidecar."""
    old = copy.deepcopy(list(raw_messages))
    if (not old or old[0].get('role') != 'system'
            or any(set(m) != {'role', 'content'} or not isinstance(m['content'], str) for m in old)):
        raise ValueError('S865_FROZEN_TEXT_MESSAGES_REQUIRED')
    profile = candidate_profile()
    if profile['baseline_prompt_b_suffix'] not in old[0]['content']:
        raise ValueError('S865_FROZEN_PROMPT_B_REQUIRED')
    if profile['suffix'] in old[0]['content']:
        raise ValueError('S865_CANDIDATE_ALREADY_APPLIED')
    candidate = copy.deepcopy(old)
    candidate[0]['content'] += '\n\n' + profile['suffix']
    return {'A': old, 'B': candidate, 'C': copy.deepcopy(candidate)}


def render_comparison_arms(raw_messages, options, *, cheap_config, expensive_config):
    """Build only wire bodies through existing adapters, without a client/send."""
    from ..models.provider import ProviderModel
    from ..models.types import ModelConfig
    if not isinstance(cheap_config, ModelConfig) or not isinstance(expensive_config, ModelConfig):
        raise ValueError('S865_RESOLVED_ROLE_CONFIGS_REQUIRED')
    cheap = ProviderModel(cheap_config, None)
    expensive = ProviderModel(expensive_config, None)
    return {arm: (expensive if arm == 'C' else cheap).build_request_body(
        tuple(Message(m['role'], m['content']) for m in messages), options, stream=True)
        for arm, messages in comparison_messages(raw_messages).items()}
