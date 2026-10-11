"""Offline tests for the V6 text-only capacity handoff contract."""
import hashlib
import json

import pytest

from backend.app.adapters.models.v6_text_capacity import (
    DEEPSEEK_CONTEXT_WINDOW,
    DEEPSEEK_MODEL,
    DEEPSEEK_RENDERER_IDENTITY,
    SILICONFLOW_XING_MODEL,
    CapacityCounterUnavailable,
    DeepseekV41RecipeRenderer,
    DeepseekV41TextCapacityCounter,
)
from backend.app.application.validation_usd_budget import (
    OWNER_V6_TOKEN_GRANT,
    TokenRequestBudget,
    V6_CAPACITY_SOURCE,
    V6_REQUEST_PROTOCOL,
    V6_SCOPE,
    V6_SPECIAL_TOKEN_POLICY,
    V6_TOKENIZER_SOURCE,
)


class FakePinnedRenderer:
    identity = DEEPSEEK_RENDERER_IDENTITY

    def __init__(self):
        self.requests = []
        self.rendered = []

    def __call__(self, body):
        self.requests.append(body)
        prompt = body["messages"][0]["content"]
        rendered = f"<\uff5cbegin\u2581of\u2581sentence\uff5c><\uff5cUser\uff5c>{prompt}<\uff5cAssistant\uff5c>"
        self.rendered.append(rendered)
        return rendered


def request(messages=None, **overrides):
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": (
            "SYSTEM POLICY: use evidence only.\n"
            "CONTEXT: {\\\"scope\\\":\\\"current\\\"}\n"
            "Question: compare caf\u00e9 and emoji \U0001f9ea"
        )}] if messages is None else messages,
        "thinking": {"type": "disabled"},
        "max_tokens": 512,
        "temperature": 0,
        "stream": False,
    }
    body.update(overrides)
    return body


def estimate(body=None, renderer=None, *, scope=V6_SCOPE):
    return DeepseekV41TextCapacityCounter(renderer or FakePinnedRenderer()).measure(
        "deepseek", DEEPSEEK_MODEL, request() if body is None else body, scope=scope)


def test_full_rendered_unicode_uses_byte_bound_and_exact_cloud_wire_hash():
    renderer = FakePinnedRenderer()
    body = request()
    result = estimate(body, renderer)
    rendered = renderer.rendered[0].encode("utf-8")
    wire = json.dumps(body).encode("utf-8")

    assert renderer.requests == [body]
    assert result.exact_prompt_tokens is None
    assert result.reservation_bound == result.rendered_utf8_bytes == len(rendered)
    assert result.output_reservation == 512
    assert result.context_window == DEEPSEEK_CONTEXT_WINDOW == 1_048_576
    assert result.bound_status == "CONDITIONAL_RENDERER_EQUIVALENCE_AND_BYTELEVEL_BPE"
    assert result.production_ready is False
    assert result.scope == V6_SCOPE
    assert result.scope_sha256 == hashlib.sha256(V6_SCOPE.encode()).hexdigest()
    assert result.wire_request_sha256 == hashlib.sha256(wire).hexdigest()
    assert result.rendered_sha256 and result.measurement_sha256
    assert "\u00e9" in result.prompt_text and "\U0001f9ea" in result.prompt_text


def test_measurement_identity_binds_scope_and_full_wire_body():
    base = request()
    first = estimate(base)
    other_scope = estimate(base, scope="another-scope")
    changed = request(messages=[{"role": "user", "content": "different prompt"}])
    changed_body = estimate(changed)

    assert first.measurement_sha256 != other_scope.measurement_sha256
    assert first.measurement_sha256 != changed_body.measurement_sha256
    assert first.wire_request_sha256 != changed_body.wire_request_sha256


def test_converts_to_existing_token_request_budget_contract():
    result = estimate()
    budget = result.to_token_request_budget(safety_tokens=17)

    assert type(budget) is TokenRequestBudget
    assert budget.activity_id == OWNER_V6_TOKEN_GRANT.activity_id
    assert budget.scope == V6_SCOPE
    assert budget.provider == "deepseek" and budget.model == DEEPSEEK_MODEL
    assert budget.input_token_bound == result.reservation_bound
    assert budget.output_token_bound == 512 and budget.safety_tokens == 17
    assert budget.window_tokens == DEEPSEEK_CONTEXT_WINDOW
    assert budget.capacity_source == V6_CAPACITY_SOURCE
    assert budget.tokenizer_source == V6_TOKENIZER_SOURCE
    assert budget.prompt_text == request()["messages"][0]["content"]
    assert budget.special_token_literals == ("<\uff5cUser\uff5c>", "<think>", "\uff5cDSML\uff5c")
    assert budget.protocol_id == V6_REQUEST_PROTOCOL
    assert V6_SPECIAL_TOKEN_POLICY == "deny-tokenizer-added-token-literals-v1"
    assert budget.wire_request_sha256 == result.wire_request_sha256


