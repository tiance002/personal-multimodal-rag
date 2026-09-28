from __future__ import annotations

import hashlib
import json

import pytest

from eval_center.contracts import BundleValidationError, normalize_bundle
from scripts.package_eval_bundle import build_bundle


def source_inputs():
    return (
        {
            "status": "PASS",
            "mode": "deterministic_fixture",
            "dataset_version": "core-v2",
            "metrics": {"hit_at_5": 1.0, "mrr": 0.8, "question": "PRIVATE METRIC TEXT"},
            "cases": [{
                "case_id": "m0-sample-01",
                "question": "PRIVATE QUESTION CANARY",
                "answer": "PRIVATE ANSWER CANARY",
                "retrieved_chunk_ids": ["PRIVATE CHUNK CANARY"],
                "traceback": "PRIVATE TRACEBACK CANARY",
                "api_key": "PRIVATE API KEY CANARY",
                "hit_at_5": True,
                "recall_at_5": 1.0,
                "mrr": 0.8,
                "latency_ms": 3.2,
            }],
            "errors": [{"case_id": "m0-sample-01", "stage": "evaluate", "error_code": "UNKNOWN", "message": "PRIVATE ERROR CANARY"}],
        },
        {
            "experiment_id": "e4a3e2db-5773-4d12-9be9-2db322654e12",
            "git_sha": "a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b",
            "dataset_version": "core-v2",
            "corpus_hash": "a" * 64,
            "model_profile": "q0-deterministic",
            "embedding_profile": "none",
            "started_at": "2026-09-27T10:00:00+08:00",
            "ended_at": "2026-09-27T10:00:03+08:00",
            "environment": {"os_family": "windows", "python_version": "3.12.5", "architecture": "x86_64", "hostname": "PRIVATE HOST CANARY"},
            "hostname": "PRIVATE MANIFEST CANARY",
            "token": "PRIVATE TOKEN CANARY",
        },
        {"top_k": 5, "retrieval_mode": "hybrid", "api_key": "PRIVATE CONFIG CANARY", "prompt": "PRIVATE PROMPT CANARY"},
    )


def test_build_bundle_projects_only_allowlisted_values_and_hides_privacy_canaries():
    report, manifest, config = source_inputs()

    bundle = build_bundle(report, manifest, config)
    serialized = json.dumps(bundle, ensure_ascii=False, sort_keys=True)

    for canary in (
        "PRIVATE QUESTION CANARY", "PRIVATE ANSWER CANARY", "PRIVATE CHUNK CANARY",
        "PRIVATE TRACEBACK CANARY", "PRIVATE API KEY CANARY", "PRIVATE ERROR CANARY",
        "PRIVATE HOST CANARY", "PRIVATE MANIFEST CANARY", "PRIVATE TOKEN CANARY",
        "PRIVATE CONFIG CANARY", "PRIVATE PROMPT CANARY", "PRIVATE METRIC TEXT",
    ):
        assert canary not in serialized
    assert set(bundle) == {"schema_version", "manifest", "config", "metrics", "cases", "errors"}
    assert bundle["cases"][0]["case_id"].startswith("case_")
    assert bundle["cases"][0]["case_id"] != "m0-sample-01"
    assert bundle["metrics"] == {"hit_at_5": 1.0, "mrr": 0.8}
    assert bundle["errors"] == [{
        "case_id": bundle["cases"][0]["case_id"],
        "stage": "evaluate",
        "error_code": "UNKNOWN",
    }]
    normalize_bundle(bundle)


def test_case_ids_are_stable_opaque_hashes_and_manifest_counts_are_derived():
    report, manifest, config = source_inputs()
    first = build_bundle(report, manifest, config)
    changed_answer = json.loads(json.dumps(report))
    changed_answer["cases"][0]["answer"] = "DIFFERENT PRIVATE ANSWER"
    second = build_bundle(changed_answer, manifest, config)

    assert first["cases"][0]["case_id"] == second["cases"][0]["case_id"]
    assert first["cases"][0]["case_id"] == "case_" + hashlib.sha256(b"m0-sample-01").hexdigest()[:12]
    assert first["manifest"]["sample_count"] == 1


def test_build_bundle_rejects_missing_stable_case_id_or_mismatched_dataset_version():
    report, manifest, config = source_inputs()
    del report["cases"][0]["case_id"]
    with pytest.raises((BundleValidationError, ValueError)):
        build_bundle(report, manifest, config)

    report, manifest, config = source_inputs()
    manifest["dataset_version"] = "other-dataset"
    with pytest.raises((BundleValidationError, ValueError)):
        build_bundle(report, manifest, config)
