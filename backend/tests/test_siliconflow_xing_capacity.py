"""REAL OFFLINE official tokenizer/template checks; no model API, DB or keys.

XING_TEST_ASSETS names the pre-downloaded pinned resource directory. Missing
assets/dependencies FAIL the suite; there is no fake counter or silent skip.
The dependency-version monkeypatch is explicitly a SIMULATED failure only.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from backend.app.adapters.models import siliconflow_xing_capacity as module
from backend.app.adapters.models.siliconflow_xing_capacity import (
    LOCAL_CONTENT_UTF8_CAP, LOCAL_INPUT_TOKEN_CAP, LOCAL_MESSAGE_CAP,
    LOCAL_OUTPUT_TOKEN_CAP, OFFICIAL_CONTEXT_WINDOW, RESOURCE_SHA256,
    SILICONFLOW_XING_MODEL, SiliconFlowXingCapacityCounter, XingCapacityUnavailable,
    serialize_xing_body,
)


@pytest.fixture(scope="module")
def assets():
    path = os.environ.get("XING_TEST_ASSETS")
    assert path, "XING_TEST_ASSETS is required; official offline resources were not supplied"
    return Path(path)


@pytest.fixture(scope="module")
def counter(assets):
    return SiliconFlowXingCapacityCounter.from_directory(assets)


def body(text="hello", *, messages=None, disabled=False, output=32):
    result = dict(model=SILICONFLOW_XING_MODEL,
                  messages=messages if messages is not None else [{"role": "user", "content": text}],
                  max_tokens=output, stream=False)
    if disabled:
        result["enable_thinking"] = False
    return result


def measure(counter, payload):
    return counter.measure("siliconflow", SILICONFLOW_XING_MODEL, payload, scope="PUBLIC_SYNTHETIC")


def reference(assets, rendered):
    """Independent known-render reference to audited upstream slow-tokenizer steps."""
    import sentencepiece
    processor = sentencepiece.SentencePieceProcessor(model_file=str(assets / "tokenizer.model"))
    config = json.loads((assets / "tokenizer_config.json").read_text(encoding="utf-8"))
    special = {row["content"]: int(token_id) for token_id, row in config["added_tokens_decoder"].items()}
    split = re.compile("(" + "|".join(map(re.escape, sorted(special, key=len, reverse=True))) + ")")
    ids = []
    bound = 0
    for segment in split.split(rendered):
        if segment in special:
            ids.append(special[segment])
            bound += 1
        elif segment:
            ids.extend(processor.encode(segment, out_type=int))
            bound += len(processor.normalize(segment).encode("utf-8"))
    return ids, bound


@pytest.mark.parametrize("disabled", [False, True])
def test_real_complete_template_golden_and_local_admission(counter, assets, disabled):
    estimate = measure(counter, body(disabled=disabled))
    expected_prefix = "<_bot></think>" if disabled else "<_bot><think>\n"
    rendered = "<_system><_user>hello" + expected_prefix
    ids, bound = reference(assets, rendered)
    selected = next(p for p in estimate.profiles if p.enable_thinking is not disabled)
    # Thinking prefix's trailing newline is TWO real SentencePiece tokens:
    # normalized dummy prefix (124361) plus newline (35). Never omit it.
    assert ids == ([6, 4, 45909, 5, 10] if disabled else [6, 4, 45909, 5, 9, 124361, 35])
    assert estimate.exact_prompt_tokens == selected.exact_prompt_tokens == len(ids) == (5 if disabled else 7)
    assert selected.conservative_prompt_bound == bound
    assert selected.rendered_sha256 == hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    assert selected.token_ids_sha256 == hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
    assert selected.generation_prefix == expected_prefix
    assert estimate.reservation_bound == max(p.conservative_prompt_bound for p in estimate.profiles)
    assert estimate.fits_local_policy and estimate.local_input_token_cap == LOCAL_INPUT_TOKEN_CAP
    assert estimate.context_window == OFFICIAL_CONTEXT_WINDOW == 262144
    assert estimate.hosted_revision == "UNKNOWN"
    assert estimate.hosted_input_token_bound is estimate.hosted_total_token_bound is None
    assert estimate.production_ready is False
    assert estimate.bound_status == "LOCAL_PROVEN_HOSTED_CONDITIONAL"
    with pytest.raises(TypeError):
        replace(estimate, production_ready=True)
    with pytest.raises(FrozenInstanceError):
        estimate.production_ready = True


def test_real_multiturn_includes_system_history_and_generation_prefix(counter, assets):
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "first"},
                {"role": "assistant", "content": "  answer  "}, {"role": "user", "content": "second"}]
    estimate = measure(counter, body(messages=messages, disabled=True))
    rendered = "<_system>policy<_user>first<_bot></think>answer<_end>\n<_user>second<_bot></think>"
    ids, bound = reference(assets, rendered)
    profile = next(p for p in estimate.profiles if not p.enable_thinking)
    assert profile.exact_prompt_tokens == len(ids)
    assert profile.conservative_prompt_bound == bound
    assert profile.rendered_sha256 == hashlib.sha256(rendered.encode()).hexdigest()
    assert estimate.exact_prompt_tokens > measure(counter, body("second", disabled=True)).exact_prompt_tokens


@pytest.mark.parametrize("text", ["中文知识库", "😀👩‍💻", "é e\u0301", "ＡＢＣ①㎏", "العربية",
                                   "हिन्दी", "\t \r\n", "a\x00b", "𠀋", 'JSON "quote" \\ slash'])
def test_real_unicode_exact_count_and_normalization_bound(counter, assets, text):
    estimate = measure(counter, body(text, disabled=True))
    ids, bound = reference(assets, "<_system><_user>" + text + "<_bot></think>")
    profile = next(p for p in estimate.profiles if not p.enable_thinking)
    assert estimate.exact_prompt_tokens == len(ids)
    assert len(ids) <= bound == profile.conservative_prompt_bound <= estimate.reservation_bound
    assert json.loads(estimate.wire_bytes)["messages"][0]["content"] == text


def test_real_seeded_unicode_bound_does_not_use_character_ratio(counter):
    rng = random.Random(20261009)
    alphabet = " abc\n\t中文é\u0301😀𠀋Ａ①㎏العربية"
    for _ in range(48):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 100)))
        estimate = measure(counter, body(text))
        for profile in estimate.profiles:
            assert 0 < profile.exact_prompt_tokens <= profile.conservative_prompt_bound


@pytest.mark.parametrize("literal", ["<_start>", "<_end>", "<_pad>", "<_user>", "<_bot>", "<_system>",
                                      "<think>", "</think>", "<tool_call>", "</tool_call>",
                                      "<tool_response>", "</tool_response>"])
def test_registered_special_token_literals_rejected_without_rewriting(counter, literal):
    payload = body("ordinary " + literal + " text")
    original = serialize_xing_body(payload)
    with pytest.raises(XingCapacityUnavailable, match="^XING_SPECIAL_TOKEN_LITERAL_UNSUPPORTED$"):
        measure(counter, payload)
    assert serialize_xing_body(payload) == original


@pytest.mark.parametrize("field", ["temperature", "thinking", "thinking_budget", "chat_template_kwargs",
                                    "tools", "response_format", "extra_body", "future_field"])
def test_unknown_body_fields_rejected(counter, field):
    payload = body()
    payload[field] = None
    with pytest.raises(XingCapacityUnavailable, match="^XING_REQUEST_ENVELOPE_UNSUPPORTED$"):
        measure(counter, payload)


@pytest.mark.parametrize("provider,model", [
    ("siliconflow", "Pro/XingChenAGI/Xing4.0-29B"),
    ("siliconflow", "XingChen-AGI/Xing4.0-29B-A4B"),
    ("siliconflow", "unknown"), ("deepseek", SILICONFLOW_XING_MODEL),
])
def test_unknown_models_and_provider_rejected(counter, provider, model):
    payload = body()
    payload["model"] = model
    with pytest.raises(XingCapacityUnavailable, match="^XING_MODEL_COUNTER_UNAVAILABLE$"):
        counter.measure(provider, model, payload, scope="PUBLIC_SYNTHETIC")


@pytest.mark.parametrize("output", [0, -1, 513, 1280, 262144, True, 1.0, None])
def test_output_cap_rejected(counter, output):
    with pytest.raises(XingCapacityUnavailable, match="^XING_OUTPUT_LOCAL_CAP_EXCEEDED$"):
        measure(counter, body(output=output))


def test_real_input_and_content_caps_reject_without_truncation(counter):
    payload = body("a" * LOCAL_INPUT_TOKEN_CAP)
    original = serialize_xing_body(payload)
    with pytest.raises(XingCapacityUnavailable, match="^XING_INPUT_LOCAL_CAP_EXCEEDED$"):
        measure(counter, payload)
    assert serialize_xing_body(payload) == original
    with pytest.raises(XingCapacityUnavailable, match="^XING_CONTENT_LOCAL_CAP_EXCEEDED$"):
        measure(counter, body("a" * (LOCAL_CONTENT_UTF8_CAP + 1)))
    assert measure(counter, body("a" * (LOCAL_INPUT_TOKEN_CAP - 32))).fits_local_policy
    assert measure(counter, body(output=LOCAL_OUTPUT_TOKEN_CAP)).output_reservation == 512


@pytest.mark.parametrize("messages", [[], (), [{"role": "tool", "content": "x"}],
    [{"role": "user", "content": [dict(type="text", text="x")]}],
    [{"role": "user", "content": "x", "reasoning_content": "hidden"}],
    [{"role": "user", "content": "x", "name": "user"}],
    [{"role": "user", "content": "x"}] * (LOCAL_MESSAGE_CAP + 1)])
def test_message_contract_rejects_unknown_multimodal_and_tools(counter, messages):
    with pytest.raises(XingCapacityUnavailable, match="^XING_MESSAGE(S)?_UNSUPPORTED$"):
        measure(counter, body(messages=messages))


@pytest.mark.parametrize("field,value", [("stream", True), ("stream", 0), ("enable_thinking", True),
                                          ("enable_thinking", 0), ("enable_thinking", None)])
def test_stream_and_thinking_types_fail_closed(counter, field, value):
    payload = body()
    payload[field] = value
    with pytest.raises(XingCapacityUnavailable, match="^XING_(REQUEST_ENVELOPE|THINKING_FIELD)_UNSUPPORTED$"):
        measure(counter, payload)


def test_invalid_surrogate_rejected_before_render(counter):
    with pytest.raises(XingCapacityUnavailable, match="^XING_TEXT_UNICODE_INVALID$"):
        measure(counter, body("\ud800"))


def test_final_transport_wire_hash_and_mutation_detection(counter):
    payload = body("中文 😀")
    estimate = measure(counter, payload)
    current_cloud_wire = json.dumps(payload).encode()
    assert estimate.wire_bytes == current_cloud_wire
    assert estimate.wire_request_sha256 == hashlib.sha256(current_cloud_wire).hexdigest()
    estimate.assert_wire_bytes(current_cloud_wire)
    assert "messages" not in repr(estimate) and "PUBLIC_SYNTHETIC" not in repr(estimate)
    for changed_wire in [json.dumps(payload, ensure_ascii=False).encode(),
                         json.dumps(payload, separators=(",", ":")).encode(), current_cloud_wire + b" "]:
        with pytest.raises(XingCapacityUnavailable, match="^XING_FINAL_WIRE_MISMATCH$"):
            estimate.assert_wire_bytes(changed_wire)
        with pytest.raises(XingCapacityUnavailable, match="^XING_WIRE_SERIALIZATION_MISMATCH$"):
            counter.measure("siliconflow", SILICONFLOW_XING_MODEL, payload,
                            scope="PUBLIC_SYNTHETIC", wire_bytes=changed_wire)
    payload["messages"][0]["content"] += "changed"
    with pytest.raises(XingCapacityUnavailable, match="^XING_FINAL_WIRE_MISMATCH$"):
        estimate.assert_wire_bytes(serialize_xing_body(payload))


def test_scope_bound_in_measurement_but_not_sent(counter):
    payload = body()
    first = counter.measure("siliconflow", SILICONFLOW_XING_MODEL, payload, scope="KB_A")
    second = counter.measure("siliconflow", SILICONFLOW_XING_MODEL, payload, scope="KB_B")
    assert first.wire_bytes == second.wire_bytes
    assert first.scope_sha256 != second.scope_sha256
    assert first.measurement_sha256 != second.measurement_sha256
    for scope in ["", "  ", None, "\udfff"]:
        with pytest.raises(XingCapacityUnavailable, match="^XING_SCOPE_INVALID$"):
            counter.measure("siliconflow", SILICONFLOW_XING_MODEL, payload, scope=scope)


def test_injectable_modelwindow_counter_is_local_precount_only(counter):
    from backend.app.ports.context_budget import ModelWindow
    window = ModelWindow("siliconflow", SILICONFLOW_XING_MODEL,
                         window_tokens=LOCAL_INPUT_TOKEN_CAP, capacity_source="LOCAL_POLICY_CONDITIONAL",
                         tokenizer_source=counter.identity, counter=counter)
    messages = body()["messages"]
    assert window.count(messages) == measure(counter, body()).reservation_bound
    assert window.check(messages, 32)["input_tokens"] == window.count(messages)
    with pytest.raises(XingCapacityUnavailable, match="^XING_WINDOW_ENVELOPE_UNSUPPORTED$"):
        window.count(messages, tools=[{"type": "function"}])


@pytest.mark.parametrize("name", list(RESOURCE_SHA256))
def test_tampered_official_resource_rejected_before_loading(assets, tmp_path, name):
    for resource in RESOURCE_SHA256:
        (tmp_path / resource).write_bytes((assets / resource).read_bytes())
    with (tmp_path / name).open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(XingCapacityUnavailable, match="^XING_RESOURCE_HASH_MISMATCH$"):
        SiliconFlowXingCapacityCounter(tmp_path)


def test_missing_resources_rejected(tmp_path):
    with pytest.raises(XingCapacityUnavailable, match="^XING_RESOURCE_UNAVAILABLE$"):
        SiliconFlowXingCapacityCounter(tmp_path)


def test_simulated_dependency_version_mismatch_rejected(assets, monkeypatch):
    """SIMULATED bad version metadata; not a tokenizer/model inference result."""
    monkeypatch.setattr(module, "version", lambda distribution: "UNVERIFIED")
    with pytest.raises(XingCapacityUnavailable, match="^XING_RUNTIME_VERSION_UNVERIFIED$"):
        SiliconFlowXingCapacityCounter(assets)
