"""Directed offline tests for the output-budget contract and reasoning usage.

No network, no real provider. Config/meter assets must live on an approved D/E
path, so fixtures are written under E:/WK/run with a per-run uuid directory.
"""
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone, timedelta

import pytest

from backend.weknora_port.authority import (
    ApprovedTokenAuthority, load_authority_settings)
from backend.weknora_port.models import output_contract as oc
from backend.weknora_port.models.output_contract import (
    CAP_CONTRACTS, FULL_COMPLETION, VISIBLE_PLUS_REASONING, OutputBudgetContract,
    registry_cap_field, resolve_output_contract)
from backend.weknora_port.models.types import (
    CallContext, ChatSettings, ModelConfig, ProviderError, PreparedCall, TrustedScope)
from backend.weknora_port.source_facts import (
    BoundCallFacts, OperationSlot, SourceFacts, SourceSlice, TokenReservationBound,
    bind_wire_facts, measure_final_input, sha)
from backend.weknora_port.models.usage_facts import capture_usage
from backend.weknora_port.storage.token_budget import same_month_authority

ROOT = __import__('pathlib').Path('E:/WK/run')/'output-bound-contract-20261015'


def _deepeek_config(**chat):
    return ModelConfig(provider='deepseek', model='deepseek-flash', model_id='mid',
        base_url='https://api.deepseek.com', chat=ChatSettings(**chat))


def _facts(config, body, wire=b'{"a":1}', *, capability='chat', input_upper=None):
    sources = SourceFacts(OperationSlot('op/answer/1/cheap', capability),
        (SourceSlice('question', 'SRC:q', sha(wire), sha(wire), ('owner',), 'server-resolved/v1', '/'),),
        sha(wire))
    bound = TokenReservationBound(len(wire) if input_upper is None else input_upper, 0, 'x')
    return BoundCallFacts(sources, bound, 'cfg', 'fp', False)


# ---------------------------------------------------------------- A: params ---

def test_registry_declares_max_tokens_and_resolved_config_matches():
    assert registry_cap_field('deepseek', 'openai-completions') == 'max_tokens'
    config = _deepeek_config(max_tokens_field='max_tokens')
    contract = resolve_output_contract(config, {'max_tokens': 64})
    assert contract.cap_field == 'max_tokens' and contract.cap_value == 64
    assert contract.case == 'A' and contract.output_upper == 64 and contract.reasoning_included


def test_non_contract_cap_field_is_refused_not_silently_accepted():
    # The observed failure combination: a hand-built config that bypassed
    # resolve_config renders the vendor's non-contract field.
    config = _deepeek_config()  # default max_tokens_field = max_completion_tokens
    assert config.chat.max_tokens_field == 'max_completion_tokens'
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_HARD_LIMIT_DENIED'):
        resolve_output_contract(config, {'max_completion_tokens': 64})


def test_thinking_mode_is_recorded_from_the_wire_never_inferred():
    config = _deepeek_config(max_tokens_field='max_tokens', thinking_format='thinking-type')
    enabled = resolve_output_contract(config, {'max_tokens': 64, 'thinking': {'type': 'enabled'}})
    disabled = resolve_output_contract(config, {'max_tokens': 64, 'thinking': {'type': 'disabled'}})
    unspecified = resolve_output_contract(config, {'max_tokens': 64})
    assert (enabled.thinking_mode, disabled.thinking_mode, unspecified.thinking_mode) == (
        'enabled', 'disabled', 'unspecified')
    # The cap still bounds the whole completion in every thinking mode.
    assert enabled.output_upper == disabled.output_upper == unspecified.output_upper == 64


def test_case_b_sums_two_verifiable_hard_caps(monkeypatch):
    monkeypatch.setitem(CAP_CONTRACTS, ('fakevis', 'openai-completions'),
        ('max_tokens', VISIBLE_PLUS_REASONING, 'reasoning_budget'))
    config = ModelConfig(provider='fakevis', model='m', model_id='m',
        base_url='https://example.invalid', chat=ChatSettings(max_tokens_field='max_tokens'))
    contract = resolve_output_contract(config, {'max_tokens': 10, 'reasoning_budget': 5})
    assert contract.case == 'B' and contract.output_upper == 15
    assert contract.cap_value == 10 and contract.reasoning_upper == 5
    # A separated upper bound must never be minted from a missing hard cap.
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_HARD_LIMIT_DENIED'):
        resolve_output_contract(config, {'max_tokens': 10})


