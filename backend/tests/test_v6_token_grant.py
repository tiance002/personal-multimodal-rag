"""Offline-only tests for the versioned owner token grant (SIMULATED ledger)."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.validation_usd_budget import (
    POLICY,
    V6_CAPACITY_SOURCE,
    V6_CONTEXT_WINDOW_TOKENS,
    V6_REQUEST_PROTOCOL,
    V6_TOKENIZER_SOURCE,
    OwnerActivityTokenGrant,
    TokenRequestBudget,
    ValidationAttemptGate,
    V6OwnerActivityGate,
)
from backend.app.ports.session_attempts import AttemptDenied


def _history_row(index, input_tokens, output_tokens, reserved_input, reserved_output):
    return {
        "attempt_id": f"legacy-{index}",
        "request_id": f"legacy-request-{index}",
        "status": "ok",
        "validation_tokens": {
            "state": "settled",
            "input": input_tokens,
            "output": output_tokens,
            "reserved_input": reserved_input,
            "reserved_output": reserved_output,
        },
        "validation_cost": {"currency": "USD", "scale": 1_000_000,
                            "state": "usage_observed_charge_unverified",
                            "actual_charge_verified": False},
    }


def _ledger(tmp_path):
    path = tmp_path / "deepseek-call-ledger.json"
    rows = []
    for index in range(53):
        rows.append(_history_row(
            index,
            1168 if index < 9 else 1167,       # 61,860 observed input tokens
            70 if index < 52 else 69,          # 3,709 observed output tokens
            5427 if index < 25 else 5426,      # historical upper bound only
            682 if index < 3 else 681,
        ))
    path.write_text(json.dumps({
        "schema_version": 1,
        "provider": "deepseek",
        "calls_allowed_in_this_task": False,
        "authorized_limit": 53,
        "consumed_attempts": 53,
        "reserved_attempts": 0,
        "attempts": rows,
        "validation_policy": dict(POLICY),
    }), encoding="utf-8")
    return path


def _gate(tmp_path, *, token_limit=1_000_000, baseline=65_569, now=None):
    path = _ledger(tmp_path)
    grant = OwnerActivityTokenGrant.simulated(
        token_limit=token_limit, baseline_used_tokens=baseline,
    )
    clock = (lambda: now) if now is not None else (lambda: datetime(2026, 10, 9, tzinfo=timezone.utc))
    gate = V6OwnerActivityGate(path, grant=grant, clock=clock)
    gate.initialize_grant()
    return gate, path


def _budget(gate, *, input_bound=100, output_bound=512, safety=8,
            capacity_source=V6_CAPACITY_SOURCE, tokenizer_source=V6_TOKENIZER_SOURCE,
            prompt="public evaluation question", special_tokens=("<｜User｜>", "<think>", "｜DSML｜"),
            window=V6_CONTEXT_WINDOW_TOKENS, scope=None, model=None):
    return TokenRequestBudget(
        activity_id=gate.grant.activity_id,
        scope=gate.grant.scope if scope is None else scope,
        provider=gate.grant.provider,
        model=gate.grant.model if model is None else model,
        input_token_bound=input_bound,
        output_token_bound=output_bound,
        safety_tokens=safety,
        window_tokens=window,
        capacity_source=capacity_source,
        tokenizer_source=tokenizer_source,
        prompt_text=prompt,
        special_token_literals=special_tokens,
        wire_request_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        protocol_id=V6_REQUEST_PROTOCOL,
    )


def test_v6_grant_is_additive_and_preserves_legacy_authorization(tmp_path):
    gate, path = _gate(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["authorized_limit"] == 53
    assert data["consumed_attempts"] == 53
    assert data["reserved_attempts"] == 0
    assert data["calls_allowed_in_this_task"] is False
    assert gate._used(data) == 65_569
    assert data["v6_owner_activity"]["grant"]["token_limit"] == 1_000_000

    with pytest.raises(AttemptDenied, match="SESSION_CALLS_DISABLED"):
        ValidationAttemptGate(path).reserve(request_id="old-policy-stays-disabled")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["consumed_attempts"] == 53
    assert after["authorized_limit"] == 53


def test_v6_gate_is_closed_typed_and_real_grant_cannot_use_temp_ledger(tmp_path):
    assert V6OwnerActivityGate in DEEPSEEK_GATE_TYPES.supported
    with pytest.raises(AttemptDenied, match="V6_REAL_GRANT_PATH_OR_IDENTITY_INVALID"):
        V6OwnerActivityGate(tmp_path / "ledger.json")

    from backend.app.application.session_attempts import CANONICAL_LEDGER
    grant = OwnerActivityTokenGrant.simulated()
    with pytest.raises(AttemptDenied, match="V6_SIMULATED_GRANT_CANNOT_USE_CANONICAL_LEDGER"):
        V6OwnerActivityGate(CANONICAL_LEDGER, grant=grant)


def test_simulated_gate_cannot_cross_the_before_send_boundary(tmp_path):
    gate, path = _gate(tmp_path)
    attempt = gate.reserve(request_id="simulated-never-sends", budget=_budget(gate))
    with pytest.raises(AttemptDenied, match="V6_SIMULATED_SEND_DISABLED"):
        gate.validate_before_send(attempt)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["v6_owner_activity"]["attempts"][0]["status"] == "reserved"


def test_v6_atomic_reserve_settle_and_usage_replaces_reserve(tmp_path):
    gate, path = _gate(tmp_path)
    attempt = gate.reserve(request_id="v6-eval-001", budget=_budget(gate))
    data = json.loads(path.read_text(encoding="utf-8"))
    row = data["v6_owner_activity"]["attempts"][0]
    assert row["validation_tokens"]["reserved_input"] == 108
    assert row["validation_tokens"]["reserved_output"] == 512
    assert "prompt_text" not in row["request_budget"]
    assert row["request_budget"]["prompt_sha256"]
    gate.finish_with_usage(attempt, "ok", {
        "prompt_tokens": 95, "completion_tokens": 21, "total_tokens": 116,
    })
    data = json.loads(path.read_text(encoding="utf-8"))
    assert gate._used(data) == 65_569 + 116
    assert data["consumed_attempts"] == 53
    assert data["reserved_attempts"] == 0


def test_v6_duplicate_request_id_is_denied_across_legacy_history(tmp_path):
    gate, _ = _gate(tmp_path)
    with pytest.raises(AttemptDenied, match="SESSION_REQUEST_ALREADY_RESERVED"):
        gate.reserve(request_id="legacy-request-0", budget=_budget(gate))


def test_v6_has_no_new_call_count_cap_and_dedupes_settled_requests(tmp_path):
    gate, path = _gate(tmp_path)
    budget = _budget(gate, input_bound=1, output_bound=512, safety=0)
    attempts = [gate.reserve(request_id=f"v6-many-{index}", budget=budget) for index in range(54)]
    assert len(attempts) == 54
    with pytest.raises(AttemptDenied, match="SESSION_REQUEST_ALREADY_RESERVED"):
        gate.reserve(request_id="v6-many-0", budget=budget)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["consumed_attempts"] == data["authorized_limit"] == 53
    assert len(data["v6_owner_activity"]["attempts"]) == 54


def test_v6_unknown_keeps_full_bound_and_not_sent_releases_only_its_reservation(tmp_path):
    gate, path = _gate(tmp_path)
    unknown = gate.reserve(request_id="v6-unknown", budget=_budget(gate))
    gate.finish(unknown, "unknown")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert gate._used(data) == 65_569 + 620

    not_sent = gate.reserve(request_id="v6-not-sent", budget=_budget(gate))
    gate.finish(not_sent, "not_sent")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert gate._used(data) == 65_569 + 620
    assert data["v6_owner_activity"]["attempts"][1]["validation_tokens"]["state"] == "released"


def test_v6_token_cap_is_atomic_under_concurrency_without_call_count_cap(tmp_path):
    gate, path = _gate(tmp_path, token_limit=1_000_000)
    budget = _budget(gate, input_bound=300_000, output_bound=512, safety=0)

    def reserve(index):
        try:
            gate.reserve(request_id=f"v6-parallel-{index}", budget=budget)
            return True
        except AttemptDenied:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        successes = list(pool.map(reserve, range(12)))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert sum(successes) == 3
    assert gate._used(data) == 65_569 + 3 * 300_512
    assert data["consumed_attempts"] == 53
    assert data["v6_owner_activity"]["grant"]["token_limit"] == 1_000_000


def test_v6_unverified_or_overrun_usage_retains_bound_and_blocks_later_reserve(tmp_path):
    gate, path = _gate(tmp_path)
    attempt = gate.reserve(request_id="v6-bad-usage", budget=_budget(gate))
    with pytest.raises(AttemptDenied, match="V6_USAGE_UNVERIFIED"):
        gate.finish_with_usage(attempt, "ok", {})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert gate._used(data) == 65_569 + 620

    attempt = gate.reserve(request_id="v6-overrun", budget=_budget(gate))
    with pytest.raises(AttemptDenied, match="V6_USAGE_UNVERIFIED"):
        gate.finish_with_usage(attempt, "ok", {
            "prompt_tokens": 109, "completion_tokens": 1, "total_tokens": 110,
        })
    with pytest.raises(AttemptDenied, match="V6_ACTIVITY_BLOCKED"):
        gate.reserve(request_id="v6-after-overrun", budget=_budget(gate))


@pytest.mark.parametrize("kwargs", [
    {"capacity_source": "UNKNOWN"},
    {"tokenizer_source": "UNKNOWN"},
    {"window": 1_000_000},
    {"output_bound": 896},
    {"prompt": "quoted marker <｜User｜> in public text"},
    {"prompt": "quoted marker <think> in public text"},
    {"prompt": "quoted marker ｜DSML｜ in public text"},
    {"special_tokens": ("not-a-token",)},
])
def test_v6_requires_pinned_capacity_tokenizer_and_special_token_safe_prompt(tmp_path, kwargs):
    gate, _ = _gate(tmp_path)
    with pytest.raises(AttemptDenied, match="V6_REQUEST_BUDGET_INVALID"):
        gate.reserve(request_id="v6-invalid-budget", budget=_budget(gate, **kwargs))


def test_v6_wrong_scope_model_and_context_overflow_are_rejected(tmp_path):
    gate, _ = _gate(tmp_path)
    for change in ({"scope": "other"}, {"model": "deepseek-v4-pro"},
                   {"input_bound": V6_CONTEXT_WINDOW_TOKENS}):
        budget = _budget(gate, **change)
        with pytest.raises(AttemptDenied, match="V6_REQUEST_BUDGET_INVALID"):
            gate.reserve(request_id=f"v6-invalid-{len(change)}", budget=budget)


def test_v6_deadline_is_enforced_and_pre_send_expiry_releases_reserve(tmp_path):
    gate, path = _gate(tmp_path, now=datetime(2026, 10, 10, 4, 25, 43, tzinfo=timezone.utc))
    attempt = gate.reserve(request_id="v6-near-deadline", budget=_budget(gate))
    gate.clock = lambda: datetime(2026, 10, 10, 4, 25, 44, tzinfo=timezone.utc)
    with pytest.raises(AttemptDenied, match="V6_ACTIVITY_DEADLINE_EXPIRED"):
        gate.validate_before_send(attempt)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert gate._used(data) == 65_569
    assert data["v6_owner_activity"]["attempts"][0]["status"] == "not_sent"


def test_v6_initialization_rejects_baseline_mismatch_without_rewriting(tmp_path):
    path = _ledger(tmp_path)
    grant = OwnerActivityTokenGrant.simulated(baseline_used_tokens=65_570)
    gate = V6OwnerActivityGate(path, grant=grant,
        clock=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc))
    before = path.read_bytes()
    with pytest.raises(AttemptDenied, match="V6_BASELINE_USAGE_MISMATCH"):
        gate.initialize_grant()
    assert path.read_bytes() == before
