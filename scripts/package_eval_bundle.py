from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from eval_center.contracts import (
    BundleValidationError,
    _CONFIG_ENUMS,
    _CONFIG_RULES,
    _ERROR_CODES,
    _METRIC_NAMES,
    _STAGES,
    normalize_bundle,
)


_MANIFEST_FIELDS = {
    "experiment_id", "git_sha", "dataset_version", "corpus_hash", "model_profile",
    "embedding_profile", "started_at", "ended_at",
}
_CONFIG_FIELDS = set(_CONFIG_RULES) | set(_CONFIG_ENUMS)
_REPORT_STATUS = {"PASS": "pass", "FAIL": "fail", "ERROR": "error", "PARTIAL": "partial"}
_CASE_STATUS = {"passed", "failed", "not_evaluated"}


def _private_error(code: str) -> BundleValidationError:
    return BundleValidationError(code)


def _opaque_case_id(source_id: object) -> str:
    if not isinstance(source_id, str) or not source_id or len(source_id) > 256:
        raise _private_error("missing_stable_case_id")
    return "case_" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]


def _project_metrics(raw: object) -> dict[str, int | float]:
    if not isinstance(raw, dict):
        raise _private_error("invalid_metrics")
    projected: dict[str, int | float] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or name not in _METRIC_NAMES:
            continue
        if type(value) is bool:
            continue
        if type(value) not in (int, float) or abs(value) > 1e15 or not math.isfinite(value):
            raise _private_error("invalid_metrics")
        projected[name] = value
    return projected


def _safe_stage(value: object) -> str | None:
    return value if isinstance(value, str) and value in _STAGES else None


def _safe_error_code(value: object) -> str:
    return value if isinstance(value, str) and value in _ERROR_CODES else "UNKNOWN"


def build_bundle(report: dict, manifest: dict, config: dict) -> dict:
    """Project local artifacts into their explicit versioned private contract."""
    if not isinstance(report, dict) or not isinstance(manifest, dict) or not isinstance(config, dict):
        raise _private_error("invalid_input")
    if report.get('schema_version') == 2:
        from eval_center.export_v2 import build_v2_bundle
        return build_v2_bundle(report, manifest, config)
    if report.get('schema_version', 1) != 1:
        raise _private_error('unsupported_schema_version')
    report_dataset = report.get("dataset_version")
    manifest_dataset = manifest.get("dataset_version")
    if report_dataset is not None and report_dataset != manifest_dataset:
        raise _private_error("dataset_version_mismatch")

    config_copy = {key: value for key, value in config.items() if key in _CONFIG_FIELDS}
    config_hash = hashlib.sha256(
        json.dumps(config_copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    cases_source = report.get("cases")
    if not isinstance(cases_source, list):
        raise _private_error("invalid_cases")
    cases: list[dict[str, Any]] = []
    source_to_opaque: dict[str, str] = {}
    seen_opaque_ids: set[str] = set()
    for source_case in cases_source:
        if not isinstance(source_case, dict):
            raise _private_error("invalid_cases")
        source_id = source_case.get("case_id")
        opaque_id = _opaque_case_id(source_id)
        if opaque_id in seen_opaque_ids:
            raise _private_error("duplicate_case_id")
        seen_opaque_ids.add(opaque_id)
        source_to_opaque[source_id] = opaque_id

        status = source_case.get("status")
        if not isinstance(status, str) or status not in _CASE_STATUS:
            passed = source_case.get("pass", source_case.get("passed"))
            if type(passed) is bool:
                status = "passed" if passed else "failed"
            elif type(source_case.get("hit_at_5")) is bool:
                status = "passed" if source_case["hit_at_5"] else "failed"
            else:
                status = "not_evaluated"
        case: dict[str, Any] = {
            "case_id": opaque_id,
            "status": status,
            "metrics": _project_metrics(source_case),
        }
        stage = _safe_stage(source_case.get("stage"))
        if stage is not None:
            case["stage"] = stage
        if source_case.get("error_code") is not None:
            case["error_code"] = _safe_error_code(source_case.get("error_code"))
        cases.append(case)

    errors: list[dict[str, str]] = []
    raw_errors = report.get("errors", [])
    if isinstance(raw_errors, list):
        for raw_error in raw_errors:
            if not isinstance(raw_error, dict):
                continue
            source_id = raw_error.get("case_id")
            opaque_id = source_to_opaque.get(source_id) if isinstance(source_id, str) else None
            stage = _safe_stage(raw_error.get("stage"))
            if opaque_id is None or stage is None:
                continue
            errors.append({
                "case_id": opaque_id,
                "stage": stage,
                "error_code": _safe_error_code(raw_error.get("error_code")),
            })

    raw_status = report.get("status", manifest.get("status"))
    if not isinstance(raw_status, str) or raw_status.upper() not in _REPORT_STATUS:
        raise _private_error("invalid_report_status")
    report_mode = report.get("mode", manifest.get("evaluation_mode"))
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        raise _private_error("invalid_environment")
    clean_manifest = {
        field: manifest[field]
        for field in _MANIFEST_FIELDS
        if field in manifest
    }
    clean_manifest.update({
        "config_hash": config_hash,
        "environment": {
            field: environment[field]
            for field in ("os_family", "python_version", "architecture")
            if field in environment
        },
        "evaluation_mode": report_mode,
        "status": _REPORT_STATUS[raw_status.upper()],
        "sample_count": len(cases),
    })
    bundle = {
        "schema_version": 1,
        "manifest": clean_manifest,
        "config": config_copy,
        "metrics": _project_metrics(report.get("metrics")),
        "cases": cases,
        "errors": errors,
    }
    return normalize_bundle(bundle)


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        raise _private_error("input_too_large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise _private_error("invalid_input")
    return value


def package_files(report_path: Path, manifest_path: Path, config_path: Path, output_path: Path) -> dict[str, object]:
    bundle = build_bundle(
        _read_json(report_path),
        _read_json(manifest_path),
        _read_json(config_path),
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, prefix=".eval-bundle-", suffix=".tmp", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return {"status": "packaged", "experiment_id": bundle["manifest"]["experiment_id"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Project a local RAG report into an allowlisted private bundle.")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = package_files(args.report, args.manifest, args.config, args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, BundleValidationError, ValueError):
        print(json.dumps({"status": "FAIL", "error": "invalid_or_unreadable_input"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