def test_case_d_no_verifiable_total_upper_is_refused():
    config = ModelConfig(provider='somevendor', model='m', model_id='m',
        base_url='https://example.invalid')
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_CONTRACT_UNKNOWN'):
        resolve_output_contract(config, {'max_tokens': 64})
    anthropic = ModelConfig(provider='deepseek', model='deepseek-flash', model_id='mid',
        base_url='https://api.deepseek.com/anthropic', api='anthropic-messages')
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_CONTRACT_UNKNOWN'):
        resolve_output_contract(anthropic, {'max_tokens': 64})


def test_registry_drift_between_table_and_pinned_reference_is_refused(monkeypatch):
    monkeypatch.setitem(CAP_CONTRACTS, ('deepseek', 'openai-completions'),
        ('max_completion_tokens', FULL_COMPLETION, None))
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_CAP_FIELD_DRIFT'):
        resolve_output_contract(_deepeek_config(), {'max_completion_tokens': 64})


def test_cap_must_be_a_positive_int_and_non_generative_capability_is_zero():
    config = _deepeek_config(max_tokens_field='max_tokens')
    for bad in (0, -1, None, '64', 64.0):
        with pytest.raises(ProviderError, match='MODEL_OUTPUT_HARD_LIMIT_DENIED'):
            resolve_output_contract(config, {} if bad is None else {'max_tokens': bad})
    embedding = resolve_output_contract(config, {'input': ['x']}, capability='embedding')
    assert embedding.output_upper == 0 and embedding.case == 'A'


def test_bind_wire_facts_refuses_a_non_contract_wire_and_keeps_v1_numbers():
    config = _deepeek_config(max_tokens_field='max_tokens')
    wire = b'{"max_tokens":64}'
    context = CallContext('op/answer/1/cheap', TrustedScope('owner', ('kb-1',)))
    facts = _facts(config, {'max_tokens': 64}, wire)
    bound = bind_wire_facts(config, replace(context, source_facts=facts.sources), {'max_tokens': 64},
        wire, 'chat', config_sha='cfg', recipe='wire-bytes-upper-bound/v1')
    assert bound.bound.output_upper == 64 and bound.bound.source == 'wire-bytes-upper-bound/v1'
    assert isinstance(bound.output_contract, OutputBudgetContract) and bound.output_contract.case == 'A'
    bad = _deepeek_config()
    bad_wire = b'{"max_completion_tokens":64}'
    bad_facts = _facts(bad, {'max_completion_tokens': 64}, bad_wire)
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_HARD_LIMIT_DENIED'):
        bind_wire_facts(bad, replace(context, source_facts=bad_facts.sources),
            {'max_completion_tokens': 64}, bad_wire, 'chat',
            config_sha='cfg', recipe='wire-bytes-upper-bound/v1')
    with pytest.raises(ProviderError, match='TOKEN_BOUND_RECIPE_UNSUPPORTED'):
        bind_wire_facts(config, replace(context, source_facts=facts.sources), {'max_tokens': 64},
            wire, 'chat', config_sha='cfg', recipe='guess/v9')


# -------------------------------------------------------------- B: metering ---

def test_old_record_output_component_overrun_is_still_detected():
    # Real observed usage 44 / 177 / 221 against a reserved output of 64.
    facts = capture_usage({'prompt_tokens': 44, 'completion_tokens': 177, 'total_tokens': 221})
    settled, lower = facts.accounting()
    assert facts.get('output').value == 177 and 177 > 64  # component overrun intact
    assert lower == 221 and settled == 221


def test_reasoning_is_a_breakdown_never_double_counted_and_validated():
    with_reasoning = capture_usage({'prompt_tokens': 10, 'completion_tokens': 100,
        'total_tokens': 110, 'completion_tokens_details': {'reasoning_tokens': 30}})
    reasoning = with_reasoning.get('reasoning')
    assert reasoning.present and reasoning.value == 30
    settled, lower = with_reasoning.accounting()
    assert (settled, lower) == (110, 110)  # not 140: never added to completion/total
    missing = capture_usage({'prompt_tokens': 10, 'completion_tokens': 100, 'total_tokens': 110})
    assert missing.get('reasoning').present is False and missing.get('reasoning').value is None
    assert missing.accounting()[0] == 110
    illegal = capture_usage({'prompt_tokens': 10, 'completion_tokens': 100, 'total_tokens': 110,
        'completion_tokens_details': {'reasoning_tokens': 130}})
    assert illegal.accounting() == (None, 110)  # out-of-range breakdown: not settled


