"""P27 bounded inline-image meter: offline only, no real-account facts or sends."""
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path

from PIL import Image
import pytest

from backend.weknora_port.models import ChatOptions, Message, ProviderError, build_chat_body, provider_config
from backend.weknora_port.authority import ApprovedTokenAuthority, load_authority_settings
from backend.weknora_port.models.types import CallContext, PreparedCall, TrustedScope
from backend.weknora_port.source_facts import (
    DEEPSEEK_INLINE_IMAGE_RECIPE, bind_wire_facts, build_call_sources, measure_final_input)


def image_url():
    raw = io.BytesIO()
    Image.new('RGB', (12, 10), 'white').save(raw, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(raw.getvalue()).decode('ascii')


def request():
    config = provider_config('deepseek', 'deepseek-flash')
    body = build_chat_body(config, (Message('user', 'Describe only what the image supports.',
                                           images=(image_url(),)),),
                           ChatOptions(max_tokens=5000, thinking=True))
    return config, body


def bind(config, body, *, capability='vision', recipe=DEEPSEEK_INLINE_IMAGE_RECIPE, wire=None,
         config_sha='offline-test-config'):
    wire = wire or json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    context = CallContext('test-p27/version/image/0/caption',
                          TrustedScope('test-p27-owner', ('test-p27-kb',), ('test-p27-doc',)))
    sources = build_call_sources(context, body, wire, capability=capability)
    return bind_wire_facts(config, replace(context, source_facts=sources), body, wire, capability,
                           config_sha=config_sha, recipe=recipe), wire


def test_inline_image_bound_keeps_wire_bytes_plus_documented_image_upper_and_full_output():
    config, body = request()
    original = deepcopy(body)
    facts, wire = bind(config, body)
    assert facts.bound.input_upper == len(wire) + 1024
    assert facts.bound.output_upper == 5000
    assert facts.output_contract.cap_field == 'max_tokens'
    assert facts.output_contract.reasoning_included
    assert facts.output_contract.output_upper == facts.output_contract.cap_value == 5000
    assert facts.sources.slices[0].origin_kbs == ('test-p27-kb',)
    assert facts.simulated is False  # Non-synthetic binding contract on synthetic test data.
    assert body == original  # Meter must not change the business request or generation cap.


def test_each_image_is_counted_and_old_text_recipe_still_refuses_images():
    config, body = request()
    body['messages'][0]['content'].append(deepcopy(body['messages'][0]['content'][0]))
    facts, wire = bind(config, body)
    assert facts.bound.input_upper == len(wire) + 2048
    for recipe in ('wire-bytes-upper-bound/v1', 'provider-completion-cap/v2'):
        with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
            bind(config, body, recipe=recipe)
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        measure_final_input(DEEPSEEK_INLINE_IMAGE_RECIPE, wire)


@pytest.mark.parametrize('change', [
    {'provider': 'siliconflow'}, {'model': 'deepseek-v4-pro'},
    {'base_url': 'https://relay.invalid/v1'}, {'api': 'openai-responses'},
    {'supports_vision': False}, {'base_url': 'https://user@api.deepseek.com/v1'},
])
def test_recipe_cannot_be_reused_for_another_model_deployment_or_protocol(change):
    config, body = request()
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(replace(config, **change), body)


@pytest.mark.parametrize('url', [
    'https://example.invalid/x.png', 'file:///E:/private.png',
    'data:image/png;base64,!!!!', 'data:image/png;base64,QUJD',
    'data:image/gif;base64,QUJD', 'data:image/jpeg;base64,',
])
def test_remote_files_unsupported_media_and_invalid_payload_refuse(url):
    config, body = request()
    body['messages'][0]['content'][0]['image_url']['url'] = url
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body)


def test_no_unproven_text_only_or_system_image_or_wrong_wire_admission():
    config, body = request()
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body, capability='chat')
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body, wire=b'{"model":"deepseek-flash","messages":[]}')
    body['messages'][0]['role'] = 'system'
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body)
    body['messages'][0]['role'] = 'user'
    body['messages'][0]['content'] = [{'type': 'text', 'text': 'q'}]
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body)


def test_wrong_completion_cap_field_cannot_under_reserve_reasoning_or_visible_output():
    config, body = request()
    body['max_completion_tokens'] = body.pop('max_tokens')
    with pytest.raises(ProviderError, match='MODEL_OUTPUT_HARD_LIMIT_DENIED'):
        bind(config, body)


def test_file_content_does_not_escape_inline_image_validation():
    config, body = request()
    body['messages'][0]['content'].append({'type': 'file', 'file_id': 'unknown'})
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body)


@pytest.mark.parametrize('variant', ['oversized_dimension', 'animated_png', 'mime_mismatch'])
def test_actual_image_content_must_match_supported_static_media(variant):
    config, body = request()
    raw = io.BytesIO()
    image = Image.new('RGB', (8193, 1) if variant == 'oversized_dimension' else (12, 10), 'white')
    if variant == 'animated_png':
        image.save(raw, format='PNG', save_all=True, append_images=[Image.new('RGB', (12, 10), 'black')])
    else:
        image.save(raw, format='PNG')
    mime = 'jpeg' if variant == 'mime_mismatch' else 'png'
    body['messages'][0]['content'][0]['image_url']['url'] = (
        f'data:image/{mime};base64,' + base64.b64encode(raw.getvalue()).decode('ascii'))
    with pytest.raises(ProviderError, match='MODEL_INPUT_BOUND_NOT_APPLICABLE'):
        bind(config, body)


def test_approved_loader_and_authority_accept_new_recipe_only_with_full_bound_contract(tmp_path):
    # All identities and carry facts here are explicitly offline fixtures. This
    # never contacts a provider or creates a real account/ledger/grant.
    from backend.weknora_port import source_facts
    meter_path = Path(source_facts.__file__)
    cfg = dict(schema='weknora-token-authority/v1', mode='approved', tenant_id=17,
        subject_id='test-p27-owner', account_id='offline-p27-fixture', provider='deepseek',
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        approval_source='OFFLINE:contract-test-not-real-authorization',
        month=datetime.now(timezone.utc).date().replace(day=1).isoformat(),
        carry_in_tokens=0, carry_status='KNOWN', carry_source='LEDGER:OFFLINE:fixture-only',
        allowed_models=[['deepseek', 'deepseek-flash']], limit_tokens=1_000_000,
        input_limit=100_000, output_limit=5000,
        meter={'recipe': DEEPSEEK_INLINE_IMAGE_RECIPE, 'path': str(meter_path),
               'sha256': hashlib.sha256(meter_path.read_bytes()).hexdigest()})
    path = tmp_path/'authority.json'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    settings = load_authority_settings(path)
    config, body = request()
    facts, wire = bind(config, body, config_sha=settings.config_sha)
    context = CallContext('test-p27/version/image/0/caption',
                          TrustedScope('test-p27-owner', ('test-p27-kb',), ('test-p27-doc',)))
    call = PreparedCall(context, config.provider, config.model, config.model_id, 'vision',
        config.base_url+'/chat/completions', facts.sources.wire_sha256, len(wire), 5000, facts)
    authority = ApprovedTokenAuthority(settings)
    assert authority.reserve_bound(call) == facts.bound
    assert authority.monthly(call).account_id == 'offline-p27-fixture'
    with pytest.raises(ProviderError, match='TOKEN_BOUND_UNSUPPORTED'):
        authority.reserve_bound(replace(call, output_budget=64))
