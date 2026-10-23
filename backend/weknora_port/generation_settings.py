"""Server-owned answer settings, frozen before an ordinary turn starts.

These settings affect final generation only. The legacy execution ``mode``
continues to identify the ordinary RAG chain; it never selects the Agent API.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from collections.abc import Mapping

from .models.types import ChatOptions, ProviderError

SETTINGS_VERSION = 'answer-settings/v1'


def validate_settings(mode=None, maximum=None):
    if mode is not None and mode not in ('quick', 'smart'):
        raise ProviderError('GENERATION_REASONING_MODE_INVALID')
    if maximum is not None and (type(maximum) is not int or maximum < 0):
        raise ProviderError('GENERATION_OUTPUT_LIMIT_INVALID')


def session_settings(session):
    context = session.get('context_config') or {}
    if isinstance(context, str):
        context = json.loads(context)
    saved = context.get('generation') or {}
    if not isinstance(saved, Mapping):
        raise ProviderError('GENERATION_SETTINGS_INVALID')
    validate_settings(saved.get('reasoning_mode'), saved.get('max_output_tokens'))
    return dict(saved)


def resolve_settings(summary, session, *, reasoning_mode=None, max_output_tokens=None, reasoning_effort=''):
    validate_settings(reasoning_mode, max_output_tokens)
    saved = session_settings(session)
    # Retain the old explicit reasoning_effort input, without letting two
    # contradictory controls silently override one another.
    if reasoning_effort:
        from .conversation_configuration import reasoning_override
        effort = reasoning_override(reasoning_effort)
        if not effort:
            raise ProviderError('GENERATION_REASONING_MODE_INVALID')
        legacy_mode = 'quick' if effort == 'off' else 'smart'
        if reasoning_mode is not None and reasoning_mode != legacy_mode:
            raise ProviderError('GENERATION_REASONING_SETTINGS_CONFLICT')
        reasoning_mode = reasoning_mode or legacy_mode
    mode = reasoning_mode or saved.get('reasoning_mode') or 'quick'
    maximum = max_output_tokens if max_output_tokens is not None else saved.get('max_output_tokens', 0)
    validate_settings(mode, maximum)
    from .conversation_configuration import resolve_completion_limit
    completion, legacy = resolve_completion_limit(summary, mode=mode)
    default_limit = completion or legacy
    effective = maximum or default_limit
    version = hashlib.sha256(json.dumps({'schema': SETTINGS_VERSION, 'saved': saved,
        'defaults': {'quick': resolve_completion_limit(summary, mode='quick'),
                     'smart': resolve_completion_limit(summary, mode='smart')}}, sort_keys=True).encode()).hexdigest()
    return {'schema_version': SETTINGS_VERSION, 'configuration_version': version,
            'session_revision': int(saved.get('revision', 0)),
            'requested': {'reasoning_mode': reasoning_mode, 'max_output_tokens': max_output_tokens,
                          'reasoning_effort': reasoning_effort},
            'saved': {k: v for k, v in saved.items() if k != 'revision'},
            'effective': {'reasoning_mode': mode, 'max_output_tokens': effective},
            'defaults': {'reasoning_mode': 'quick', 'max_output_tokens': default_limit}}


def apply_settings(options: ChatOptions, snapshot):
    effective = snapshot['effective']
    mode, maximum = effective['reasoning_mode'], effective['max_output_tokens']
    validate_settings(mode, maximum)
    if maximum <= 0:
        raise ProviderError('GENERATION_OUTPUT_LIMIT_INVALID')
    return replace(options, max_tokens=0, max_completion_tokens=maximum,
                   reasoning_mode=mode, thinking=mode == 'smart',
                   reasoning_effort='auto' if mode == 'smart' else 'off')