# ------------------------------------------------------------- C: authority ---

def _paths():
    root = ROOT/uuid.uuid4().hex
    root.mkdir(parents=True)
    meter = root/'meter.bin'
    meter.write_bytes(b'approved-meter-asset')
    return root/'authority.json', meter, hashlib.sha256(meter.read_bytes()).hexdigest()


def _approved_settings(**over):
    path, meter, meter_sha = _paths()
    cfg = dict(schema='weknora-token-authority/v1', mode='approved', tenant_id=42,
        subject_id='owner-approved-'+uuid.uuid4().hex[:8], account_id='deepseek-account-main',
        provider='deepseek', expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        approval_source='OWNER:20261015-approved-token-meter',
        month=datetime.now(timezone.utc).date().replace(day=1).isoformat(),
        carry_in_tokens=0, carry_status='KNOWN', carry_source='LEDGER:port_token_calls:prior-month',
        limit_tokens=1_000_000, allowed_models=[['deepseek', 'deepseek-flash']],
        input_limit=200000, output_limit=8192,
        meter={'recipe': 'provider-completion-cap/v2', 'path': str(meter), 'sha256': meter_sha})
    cfg.update(over)
    path.write_text(json.dumps(cfg), encoding='utf-8')
    return load_authority_settings(path)


_UNSET = object()


def _approved_call(settings, contract, *, wire=b'{"a":1}', provider='deepseek',
                   model='deepseek-flash', wire_cap=_UNSET):
    sources = SourceFacts(OperationSlot('op/answer/1/cheap', 'chat'),
        (SourceSlice('question', 'SRC:q1', sha(wire), sha(wire), (settings.subject_id,),
            'server-resolved/v1', '/'),), sha(wire))
    facts = BoundCallFacts(sources, TokenReservationBound(len(wire),
        contract.output_upper if contract else 0, settings.meter.recipe),
        settings.config_sha, 'fp', False, contract)
    context = CallContext('op/answer/1/cheap', TrustedScope(settings.subject_id, ('kb-1',)))
    cap = (contract.cap_value if contract else None) if wire_cap is _UNSET else wire_cap
    return PreparedCall(context, provider, model, 'mid', 'chat',
        'https://api.deepseek.com/chat/completions', sha(wire), len(wire), cap, facts)


def test_approved_authority_verifies_the_same_contract_and_decouples_the_upper():
    settings = _approved_settings()
    authority = ApprovedTokenAuthority(settings)
    case_a = resolve_output_contract(_deepeek_config(max_tokens_field='max_tokens'), {'max_tokens': 64})
    assert authority.reserve_bound(_approved_call(settings, case_a)).output_upper == 64
    # A provider that bounds visible output and reasoning separately may reserve
    # an upper bound larger than the wire cap; that is no longer a hard failure.
    separated = OutputBudgetContract('deepseek', 'openai-completions', 'chat', 'max_tokens', 64,
        VISIBLE_PLUS_REASONING, 'reasoning_budget', 16, 'enabled', '', True, 80,
        'provider-completion-cap/v2', 'B')
    assert authority.reserve_bound(_approved_call(settings, separated)).output_upper == 80
    with pytest.raises(ProviderError, match='TOKEN_BOUND_UNSUPPORTED'):
        authority.reserve_bound(_approved_call(settings, None))
    # A contract whose cap disagrees with the actual wire cap is refused.
    with pytest.raises(ProviderError, match='TOKEN_BOUND_UNSUPPORTED'):
        authority.reserve_bound(_approved_call(settings, case_a, wire_cap=65))
    with pytest.raises(ProviderError, match='TOKEN_BOUND_UNSUPPORTED'):
        authority.reserve_bound(_approved_call(settings, replace(case_a, provider='siliconflow')))


