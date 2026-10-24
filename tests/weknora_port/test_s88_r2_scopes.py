"""Admission evidence for a single cohort with per-input document scopes."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.weknora_port.models.types import TrustedScope
from scripts import s8_8_acceptance_profile as profile
from test_s88_acceptance_profile import fixture


def install(runtime, batch, scopes):
    profile.install_profile(runtime, approved_batch=batch, approved_scopes=scopes,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))


def test_selected_document_scope_is_bound_to_trusted_client_before_configuration():
    runtime, cfg, turn, batch = fixture()
    turn.context = replace(turn.context, scope=TrustedScope('owner', ('kb',), ('doc-a',)))
    scopes = {'client': {'knowledge_base_ids': ['kb'], 'document_ids': ['doc-a']}}
    install(runtime, batch, scopes)
    # Caller mutation cannot expand the already-frozen authorization map.
    scopes['client']['document_ids'].append('doc-b')
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert result.generation_options['options'] is cfg.generation_options['options']
    turn.context = replace(turn.context, scope=TrustedScope('owner', ('kb',), ('doc-b',)))
    with pytest.raises(ValueError, match='S861_APPROVED_SCOPE_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))


@pytest.mark.parametrize('wrong', [TrustedScope('owner', ('other',), ('doc-a',)),
    TrustedScope('owner', ('kb',)), TrustedScope('owner', ('kb',), ('doc-a', 'doc-b'))])
def test_scope_mismatch_refuses_before_original_configuration(wrong):
    runtime, _, turn, batch = fixture()
    calls = []
    async def forbidden(_):
        calls.append(True)
        raise AssertionError('configuration and provider must not be reached')
    runtime.ordinary.configuration = forbidden
    install(runtime, batch, {'client': {'knowledge_base_ids': ['kb'], 'document_ids': ['doc-a']}})
    turn.context = replace(turn.context, scope=wrong)
    with pytest.raises(ValueError, match='S861_APPROVED_SCOPE_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert calls == []


def test_two_frozen_inputs_have_independent_scopes_and_keep_full_kb_control():
    runtime, _, turn, batch = fixture()
    other = deepcopy(batch['turns'][0])
    other['request_id'] = 'second-client'
    batch['turns'].append(other)
    install(runtime, batch, {
        'client': {'knowledge_base_ids': ['kb'], 'document_ids': ['doc-a']},
        'second-client': {'knowledge_base_ids': ['kb'], 'document_ids': []}})
    selected = deepcopy(turn)
    selected.context = replace(turn.context, scope=TrustedScope('owner', ('kb',), ('doc-a',)))
    asyncio.run(runtime.ordinary.configuration(selected))
    full = deepcopy(turn)
    full.run_id, full.client_request_id = 'second-run', 'second-client'
    asyncio.run(runtime.ordinary.configuration(full))
    full.run_id = 'third-run'
    with pytest.raises(ValueError, match='S861_CLIENT_REQUEST_ALREADY_BOUND'):
        asyncio.run(runtime.ordinary.configuration(full))


@pytest.mark.parametrize('scopes', [{}, {'unapproved': {'knowledge_base_ids': ['kb'], 'document_ids': []}},
    {'client': {'knowledge_base_ids': ['kb'], 'document_ids': ['doc-a', 'doc-a']}},
    {'client': {'knowledge_base_ids': ['kb'], 'document_ids': [None]}},
    {'client': {'knowledge_base_ids': ['kb', 'other'], 'document_ids': []}}])
def test_incomplete_or_ambiguous_scope_manifest_fails_closed(scopes):
    runtime, _, _, batch = fixture()
    with pytest.raises(ValueError, match='S88_APPROVED_SCOPES_REQUIRED'):
        install(runtime, batch, scopes)