@pytest.mark.parametrize("overrides,code", [
    ({"tools": []}, "REQUEST_ENVELOPE_UNSUPPORTED"),
    ({"temperature": 1}, "TEMPERATURE_UNSUPPORTED"),
    ({"thinking": {"type": "enabled"}}, "THINKING_MODE_UNSUPPORTED"),
    ({"stream": True}, "REQUEST_ENVELOPE_UNSUPPORTED"),
    ({"max_tokens": 513}, "OUTPUT_RESERVATION_UNSUPPORTED"),
])
def test_unsupported_request_features_fail_closed(overrides, code):
    with pytest.raises(CapacityCounterUnavailable, match=code):
        estimate(request(**overrides))


@pytest.mark.parametrize("messages,code", [
    ([{"role": "user", "content": [{"type": "text", "text": "image"}]}], "SINGLE_USER_TEXT_REQUIRED"),
    ([{"role": "system", "content": "policy"}, {"role": "user", "content": "now"}], "SINGLE_USER_TEXT_REQUIRED"),
    ([{"role": "assistant", "content": "old turn"}], "SINGLE_USER_TEXT_REQUIRED"),
    ([{"role": "user", "content": "now"}, {"role": "user", "content": "again"}], "SINGLE_USER_TEXT_REQUIRED"),
])
def test_only_single_user_text_message_is_admitted(messages, code):
    with pytest.raises(CapacityCounterUnavailable, match=code):
        estimate(request(messages=messages))


@pytest.mark.parametrize("literal", ["<\uff5cUser\uff5c>", "<think>", "\uff5cDSML\uff5c"])
def test_special_token_literals_fail_closed(literal):
    with pytest.raises(CapacityCounterUnavailable, match="SPECIAL_TOKEN_LITERAL_UNSUPPORTED"):
        estimate(request(messages=[{"role": "user", "content": f"unsafe {literal}"}]))


def test_siliconflow_hosted_xing_fails_closed_without_template_evidence():
    counter = DeepseekV41TextCapacityCounter(FakePinnedRenderer())
    with pytest.raises(CapacityCounterUnavailable, match="SILICONFLOW_HOSTED_TEMPLATE_UNVERIFIED"):
        counter.measure("siliconflow", SILICONFLOW_XING_MODEL, request(), scope=V6_SCOPE)


def test_missing_or_untrusted_renderer_never_falls_back_to_raw_message_length(monkeypatch):
    def missing_package(_name):
        raise __import__("importlib.metadata", fromlist=["PackageNotFoundError"]).PackageNotFoundError

    monkeypatch.setattr("backend.app.adapters.models.v6_text_capacity.version", missing_package)
    with pytest.raises(CapacityCounterUnavailable, match="DEEPSEEK_RECIPE_NOT_INSTALLED"):
        DeepseekV41RecipeRenderer()(request())

    class BadRenderer:
        identity = DEEPSEEK_RENDERER_IDENTITY

        def __call__(self, _body):
            return None

    with pytest.raises(CapacityCounterUnavailable, match="DEEPSEEK_RENDERED_PROMPT_INVALID"):
        estimate(renderer=BadRenderer())


def test_invalid_scope_unicode_and_budget_safety_fail_closed():
    with pytest.raises(CapacityCounterUnavailable, match="SCOPE_IDENTITY_INVALID"):
        estimate(scope=" ")
    with pytest.raises(CapacityCounterUnavailable, match="MESSAGE_UNICODE_INVALID"):
        estimate(request(messages=[{"role": "user", "content": "bad \ud800"}]))
    result = estimate()
    with pytest.raises(CapacityCounterUnavailable, match="TOKEN_REQUEST_BUDGET_BINDING_INVALID"):
        result.to_token_request_budget(safety_tokens=-1)
    with pytest.raises(CapacityCounterUnavailable, match="TOKEN_REQUEST_BUDGET_BINDING_INVALID"):
        result.to_token_request_budget(grant=OWNER_V6_TOKEN_GRANT.simulated())
