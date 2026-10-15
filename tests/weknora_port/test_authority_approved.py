"""Approved-mode authority/meter contract; closed, no network and no real provider.

Config and meter assets must live on an approved D/E path, so fixtures are written
under E:/WK/run with a per-run uuid directory.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from backend.weknora_port.authority import (
    ApprovedTokenAuthority, AuthoritySettings, ConfiguredTokenAuthority, MeterAsset, load_authority_settings)
from backend.weknora_port.models.types import CallContext, ModelConfig, ProviderError, PreparedCall, TrustedScope
from backend.weknora_port.source_facts import (
    BoundCallFacts, OperationSlot, SourceFacts, SourceSlice, TokenReservationBound,
    bind_wire_facts, build_call_sources, measure_final_input, sha)

ROOT = __import__('pathlib').Path('E:/WK/run')/'authority-approved-20261015'


def _paths():
    root = ROOT/uuid.uuid4().hex
    root.mkdir(parents=True)
    meter = root/'meter.bin'
    meter.write_bytes(b'approved-meter-asset')
    return root/'authority.json', meter, hashlib.sha256(meter.read_bytes()).hexdigest()


def _config(path, meter_path, meter_sha, **over):
    cfg = dict(schema='weknora-token-authority/v1', mode='approved', tenant_id=42,
        subject_id='owner-approved-'+uuid.uuid4().hex[:8], account_id='deepseek-account-main',
        provider='deepseek', expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        approval_source='OWNER:20261015-approved-token-meter',
        month=datetime.now(timezone.utc).date().replace(day=1).isoformat(),
        carry_in_tokens=0, carry_status='KNOWN', carry_source='LEDGER:port_token_calls:prior-month',
        limit_tokens=1_000_000, allowed_models=[['deepseek', 'deepseek-flash']],
        input_limit=200000, output_limit=8192,
        meter={'recipe': 'wire-bytes-upper-bound/v1', 'path': str(meter_path), 'sha256': meter_sha})
    cfg.update(over)
    path.write_text(json.dumps(cfg), encoding='utf-8')
    return cfg


def _approved_settings(**over):
    path, meter, meter_sha = _paths()
    _config(path, meter, meter_sha, **over)
    return load_authority_settings(path)


def _call(settings, *, model='deepseek-flash', provider='deepseek', simulated=False, owner=None, wire=b'{"a":1}'):
    slot = OperationSlot('op/answer/1/cheap', 'chat')
    scope = (settings.subject_id,)
    sources = SourceFacts(slot, (SourceSlice('question', 'SRC:q1', sha(wire), sha(wire), scope,
        'server-resolved/v1', '/'),), sha(wire))
    facts = BoundCallFacts(sources, TokenReservationBound(measure_final_input(settings.meter.recipe, wire), 64,
        settings.meter.recipe), settings.config_sha, 'fp', simulated)
    context = CallContext('op/answer/1/cheap', TrustedScope(owner or settings.subject_id, ('kb-1',)))
    return PreparedCall(context, provider, model, 'mid', 'chat', 'https://api.deepseek.com/chat/completions',
        sha(wire), len(wire), 64, facts)


def test_approved_loader_accepts_real_verified_config():
    settings = _approved_settings()
    assert settings.mode == 'approved' and settings.provider == 'deepseek'
    assert settings.limit_tokens == 1_000_000 and isinstance(settings.meter, MeterAsset)


@pytest.mark.parametrize('field,value,code', [
    ('subject_id', 'synthetic-owner', 'APPROVED_SYNTHETIC_IDENTITY_REJECTED'),
    ('account_id', 'synthetic-account', 'APPROVED_SYNTHETIC_IDENTITY_REJECTED'),
    ('approval_source', 'SYNTHETIC:closed', 'APPROVED_APPROVAL_SOURCE_REJECTED'),
    ('carry_source', 'synthetic-new-empty-account', 'APPROVED_CARRY_SOURCE_UNTRUSTED'),
    ('carry_status', 'UNKNOWN', 'APPROVED_CARRY_BOUND_REQUIRED'),
    ('limit_tokens', 999_999, 'APPROVED_PROVIDER_LIMIT_UNKNOWN'),
    ('provider', 'siliconflow', 'APPROVED_PROVIDER_LIMIT_UNKNOWN'),
    ('expires_at', '2020-01-01T00:00:00+00:00', 'AUTHORITY_EXPIRED'),
])
def test_approved_loader_fail_closed(field, value, code):
    with pytest.raises(ProviderError, match=code):
        _approved_settings(**{field: value})


def test_approved_loader_rejects_unverified_or_unsupported_meter():
    path, meter, meter_sha = _paths()
    unsupported = {'recipe': 'guess-tokens/v9', 'path': str(meter), 'sha256': meter_sha}
    with pytest.raises(ProviderError, match='APPROVED_METER_RECIPE_UNSUPPORTED'):
        _config(path, meter, meter_sha, meter=unsupported); load_authority_settings(path)
    bad_sha = {'recipe': 'wire-bytes-upper-bound/v1', 'path': str(meter), 'sha256': '0'*64}
    with pytest.raises(ProviderError, match='APPROVED_METER_ASSET_UNVERIFIED'):
        _config(path, meter, meter_sha, meter=bad_sha); load_authority_settings(path)
    missing = {'recipe': 'wire-bytes-upper-bound/v1', 'path': 'E:/WK/run/absent-meter.bin', 'sha256': '0'*64}
    with pytest.raises(ProviderError, match='APPROVED_METER_ASSET_UNVERIFIED'):
        _config(path, meter, meter_sha, meter=missing); load_authority_settings(path)


def test_measure_final_input_bound_and_refusals():
    assert measure_final_input('wire-bytes-upper-bound/v1', b'1234567') == 7
    with pytest.raises(ProviderError, match='TOKEN_BOUND_RECIPE_UNSUPPORTED'):
        measure_final_input('nope/v0', b'x')
    with pytest.raises(ProviderError, match='MODEL_WIRE_REQUIRED'):
        measure_final_input('wire-bytes-upper-bound/v1', b'')


def test_bind_wire_facts_requires_real_sources_and_matching_wire():
    settings = _approved_settings()
    config = ModelConfig(provider='deepseek', model='deepseek-flash', model_id='mid',
        base_url='https://api.deepseek.com')
    body = {'max_tokens': 64}
    wire = b'{"a":1}'
    sources = SourceFacts(OperationSlot('op1', 'chat'), (SourceSlice('question', 'SRC:q', sha(wire),
        sha(wire), (settings.subject_id,), 'server-resolved/v1', '/'),), sha(wire))
    bare = CallContext('op1', TrustedScope(settings.subject_id, ('kb-1',)))
    with pytest.raises(ProviderError, match='SOURCE_FACTS_REQUIRED'):
        bind_wire_facts(config, bare, body, wire, config_sha=settings.config_sha,
            recipe=settings.meter.recipe)
    from dataclasses import replace
    mismatched = replace(bare, source_facts=SourceFacts(sources.slot, sources.slices, sha(b'other')))
    with pytest.raises(ProviderError, match='WIRE_FACTS_MISMATCH'):
        bind_wire_facts(config, mismatched, body, wire, config_sha=settings.config_sha,
            recipe=settings.meter.recipe)
    facts = bind_wire_facts(config, replace(bare, source_facts=sources), body, wire,
        config_sha=settings.config_sha, recipe=settings.meter.recipe)
    assert facts.simulated is False and facts.bound.source == settings.meter.recipe
    assert facts.bound.input_upper == len(wire) and facts.bound.output_upper == 64


def test_build_call_sources_binds_real_elements_and_refuses_empty():
    settings = _approved_settings()
    from dataclasses import replace
    context = CallContext('op/answer/1/cheap', TrustedScope(settings.subject_id, ('kb-1',)),
        required_origin_kbs=('kb-2',))
    body = {'messages': [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'q'},
        {'role': 'assistant', 'content': 'a'}, {'role': 'user', 'content': 'f'}]}
    wire = b'{"messages":1}'
    facts = build_call_sources(context, body, wire, capability='chat')
    assert [s.kind for s in facts.slices] == ['system', 'question', 'history', 'history']
    assert facts.wire_sha256 == sha(wire)
    assert all(s.origin_kbs == ('kb-1', 'kb-2') for s in facts.slices)
    assert all(s.sent_value_sha256 == s.original_sha256 for s in facts.slices)
    embedding = build_call_sources(context, {'input': ['a', 'b']}, wire, capability='embedding')
    assert [s.kind for s in embedding.slices] == ['document', 'document']
    with pytest.raises(ProviderError, match='SOURCE_FACTS_REQUIRED'):
        build_call_sources(context, {'messages': []}, wire, capability='chat')
    config = ModelConfig(provider='deepseek', model='deepseek-flash', model_id='mid',
        base_url='https://api.deepseek.com')
    bound = bind_wire_facts(config, replace(context, source_facts=facts), {'max_tokens': 8}, wire,
        config_sha=settings.config_sha, recipe=settings.meter.recipe)
    assert bound.simulated is False and bound.sources.slices == facts.slices


def test_approved_authority_rejects_simulated_facts_and_maps_real_month():
    settings = _approved_settings()
    authority = ApprovedTokenAuthority(settings)
    authority.validate(_call(settings))  # real facts pass
    with pytest.raises(ProviderError, match='MODEL_BOUND_FACTS_REQUIRED'):
        authority.validate(_call(settings, simulated=True))
    with pytest.raises(ProviderError, match='MODEL_BOUND_FACTS_REQUIRED'):
        authority.validate(_call(settings, model='deepseek-v4-pro'))
    with pytest.raises(ProviderError, match='MODEL_BOUND_FACTS_REQUIRED'):
        authority.validate(_call(settings, owner='someone-else'))
    monthly = authority.monthly(_call(settings))
    assert monthly.account_id == 'deepseek-account-main' and monthly.provider == 'deepseek'
    assert monthly.limit_tokens == 1_000_000 and monthly.month == settings.month


def test_configured_synthetic_authority_still_refuses_approved_settings():
    settings = _approved_settings()
    with pytest.raises(ProviderError, match='TOKEN_MONTH_AUTHORITY_REQUIRED'):
        ConfiguredTokenAuthority(settings)


def test_approved_authority_requires_a_real_meter_asset():
    settings = _approved_settings()
    broken = AuthoritySettings(**{**settings.__dict__, 'meter': None})
    with pytest.raises(ProviderError, match='TOKEN_MONTH_AUTHORITY_REQUIRED'):
        ApprovedTokenAuthority(broken)
