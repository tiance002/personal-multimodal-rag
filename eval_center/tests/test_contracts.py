from __future__ import annotations

import copy
import hashlib
import json

import pytest

from eval_center.contracts import (
    BundleValidationError,
    bundle_digest,
    canonical_bundle_bytes,
    normalize_bundle,
)


def make_bundle() -> dict[str, object]:
    config: dict[str, object] = {"top_k": 5, "retrieval_mode": "hybrid"}
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "manifest": {
            "experiment_id": "e4a3e2db-5773-4d12-9be9-2db322654e12",
            "git_sha": "a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b",
            "dataset_version": "core-v2-20-cases",
            "corpus_hash": "a" * 64,
            "config_hash": config_hash,
            "model_profile": "q0-deterministic",
            "embedding_profile": "none",
            "started_at": "2026-09-27T10:00:00+08:00",
            "ended_at": "2026-09-27T10:00:03+08:00",
            "environment": {
                "os_family": "windows",
                "python_version": "3.12.5",
                "architecture": "x86_64",
            },
            "evaluation_mode": "deterministic_fixture",
            "status": "pass",
            "sample_count": 1,
        },
        "config": config,
        "metrics": {"hit_at_5": 1.0, "mrr": 0.75},
        "cases": [
            {
                "case_id": "case_123456789abc",
                "status": "passed",
                "stage": "evaluate",
                "error_code": None,
                "metrics": {"mrr": 0.75, "latency_ms": 2.5},
            }
        ],
        "errors": [],
    }


def test_normalize_valid_bundle_returns_a_detached_canonical_shape():
    original = make_bundle()
    normalized = normalize_bundle(original)

    assert normalized == original
    assert normalized is not original
    assert normalized["manifest"] is not original["manifest"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda bundle: bundle.update({"question": "PRIVATE QUESTION"}),
        lambda bundle: bundle["manifest"].update({"hostname": "private-host"}),
        lambda bundle: bundle["cases"][0].update({"retrieved_chunk_ids": ["secret-chunk"]}),
        lambda bundle: bundle["errors"].append({"traceback": "PRIVATE TRACEBACK"}),
    ],
)
def test_normalize_rejects_unknown_or_private_fields(mutate):
    bundle = make_bundle()
    mutate(bundle)

    with pytest.raises(BundleValidationError):
        normalize_bundle(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("experiment_id", "not-a-uuid"),
        ("git_sha", "not-hex"),
        ("corpus_hash", "x" * 64),
        ("config_hash", "0" * 64),
        ("started_at", "2026-09-27T10:00:00"),
        ("ended_at", "2026-09-27T09:00:00+08:00"),
    ],
)
def test_normalize_rejects_invalid_manifest_values(field, value):
    bundle = make_bundle()
    bundle["manifest"][field] = value

    with pytest.raises(BundleValidationError):
        normalize_bundle(bundle)


def test_normalize_rejects_changed_config_hash():
    bundle = make_bundle()
    bundle["config"]["top_k"] = 10

    with pytest.raises(BundleValidationError):
        normalize_bundle(bundle)


@pytest.mark.parametrize("case_id", ["case_short", "case_ABCDEF012345", "real-case-id"])
def test_normalize_rejects_non_opaque_case_ids(case_id):
    bundle = make_bundle()
    bundle["cases"][0]["case_id"] = case_id

    with pytest.raises(BundleValidationError):
        normalize_bundle(bundle)


def test_normalize_rejects_duplicate_case_ids_and_non_finite_or_boolean_metrics():
    duplicate = make_bundle()
    duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
    duplicate["manifest"]["sample_count"] = 2
    with pytest.raises(BundleValidationError):
        normalize_bundle(duplicate)

    for invalid in (float("nan"), float("inf"), True):
        bundle = make_bundle()
        bundle["metrics"]["mrr"] = invalid
        with pytest.raises(BundleValidationError):
            normalize_bundle(bundle)


def test_normalize_rejects_unsupported_schema_version():
    bundle = make_bundle()
    bundle["schema_version"] = 3

    with pytest.raises(BundleValidationError) as exc:
        normalize_bundle(bundle)

    assert exc.value.code == "unsupported_schema_version"


def test_malformed_container_values_raise_validation_errors_without_type_errors():
    malformed_status = make_bundle()
    malformed_status["manifest"]["status"] = []
    with pytest.raises(BundleValidationError):
        normalize_bundle(malformed_status)

    malformed_error = make_bundle()
    malformed_error["errors"] = [{"case_id": [], "stage": "evaluate", "error_code": "UNKNOWN"}]
    with pytest.raises(BundleValidationError):
        normalize_bundle(malformed_error)


def test_canonical_digest_is_stable_and_matches_sha256():
    normalized = normalize_bundle(make_bundle())
    reordered = json.loads(json.dumps(normalized, sort_keys=True))

    encoded = canonical_bundle_bytes(normalized)
    assert encoded == canonical_bundle_bytes(reordered)
    assert bundle_digest(normalized) == hashlib.sha256(encoded).hexdigest()