def test_monthly_quota_identity_is_stable_across_a_recipe_version_change():
    first = _approved_settings()
    second = _approved_settings()  # different file bytes => different config_sha
    assert first.config_sha != second.config_sha
    assert first.approval_source == second.approval_source
    case_a = resolve_output_contract(_deepeek_config(max_tokens_field='max_tokens'), {'max_tokens': 8})
    a = ApprovedTokenAuthority(first).monthly(_approved_call(first, case_a))
    b = ApprovedTokenAuthority(second).monthly(_approved_call(second, case_a))
    assert a.account_id == b.account_id == 'deepseek-account-main'
    assert a.provider == b.provider == 'deepseek' and a.month == b.month
    assert a.approval_source == b.approval_source == 'OWNER:20261015-approved-token-meter'
    assert ':' + first.config_sha not in a.approval_source


def test_same_month_authority_legacy_prefix_compatibility():
    assert same_month_authority('OWNER:X', 'OWNER:X')
    assert same_month_authority('OWNER:X:' + 'a'*64, 'OWNER:X')      # legacy suffix
    assert same_month_authority('LEDGER:port_token_calls:<2026-10-01', 'LEDGER:port_token_calls:<2026-10-01')
    assert not same_month_authority('OWNER:Y:' + 'a'*64, 'OWNER:X')
    assert not same_month_authority('OWNER:X2:zz', 'OWNER:X')


def test_approved_loader_still_accepts_both_recipes():
    assert _approved_settings().meter.recipe == 'provider-completion-cap/v2'
    assert _approved_settings(meter={'recipe': 'wire-bytes-upper-bound/v1',
        'path': str(_paths()[1]), 'sha256': hashlib.sha256(b'approved-meter-asset').hexdigest()}
        ).meter.recipe == 'wire-bytes-upper-bound/v1'


def test_measure_final_input_unchanged_for_both_recipes():
    assert measure_final_input('wire-bytes-upper-bound/v1', b'1234567') == 7
    assert measure_final_input('provider-completion-cap/v2', b'1234567') == 7
    with pytest.raises(ProviderError, match='TOKEN_BOUND_RECIPE_UNSUPPORTED'):
        measure_final_input('nope/v0', b'x')


# ---------------------------------------------------------------- E: sources ---

def test_rerank_query_is_now_a_first_class_provenance_slice():
    from backend.weknora_port.source_facts import build_call_sources
    context = CallContext('op/rerank-0', TrustedScope('owner', ('kb-1',)))
    wire = b'{"query":1}'
    facts = build_call_sources(context, {'query': 'what protocol', 'documents': ['d1', 'd2']}, wire,
        capability='rerank')
    assert [s.kind for s in facts.slices] == ['question', 'evidence', 'evidence']
    assert facts.slices[0].wire_locator == '/query'
    assert facts.wire_sha256 == sha(wire)


def test_hosted_media_input_is_not_covered_by_the_wire_bytes_bound():
    config = _deepeek_config(max_tokens_field='max_tokens')
    context = CallContext('op/answer/1/cheap', TrustedScope('owner', ('kb-1',)))
    vision = {'model': 'deepseek-flash', 'max_tokens': 64, 'messages': [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'q'},
                                     {'type': 'image_url', 'image_url': {'url': 'https://x/y.png'}}]}]}
    wire = json.dumps(vision, sort_keys=True, separators=(',', ':')).encode()
    facts = _facts(config, vision, wire, capability='vision')
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind_wire_facts(config, replace(context, source_facts=facts.sources), vision, wire,
            'vision', config_sha='cfg', recipe='provider-completion-cap/v2')
    # An equivalent text-only wire still binds (no unproven constant is added).
    text = {'model': 'deepseek-flash', 'max_tokens': 64,
            'messages': [{'role': 'user', 'content': 'q'}]}
    text_wire = json.dumps(text, sort_keys=True, separators=(',', ':')).encode()
    text_facts = _facts(config, text, text_wire)
    assert bind_wire_facts(config, replace(context, source_facts=text_facts.sources), text, text_wire,
        'chat', config_sha='cfg', recipe='provider-completion-cap/v2').bound.output_upper == 64


def test_synthetic_binder_output_contract_stays_none():
    from backend.weknora_port.source_facts import bind_synthetic
    config = _deepeek_config()
    context = CallContext('op/answer/1/cheap', TrustedScope('synthetic-owner', ('kb-1',)))
    wire = b'{"max_completion_tokens":10}'
    facts = bind_synthetic(config, context, {'max_completion_tokens': 10}, wire, 'cfg', capability='chat')
    assert facts.output_contract is None and facts.bound.output_upper == 10
