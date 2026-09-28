from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from typing import Any


class BundleValidationError(ValueError):
    """A bundle validation error with a stable, non-sensitive machine code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("invalid evaluation bundle")


_TOP_FIELDS = {"schema_version", "manifest", "config", "metrics", "cases", "errors"}
_MANIFEST_FIELDS = {
    "experiment_id", "git_sha", "dataset_version", "corpus_hash", "config_hash",
    "model_profile", "embedding_profile", "started_at", "ended_at", "environment",
    "evaluation_mode", "status", "sample_count",
}
_ENVIRONMENT_FIELDS = {"os_family", "python_version", "architecture"}
_CASE_REQUIRED_FIELDS = {"case_id", "status", "metrics"}
_CASE_OPTIONAL_FIELDS = {"stage", "error_code"}
_ERROR_FIELDS = {"case_id", "stage", "error_code"}
_METRIC_NAMES = {
    "hit_at_5", "recall_at_5", "mrr", "precision_at_5", "ndcg_at_5",
    "target_coverage", "mean_target_coverage", "expected_hit_at_5",
    "citation_readback_rate", "citation_precision", "citation_recall",
    "faithfulness", "answer_similarity", "llm_judge_score", "latency_ms",
    "retrieval_latency_ms", "generation_latency_ms", "total_latency_ms",
    "cases", "count", "passed", "failed", "max_chat_model_calls",
    "tokens_in", "tokens_out", "cloud_cost_usd",
}
_CONFIG_RULES: dict[str, tuple[type, int | float | None, int | float | None]] = {
    "top_k": (int, 1, 1000),
    "rrf_k": (int, 1, 1000),
    "chunk_size": (int, 1, 100000),
    "chunk_overlap": (int, 0, 99999),
    "bm25_weight": (float, 0.0, 1.0),
    "vector_weight": (float, 0.0, 1.0),
}
_CONFIG_ENUMS = {
    "retrieval_mode": {"hybrid", "bm25", "vector", "keyword"},
    "query_mode": {"q0", "rules", "l1"},
    "answer_mode": {"not_run", "rules", "local_model"},
    "model_profile": {
        "none", "q0-deterministic", "local-qwen", "local-qwen3.5-4b",
        "local-qwen3-8b", "local-qwen2.5-7b", "local-judge",
    },
    "embedding_profile": {"none", "local-bge-m3"},
}
_MODEL_PROFILES = _CONFIG_ENUMS["model_profile"]
_EMBEDDING_PROFILES = _CONFIG_ENUMS["embedding_profile"]
_EVALUATION_MODES = {
    "deterministic_fixture", "retrieval_only", "quality_v1", "real_rag_local",
    "real_postgres_local",
}
_OVERALL_STATUSES = {"pass", "fail", "error", "partial"}
_CASE_STATUSES = {"passed", "failed", "not_evaluated"}
_OS_FAMILIES = {"windows", "linux", "macos"}
_ARCHITECTURES = {"x86_64", "aarch64", "arm64"}
_STAGES = {"retrieve", "plan", "generate", "judge", "cite", "evaluate", "import"}
_ERROR_CODES = {
    "NO_EVIDENCE", "RETRIEVAL_TIMEOUT", "MODEL_TIMEOUT", "MODEL_UNAVAILABLE",
    "INVALID_RESPONSE", "CITATION_READBACK_FAILED", "CANCELLED", "UNKNOWN",
}
_CASE_ID_RE = re.compile(r"case_[0-9a-f]{12}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{7,40}\Z")
_DATASET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_PYTHON_VERSION_RE = re.compile(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,3}\Z")
_MAX_CASES = 10000
_MAX_ERRORS = 10000
_MAX_METRICS = 64
_MAX_BUNDLE_BYTES = 10 * 1024 * 1024


def parse_bundle_bytes(raw: bytes) -> dict[str, Any]:
    """Parse JSON while rejecting duplicate keys, then return its strict allowlist form."""
    if len(raw) > _MAX_BUNDLE_BYTES:
        _fail("bundle_too_large")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("duplicate_json_key")
            result[key] = value
        return result

    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except BundleValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail("invalid_bundle")
    if not isinstance(decoded, dict):
        _fail("invalid_bundle")
    return normalize_bundle(decoded)


def _fail(code: str = "invalid_bundle") -> None:
    raise BundleValidationError(code)


def _object(value: Any, code: str = "invalid_bundle") -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _fail(code)
    return value


def _exact_fields(value: dict[str, Any], required: set[str], optional: set[str] = frozenset()) -> None:
    keys = set(value)
    if not required <= keys or keys - required - optional:
        _fail("unknown_or_missing_fields")


def _safe_text(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _fail("invalid_bundle")


def _validate_config(raw: Any) -> dict[str, Any]:
    config = _object(raw)
    if set(config) - set(_CONFIG_RULES) - set(_CONFIG_ENUMS):
        _fail("unknown_or_missing_fields")
    result: dict[str, Any] = {}
    for name, value in config.items():
        if name in _CONFIG_RULES:
            expected_type, minimum, maximum = _CONFIG_RULES[name]
            if expected_type is int:
                valid_type = type(value) is int
            else:
                valid_type = type(value) in (int, float) and not isinstance(value, bool)
            if not valid_type or value < minimum or value > maximum or not math.isfinite(value):
                _fail("invalid_config")
            result[name] = value
        else:
            choices = _CONFIG_ENUMS[name]
            if not isinstance(value, str) or value not in choices:
                _fail("invalid_config")
            result[name] = value
    if "chunk_size" in result and result.get("chunk_overlap", 0) >= result["chunk_size"]:
        _fail("invalid_config")
    return result


def _validate_metrics(raw: Any, *, allow_empty: bool = False) -> dict[str, int | float]:
    metrics = _object(raw)
    if len(metrics) > _MAX_METRICS or (not metrics and not allow_empty) or set(metrics) - _METRIC_NAMES:
        _fail("invalid_metrics")
    result: dict[str, int | float] = {}
    for name, value in metrics.items():
        if type(value) not in (int, float) or abs(value) > 1e15 or not math.isfinite(value):
            _fail("invalid_metrics")
        result[name] = value
    return result


def normalize_bundle(raw: object) -> dict[str, Any]:
    """Return a detached, strict allowlist representation of a v1 bundle."""
    bundle = _object(raw)
    if type(bundle.get('schema_version')) is int and bundle['schema_version'] == 2:
        from eval_center.contracts_v2 import normalize_v2
        return normalize_v2(bundle)
    _exact_fields(bundle, _TOP_FIELDS)
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1:
        _fail("unsupported_schema_version")

    manifest = _object(bundle["manifest"])
    _exact_fields(manifest, _MANIFEST_FIELDS)
    experiment_id = manifest["experiment_id"]
    try:
        parsed_id = uuid.UUID(experiment_id) if isinstance(experiment_id, str) else None
    except (ValueError, AttributeError):
        parsed_id = None
    if parsed_id is None or str(parsed_id) != experiment_id:
        _fail("invalid_experiment_id")
    if not _safe_text(manifest["git_sha"], _GIT_SHA_RE):
        _fail("invalid_manifest")
    if not _safe_text(manifest["dataset_version"], _DATASET_RE):
        _fail("invalid_manifest")
    for field in ("corpus_hash", "config_hash"):
        if not _safe_text(manifest[field], _HASH_RE):
            _fail("invalid_manifest")
    if (
        not isinstance(manifest["model_profile"], str)
        or manifest["model_profile"] not in _MODEL_PROFILES
        or not isinstance(manifest["embedding_profile"], str)
        or manifest["embedding_profile"] not in _EMBEDDING_PROFILES
    ):
        _fail("invalid_manifest")
    try:
        started = datetime.fromisoformat(manifest["started_at"])
        ended = datetime.fromisoformat(manifest["ended_at"])
        if started.tzinfo is None or started.utcoffset() is None or ended.tzinfo is None or ended.utcoffset() is None or ended < started:
            _fail("invalid_timestamp")
    except (TypeError, ValueError, OverflowError):
        _fail("invalid_timestamp")
    environment = _object(manifest["environment"])
    _exact_fields(environment, _ENVIRONMENT_FIELDS)
    if (
        not isinstance(environment["os_family"], str)
        or environment["os_family"] not in _OS_FAMILIES
        or not _safe_text(environment["python_version"], _PYTHON_VERSION_RE)
        or not isinstance(environment["architecture"], str)
        or environment["architecture"] not in _ARCHITECTURES
    ):
        _fail("invalid_environment")
    if (
        not isinstance(manifest["evaluation_mode"], str)
        or manifest["evaluation_mode"] not in _EVALUATION_MODES
        or not isinstance(manifest["status"], str)
        or manifest["status"] not in _OVERALL_STATUSES
    ):
        _fail("invalid_manifest")
    if type(manifest["sample_count"]) is not int or not 0 <= manifest["sample_count"] <= _MAX_CASES:
        _fail("invalid_manifest")

    config = _validate_config(bundle["config"])
    config_hash = hashlib.sha256(_canonical_json(config).encode("utf-8")).hexdigest()
    if config_hash != manifest["config_hash"]:
        _fail("config_hash_mismatch")

    metrics = _validate_metrics(bundle["metrics"])
    raw_cases = bundle["cases"]
    if not isinstance(raw_cases, list) or len(raw_cases) > _MAX_CASES or len(raw_cases) != manifest["sample_count"]:
        _fail("invalid_cases")
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        case = _object(raw_case)
        _exact_fields(case, _CASE_REQUIRED_FIELDS, _CASE_OPTIONAL_FIELDS)
        case_id = case["case_id"]
        if not _safe_text(case_id, _CASE_ID_RE) or case_id in seen_ids:
            _fail("invalid_case_id")
        seen_ids.add(case_id)
        if not isinstance(case["status"], str) or case["status"] not in _CASE_STATUSES:
            _fail("invalid_case")
        clean_case: dict[str, Any] = {"case_id": case_id, "status": case["status"]}
        for field in ("stage", "error_code"):
            if field in case:
                value = case[field]
                if value is not None:
                    allowed = _STAGES if field == "stage" else _ERROR_CODES
                    if not isinstance(value, str) or value not in allowed:
                        _fail("invalid_case")
                clean_case[field] = value
        clean_case["metrics"] = _validate_metrics(case["metrics"], allow_empty=True)
        cases.append(clean_case)

    raw_errors = bundle["errors"]
    if not isinstance(raw_errors, list) or len(raw_errors) > _MAX_ERRORS:
        _fail("invalid_errors")
    errors: list[dict[str, str]] = []
    for raw_error in raw_errors:
        error = _object(raw_error)
        _exact_fields(error, _ERROR_FIELDS)
        case_id, stage, error_code = error["case_id"], error["stage"], error["error_code"]
        if (
            not isinstance(case_id, str) or case_id not in seen_ids
            or not isinstance(stage, str) or stage not in _STAGES
            or not isinstance(error_code, str) or error_code not in _ERROR_CODES
        ):
            _fail("invalid_errors")
        errors.append({"case_id": case_id, "stage": stage, "error_code": error_code})

    normalized = {
        "schema_version": 1,
        "manifest": {
            "experiment_id": experiment_id,
            "git_sha": manifest["git_sha"],
            "dataset_version": manifest["dataset_version"],
            "corpus_hash": manifest["corpus_hash"],
            "config_hash": config_hash,
            "model_profile": manifest["model_profile"],
            "embedding_profile": manifest["embedding_profile"],
            "started_at": manifest["started_at"],
            "ended_at": manifest["ended_at"],
            "environment": {
                "os_family": environment["os_family"],
                "python_version": environment["python_version"],
                "architecture": environment["architecture"],
            },
            "evaluation_mode": manifest["evaluation_mode"],
            "status": manifest["status"],
            "sample_count": manifest["sample_count"],
        },
        "config": config,
        "metrics": metrics,
        "cases": cases,
        "errors": errors,
    }
    if len(_canonical_json(normalized).encode("utf-8")) > _MAX_BUNDLE_BYTES:
        _fail("bundle_too_large")
    return normalized


def canonical_bundle_bytes(bundle: dict[str, object]) -> bytes:
    normalized = normalize_bundle(bundle)
    return _canonical_json(normalized).encode("utf-8")


def bundle_digest(bundle: dict[str, object]) -> str:
    return hashlib.sha256(canonical_bundle_bytes(bundle)).hexdigest()
