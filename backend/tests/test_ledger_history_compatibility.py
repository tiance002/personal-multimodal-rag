"""SIMULATED ledger history; no real ledger, credentials, provider or database."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.validation_usd_budget import POLICY, ValidationAttemptGate
from backend.app.ports.session_attempts import AttemptDenied


def history(tmp_path, *, count=53, limit=53, enabled=False):
    rows = []
    for i in range(count):
        rows.append({
            "attempt_id": f"SIMULATED-attempt-{i}", "request_id": f"SIMULATED-request-{i}",
            "status": "ok",
            "validation_tokens": {
                "reserved_input": 2048,
                "reserved_output": 1280 if i in (25, 27, 31, 33, 37, 46, 47, 48) else 512,
                "state": "settled", "input": 100, "output": 10,
            },
            "validation_cost": {"currency": "USD", "scale": 1_000_000},
        })
    data = {
        "schema_version": 1, "provider": "deepseek", "authorized_limit": limit,
        "consumed_attempts": count, "reserved_attempts": 0,
        "calls_allowed_in_this_task": enabled, "attempts": rows,
        "validation_policy": dict(POLICY),
    }
    path = tmp_path / "SIMULATED-history.json"
    save(path, data)
    return path, data


def save(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_completed_53_attempt_history_can_be_read_without_enabling_or_rewriting(tmp_path):
    path, data = history(tmp_path)
    before = path.read_bytes()
    assert SessionAttemptGate(path)._read() == data
    assert ValidationAttemptGate(path)._used(data) == 5830
    with pytest.raises(AttemptDenied, match="SESSION_CALLS_DISABLED"):
        ValidationAttemptGate(path).reserve(request_id="SIMULATED-new")
    assert path.read_bytes() == before


def test_1280_historical_reservation_counts_observed_usage_without_repricing(tmp_path):
    path, data = history(tmp_path, count=1, limit=1)
    data["attempts"][0]["validation_tokens"]["reserved_output"] = 1280
    save(path, data)
    before = path.read_bytes()
    gate = ValidationAttemptGate(path)
    assert gate._used(gate._read()) == 110
    assert path.read_bytes() == before
    with pytest.raises(AttemptDenied, match="VALIDATION_BOUND_INVALID"):
        ValidationAttemptGate(path, max_output_tokens=1280)


@pytest.mark.parametrize("limit", [51, 113, 1_000_000])
@pytest.mark.parametrize("kind", ["generic", "validation"])
def test_history_metadata_cannot_authorize_a_larger_new_attempt_limit(tmp_path, limit, kind):
    path, data = history(tmp_path, count=0, limit=limit, enabled=True)
    if kind == "generic":
        data.pop("validation_policy")
        save(path, data)
    before = path.read_bytes()
    gate = SessionAttemptGate(path) if kind == "generic" else ValidationAttemptGate(path)
    assert gate._read() == data
    with pytest.raises(AttemptDenied, match="SESSION_ATTEMPT_AUTHORIZATION_REQUIRED"):
        gate.reserve(request_id="SIMULATED-unapproved-extension")
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1.0),
    ("authorized_limit", True), ("authorized_limit", 53.0),
    ("authorized_limit", "53"), ("authorized_limit", -1),
    ("authorized_limit", float("inf")), ("authorized_limit", float("nan")),
    ("consumed_attempts", True), ("consumed_attempts", -1),
    ("consumed_attempts", 52), ("reserved_attempts", True),
    ("reserved_attempts", -1), ("reserved_attempts", 1),
])
def test_malformed_history_count_or_schema_still_fails_closed(tmp_path, field, value):
    path, data = history(tmp_path)
    data[field] = value
    save(path, data)
    before = path.read_bytes()
    with pytest.raises(AttemptDenied, match="SESSION_LEDGER_INVALID"):
        SessionAttemptGate(path)._read()
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["duplicate_id", "bad_status", "wrong_provider"])
def test_history_identity_and_status_integrity_remain_required(tmp_path, mutation):
    path, data = history(tmp_path)
    if mutation == "duplicate_id":
        data["attempts"][1]["attempt_id"] = data["attempts"][0]["attempt_id"]
    elif mutation == "bad_status":
        data["attempts"][0]["status"] = "refunded"
    else:
        data["provider"] = "other"
    save(path, data)
    with pytest.raises(AttemptDenied, match="SESSION_LEDGER_INVALID"):
        SessionAttemptGate(path)._read()


@pytest.mark.parametrize("field,value", [
    ("reserved_output", True), ("reserved_output", 1280.0),
    ("reserved_output", -1), ("reserved_output", 0),
    ("reserved_output", 1025), ("reserved_output", 1281),
    ("reserved_output", float("inf")), ("reserved_output", float("nan")),
    ("reserved_input", True), ("reserved_input", -1),
    ("reserved_input", 1_000_000), ("input", True),
    ("input", -1), ("input", 2049), ("output", 513),
])
def test_invalid_historical_token_fields_cannot_reduce_occupancy(tmp_path, field, value):
    path, data = history(tmp_path, count=1, limit=1)
    data["attempts"][0]["validation_tokens"][field] = value
    save(path, data)
    before = path.read_bytes()
    with pytest.raises(AttemptDenied, match="VALIDATION_RECORD_INVALID"):
        ValidationAttemptGate(path)._used(ValidationAttemptGate(path)._read())
    assert path.read_bytes() == before


@pytest.mark.parametrize("location,field,value", [
    ("policy", "token_limit", 1_000_000.0),
    ("policy", "scale", 1_000_000.0),
    ("policy", "token_limit", 1_000_001),
    ("policy", "resets", True),
    ("cost", "scale", 1_000_000.0),
    ("cost", "currency", "UNKNOWN"),
])
def test_policy_and_currency_types_are_not_coerced(tmp_path, location, field, value):
    path, data = history(tmp_path, count=1, limit=1)
    target = data["validation_policy"] if location == "policy" else data["attempts"][0]["validation_cost"]
    target[field] = value
    save(path, data)
    with pytest.raises(AttemptDenied, match="VALIDATION_POLICY_UNAVAILABLE|VALIDATION_RECORD_INVALID"):
        ValidationAttemptGate(path)._used(ValidationAttemptGate(path)._read())


@pytest.mark.parametrize("value", [512.0, True, float("nan"), float("inf"), -1, 1280, "512", None, []])
def test_new_request_output_cap_keeps_strict_original_authorization(tmp_path, value):
    path, _ = history(tmp_path, count=0, limit=1)
    with pytest.raises(AttemptDenied, match="VALIDATION_BOUND_INVALID"):
        ValidationAttemptGate(path, max_output_tokens=value)


def test_pending_and_unknown_history_keep_full_occupancy_even_when_finished(tmp_path):
    path, data = history(tmp_path)
    for i, state in ((50, "unknown"), (51, "reserved")):
        data["attempts"][i]["status"] = state
        data["attempts"][i]["validation_tokens"]["state"] = state
    data["reserved_attempts"] = 1
    save(path, data)
    gate = ValidationAttemptGate(path)
    assert gate._used(gate._read()) == 10730
    gate.finish("SIMULATED-attempt-51", "unknown")
    after = gate._read()
    assert after["consumed_attempts"] == 53 and after["reserved_attempts"] == 0
    assert gate._used(after) == 10730
    assert after["attempts"][50] == data["attempts"][50]
    assert after["attempts"][51]["validation_tokens"]["state"] == "unknown"


@pytest.mark.parametrize("extra", [0, 1])
def test_new_reservation_enforces_million_tokens_including_unknown_history(tmp_path, extra):
    path, data = history(tmp_path, count=1, limit=2, enabled=True)
    row = data["attempts"][0]
    row["status"] = "unknown"
    row["validation_tokens"].update(state="unknown", reserved_input=998464 + extra)
    save(path, data)
    before = path.read_bytes()
    gate = ValidationAttemptGate(path, input_tokens_cap=512)
    if extra:
        with pytest.raises(AttemptDenied, match="VALIDATION_TOKEN_CAP"):
            gate.reserve(request_id="SIMULATED-over-million")
        assert path.read_bytes() == before
    else:
        gate.reserve(request_id="SIMULATED-exact-million")
        assert gate._used(gate._read()) == 1_000_000
        assert gate._read()["attempts"][0] == row


def test_aggregate_unknown_history_over_million_is_not_accepted(tmp_path):
    path, data = history(tmp_path, count=2, limit=2)
    for row in data["attempts"]:
        row["status"] = "unknown"
        row["validation_tokens"].update(state="unknown", reserved_input=599488)
    save(path, data)
    with pytest.raises(AttemptDenied, match="VALIDATION_RECORD_INVALID"):
        ValidationAttemptGate(path)._used(ValidationAttemptGate(path)._read())


def test_parallel_duplicates_still_have_one_atomic_reservation(tmp_path):
    path, _ = history(tmp_path, count=0, limit=2, enabled=True)
    gate = ValidationAttemptGate(path)

    def reserve(_):
        try:
            return gate.reserve(request_id="SIMULATED-same-request")
        except AttemptDenied:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(16)))
    assert sum(result is not None for result in results) == 1
    after = gate._read()
    assert after["consumed_attempts"] == after["reserved_attempts"] == 1
    assert len(after["attempts"]) == 1


def test_failed_atomic_replace_preserves_ledger_and_removes_only_own_temp(tmp_path, monkeypatch):
    from backend.app.application import session_attempts

    path, _ = history(tmp_path, count=0, limit=2, enabled=True)
    before = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("SIMULATED atomic replace failure")

    monkeypatch.setattr(session_attempts.os, "replace", fail_replace)
    with pytest.raises(AttemptDenied, match="SESSION_LEDGER_UNAVAILABLE"):
        ValidationAttemptGate(path).reserve(request_id="SIMULATED-replace-failure")
    assert path.read_bytes() == before
    assert list(tmp_path.glob("SIMULATED-history.json.*.tmp")) == []


@pytest.mark.parametrize("mutation", ["question", "scope", "run", "prompt", "missing_receipt"])
def test_product_scope_and_receipt_denials_still_precede_reservation(tmp_path, monkeypatch, mutation):
    import hashlib
    from types import MappingProxyType

    from backend.app.adapters.models import deepseek
    from backend.app.application import reviewed_immutable_batch as batch
    from backend.app.application import reviewed_product_request as product
    from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
    from backend.app.domain.scope import Scope
    from backend.app.ports.providers import ProviderRequestNotSent
    from backend.app.ports.session_attempts import request_identity

    path, _ = history(tmp_path, count=0, limit=2)
    prompt = "x" * 87
    question = "SIMULATED question"
    run_id = "0669fa1f-ef7e-4b12-8cc2-3eb010379f99"
    scope_id = "SIMULATED-product-history-regression"
    spec = product.ReviewedProductRequestSpec(
        scope_id, run_id, ("SIMULATED-kb",), ("SIMULATED-doc",),
        hashlib.sha256(question.encode()).hexdigest(),
        hashlib.sha256(prompt.encode()).hexdigest(), 343,
    )
    monkeypatch.setattr(batch, "CANONICAL_LEDGER", path)
    monkeypatch.setattr(product, "_TRUSTED_PRODUCT_REQUESTS", MappingProxyType({scope_id: spec}))
    monkeypatch.setattr(product, "_PRODUCT_SPEC_DIGESTS", MappingProxyType({
        scope_id: product._digest(product.asdict(spec)),
    }))
    gate = product.ReviewedProductRequestGate(path, scope_id=scope_id)
    sink = product.ProductReceiptFileSink(tmp_path / "SIMULATED-receipt.json", scope_id=scope_id)

    class NoTransport:
        def open(self, *args, **kwargs):
            raise AssertionError("SIMULATED denial must precede transport")

    monkeypatch.setattr(deepseek, "build_opener", lambda *args: NoTransport())
    gateway = deepseek.DeepSeekGateway(
        api_key="SIMULATED-placeholder", cloud_enabled=True, gate_types=DEEPSEEK_GATE_TYPES,
        attempt_gate=gate, receipt_sink=None if mutation == "missing_receipt" else sink,
    )
    args = {
        "run_id": run_id, "scope": Scope.from_ids(["SIMULATED-kb"], ["SIMULATED-doc"]),
        "question": question, "cloud_authorized": True,
        "request_id": request_identity(run_id, "quick.answer", 1),
    }
    if mutation == "question":
        args["question"] = "SIMULATED different question"
    elif mutation == "scope":
        args["scope"] = Scope.from_ids(["SIMULATED-foreign"], ["SIMULATED-doc"])
    elif mutation == "run":
        args["run_id"] = "0669fa1f-ef7e-4b12-8cc2-3eb010379f98"
    elif mutation == "prompt":
        prompt += "SIMULATED mutation"
    before = path.read_bytes()
    with pytest.raises(ProviderRequestNotSent, match=(
        "PRODUCT_DURABLE_RECEIPT_REQUIRED" if mutation == "missing_receipt" else
        "PRODUCT_FROZEN_REQUEST_MISMATCH" if mutation == "prompt" else "PRODUCT_REQUEST_SCOPE_MISMATCH"
    )):
        gateway.answer_with_product_scope(prompt, 30, 512, **args)
    assert path.read_bytes() == before
    assert not sink.path.exists()


def test_unknown_attempt_cannot_claim_settled_zero_to_admit_over_million(tmp_path):
    path, data = history(tmp_path, count=1, limit=2, enabled=True)
    data["attempts"][0]["status"] = "unknown"
    data["attempts"][0]["validation_tokens"].update(
        reserved_input=998976, reserved_output=512, state="settled", input=0, output=0,
    )
    save(path, data)
    before = path.read_bytes()
    # 998976 + 512 unknown occupancy + 512 + 512 new reserve = 1000512.
    with pytest.raises(AttemptDenied, match="VALIDATION_RECORD_INVALID"):
        ValidationAttemptGate(path, input_tokens_cap=512).reserve(request_id="SIMULATED-overrun")
    assert path.read_bytes() == before


@pytest.mark.parametrize("status,token_state", [
    ("reserved", "settled"), ("unknown", "settled"), ("truncated", "settled"),
    ("ok", "reserved"), ("reserved", "unknown"),
    ("unknown", "reserved"), ("truncated", "reserved"),
    (True, "settled"), (float("nan"), "settled"), (float("inf"), "settled"),
    ("unknown", True), ("unknown", float("nan")), ("unknown", float("inf")),
])
def test_inconsistent_attempt_and_token_states_fail_closed(tmp_path, status, token_state):
    path, data = history(tmp_path, count=1, limit=2)
    data["attempts"][0]["status"] = status
    data["attempts"][0]["validation_tokens"]["state"] = token_state
    data["reserved_attempts"] = 1 if status == "reserved" else 0
    save(path, data)
    before = path.read_bytes()
    with pytest.raises(AttemptDenied, match="SESSION_LEDGER_INVALID|VALIDATION_RECORD_INVALID"):
        gate = ValidationAttemptGate(path)
        gate._used(gate._read())
    assert path.read_bytes() == before


@pytest.mark.parametrize("status,token_state,want", [
    ("reserved", "reserved", 2560), ("ok", "settled", 110),
    ("ok", "unknown", 2560), ("unknown", "unknown", 2560),
    ("truncated", "unknown", 2560),
])
def test_normal_state_pairs_keep_their_correct_conservative_occupancy(tmp_path, status, token_state, want):
    path, data = history(tmp_path, count=1, limit=2)
    data["attempts"][0]["status"] = status
    data["attempts"][0]["validation_tokens"]["state"] = token_state
    data["reserved_attempts"] = 1 if status == "reserved" else 0
    save(path, data)
    before = path.read_bytes()
    gate = ValidationAttemptGate(path)
    assert gate._used(gate._read()) == want
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["reviewed", "pair"])
@pytest.mark.parametrize("denial", ["ungranted", "invalid_history", "allowed"])
def test_activation_denies_ungranted_limit_before_any_write_or_enable(tmp_path, monkeypatch, kind, denial):
    """Isolate activation admission after policy review; never change canonical binding.

    Trusted-entry lookup is supplied in memory. Real lock/read/usage/authorization/
    activation/write/cleanup methods run against a SIMULATED file, with no constructor
    or source registry mutation, external manifest, provider or real ledger access.
    """
    from types import SimpleNamespace
    from backend.app.application.reviewed_immutable_batch import ReviewedImmutableBatchGate
    from backend.app.application.static_pair_validation import StaticPairValidationGate, BATCH_ID

    path, data = history(tmp_path, count=1 if denial == "invalid_history" else 0,
                         limit=113 if denial == "ungranted" else 50, enabled=True)
    if denial == "invalid_history":
        data["attempts"][0]["status"] = "unknown"
        data["attempts"][0]["validation_tokens"].update(state="settled", input=0, output=0)
    if kind == "reviewed":
        tid, bid = "SIMULATED-task", "SIMULATED-batch"
        entry = {"enabled": False, "manifest": {"cases": [{"request_id": "SIMULATED-new", "input_cap": 512}]}}
        data["reviewed_immutable_tasks"] = {tid: {"batches": {bid: entry}}}
        gate = object.__new__(ReviewedImmutableBatchGate)
        ValidationAttemptGate.__init__(gate, path, input_tokens_cap=512)
        gate.batch_id = bid
        gate.task_policy = SimpleNamespace(
            comparison_id=tid, batches=(SimpleNamespace(batch_id=bid),), output_cap=512, attempt_limit=1,
        )
        monkeypatch.setattr(gate, "_task_entry", lambda d: d["reviewed_immutable_tasks"][tid])
        monkeypatch.setattr(gate, "_entry", lambda d: d["reviewed_immutable_tasks"][tid]["batches"][bid])
        monkeypatch.setattr(gate, "_rows", lambda d: [])
    else:
        entry = {"enabled": False}
        data["static_pair_scopes"] = {BATCH_ID: entry}
        gate = object.__new__(StaticPairValidationGate)
        ValidationAttemptGate.__init__(gate, path, input_tokens_cap=512, max_output_tokens=256)
        gate.manifest = {"cases": [{"input_cap": 512} for _ in range(4)]}
        monkeypatch.setattr(gate, "_entry", lambda d: d["static_pair_scopes"][BATCH_ID])
    save(path, data)
    before = path.read_bytes()
    writes = []
    real_write = gate._write

    def record_write(value):
        writes.append(True)
        return real_write(value)

    monkeypatch.setattr(gate, "_write", record_write)
    if denial == "allowed":
        with gate.execution_scope():
            active = gate._read()
            active_entry = gate._entry(active)
            assert active_entry["enabled"] is True
            assert active["calls_allowed_in_this_task"] is True
            assert active["consumed_attempts"] == 0
        after = gate._read()
        assert gate._entry(after)["enabled"] is False
        assert "execution_owner" not in gate._entry(after)
        assert writes == [True, True]
        assert after["consumed_attempts"] == 0
        return
    with pytest.raises(AttemptDenied, match=("SESSION_ATTEMPT_AUTHORIZATION_REQUIRED"
                                           if denial == "ungranted" else "VALIDATION_RECORD_INVALID")):
        with gate.execution_scope():
            pytest.fail("SIMULATED ungranted activation reached yield")
    assert writes == []
    assert path.read_bytes() == before


def reviewed_activation_boundary(tmp_path, monkeypatch, *, count=0, limit=50):
    """SIMULATED reviewed-entry boundary; canonical binding and registries untouched."""
    from types import SimpleNamespace
    from backend.app.application.reviewed_immutable_batch import ReviewedImmutableBatchGate

    path, data = history(tmp_path, count=count, limit=limit, enabled=True)
    tid, bid = "SIMULATED-owner-task", "SIMULATED-owner-batch"
    data["reviewed_immutable_tasks"] = {tid: {"batches": {bid: {
        "enabled": False, "manifest": {"cases": [{"request_id": "SIMULATED-owner-new", "input_cap": 512}]},
    }}}}
    gate = object.__new__(ReviewedImmutableBatchGate)
    ValidationAttemptGate.__init__(gate, path, input_tokens_cap=512)
    gate.batch_id = bid
    gate.task_policy = SimpleNamespace(
        comparison_id=tid, batches=(SimpleNamespace(batch_id=bid),), output_cap=512, attempt_limit=1,
    )
    monkeypatch.setattr(gate, "_task_entry", lambda d: d["reviewed_immutable_tasks"][tid])
    monkeypatch.setattr(gate, "_entry", lambda d: d["reviewed_immutable_tasks"][tid]["batches"][bid])
    monkeypatch.setattr(gate, "_rows", lambda d: [])
    save(path, data)
    return path, data, gate


@pytest.mark.parametrize("reason,code", [
    ("count", "SESSION_CALL_LIMIT"), ("tokens", "VALIDATION_TOKEN_CAP"),
    ("blocked", "VALIDATION_USAGE_BOUND_EXCEEDED"),
])
def test_unowned_budget_rejection_preserves_entire_ledger(tmp_path, monkeypatch, reason, code):
    path, data, gate = reviewed_activation_boundary(tmp_path, monkeypatch, count=1, limit=1)
    if reason == "tokens":
        data["authorized_limit"] = 50
        data["attempts"][0]["status"] = "unknown"
        data["attempts"][0]["validation_tokens"].update(state="unknown", reserved_input=999488)
    elif reason == "blocked":
        data["authorized_limit"] = 50
        data["validation_blocked_reason"] = "VALIDATION_USAGE_BOUND_EXCEEDED"
    save(path, data)
    before = path.read_bytes()
    with pytest.raises(AttemptDenied, match=code):
        with gate.execution_scope():
            pytest.fail("SIMULATED rejected preflight reached activation")
    assert path.read_bytes() == before
    assert gate._read()["calls_allowed_in_this_task"] is True


def test_concurrent_activation_rejections_preserve_active_owner_then_owner_closes(tmp_path, monkeypatch):
    path, _, gate = reviewed_activation_boundary(tmp_path, monkeypatch)
    writes = []
    real_write = gate._write

    def record_write(value):
        writes.append(True)
        real_write(value)

    monkeypatch.setattr(gate, "_write", record_write)
    with gate.execution_scope():
        before = path.read_bytes()
        active_owner = gate._entry(gate._read())["execution_owner"]

        def reject(_):
            with pytest.raises(AttemptDenied, match="REVIEWED_ACTIVATION_DENIED"):
                with gate.execution_scope():
                    pytest.fail("SIMULATED competing activation was admitted")
            return path.read_bytes() == before

        with ThreadPoolExecutor(max_workers=8) as pool:
            assert all(pool.map(reject, range(16)))
        active = gate._read()
        assert gate._entry(active)["execution_owner"] == active_owner
        assert gate._entry(active)["enabled"] is True
        assert active["calls_allowed_in_this_task"] is True
        assert writes == [True]
    closed = gate._read()
    assert gate._entry(closed)["enabled"] is False
    assert "execution_owner" not in gate._entry(closed)
    assert closed["calls_allowed_in_this_task"] is False
    assert closed["consumed_attempts"] == closed["reserved_attempts"] == 0
    assert writes == [True, True]


def test_owner_cleanup_does_not_close_foreign_owner_or_refund_usage(tmp_path, monkeypatch):
    path, data, gate = reviewed_activation_boundary(tmp_path, monkeypatch, count=1)
    own = gate._entry(data)
    own.update(enabled=True, execution_owner="SIMULATED-owned")
    foreign = {"enabled": True, "execution_owner": "SIMULATED-foreign-owner", "manifest": {"cases": []}}
    data["reviewed_immutable_tasks"]["SIMULATED-foreign-task"] = {"batches": {"SIMULATED-foreign-batch": foreign}}
    data["attempts"][0]["status"] = "unknown"
    data["attempts"][0]["validation_tokens"].update(state="unknown")
    save(path, data)
    before = path.read_bytes()
    with gate._locked():
        gate._close_execution(gate._read(), gate.task_policy.comparison_id, "SIMULATED-stale-owner")
    assert path.read_bytes() == before
    with gate._locked():
        gate._close_execution(gate._read(), gate.task_policy.comparison_id, "SIMULATED-owned")
    after = gate._read()
    assert gate._entry(after)["enabled"] is False
    assert "execution_owner" not in gate._entry(after)
    assert after["reviewed_immutable_tasks"]["SIMULATED-foreign-task"]["batches"]["SIMULATED-foreign-batch"] == foreign
    assert after["calls_allowed_in_this_task"] is True
    assert after["attempts"] == data["attempts"]
    assert after["consumed_attempts"] == 1
    assert gate._used(after) == 2560
