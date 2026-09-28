"""Project document-qrels public results through the existing v2 allowlist."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval_center.export_v2 import build_v2_bundle
from eval_center.metrics import aggregate_metrics
from eval_center.verification import compute_case_metrics


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def _public_dataset_version(dataset: str, source_version: str) -> str:
    if dataset == "longbench-zh":
        suffix = "PAIRED-CONTEXT-DIAGNOSTIC-V1"
    else:
        suffix = "DOCUMENT-QRELS-V1"
    return f"{source_version}-{suffix}"


def _public_architecture(value: str) -> str:
    normalized = value.lower().replace("-", "_")
    if normalized in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if normalized in {"aarch64", "arm64"}:
        return normalized
    raise ValueError("unsupported public benchmark architecture")


def _telemetry(case: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    raw_calls = list(variant.get("model_usage", {}).get("calls", []))
    qa = case.get("qa")
    if isinstance(qa, dict):
        raw_calls.extend(qa.get("model_usage", {}).get("calls", []))
    calls = [{key: call.get(key) for key in
              ("stage", "role", "status", "input_tokens", "output_tokens", "latency_ms")}
             for call in raw_calls]
    timing_source = variant.get("timings_ms", {})
    hybrid_metrics = variant.get("metrics", {}).get("hybrid", {})
    timings = {name: hybrid_metrics.get(name, timing_source.get(name)) for name in (
        "query_processing_ms", "keyword_retrieval_ms", "vector_retrieval_ms", "fusion_ms",
        "embedding_ms", "context_building_ms", "retrieval_ms")}
    if isinstance(qa, dict):
        timings["generation_ms"] = qa.get("generation_latency_ms")
        timings["end_to_end_ms"] = qa.get("qa_latency_ms")
    estimated = variant.get("estimated_context_tokens", {}).get("hybrid")
    return {
        "calls": calls,
        "timings": timings,
        "context_tokens": {"availability": "estimated" if estimated is not None else "unavailable",
                           "count": estimated},
        "evidence_tokens": {"availability": "unavailable", "count": None},
    }


def build_public_v2_bundle(run_dir: Path, variant_id: str) -> tuple[dict[str, Any], Path]:
    """Create a locally verifiable, opaque-ID bundle with no public text fields."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    if variant_id not in manifest.get("effective_configs", {}) or variant_id not in report.get("metrics", {}):
        raise ValueError("public run variant is unavailable")
    config = manifest["effective_configs"][variant_id]
    raw_cases = report.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("public run has no cases")
    cases = []
    case_metrics = []
    for raw in raw_cases:
        variant = raw.get("variants", {}).get(variant_id)
        if not isinstance(variant, dict):
            raise ValueError("public run case is missing the selected variant")
        document_rankings = variant.get("document_rankings", {})
        context_rankings = variant.get("context_document_rankings", {})
        raw_rankings = {
            "keyword": list(document_rankings.get("keyword", []))[:config["candidate_k"]],
            "vector": list(document_rankings.get("vector", []))[:config["candidate_k"]],
            "fused": list(document_rankings.get("hybrid", []))[:config["top_k"]],
            "context": list(context_rankings.get("hybrid", []))[:config["top_k"]],
        }
        statistics = {
            "qrels": raw.get("qrels"),
            "rankings": raw_rankings,
            "coverage": {"gold_lengths": {}, "intervals": {}, "threshold": 0.8},
            "telemetry": _telemetry(raw, variant),
        }
        metrics = compute_case_metrics(statistics, config)
        case_metrics.append(metrics)
        cases.append({"case_id": raw["qid"], "status": "not_evaluated",
                      "statistics": statistics, "metrics": metrics})
    summary = aggregate_metrics(case_metrics)
    source_version = manifest["dataset_version"]
    exported_version = _public_dataset_version(manifest["dataset"], source_version)
    export_manifest = {
        "experiment_id": manifest["experiment_id"],
        "git_sha": manifest["git_sha"],
        "dataset_version": exported_version,
        "corpus_hash": manifest["corpus_hash"],
        "config_hash": "0" * 64,  # Replaced by the strict exporter.
        "gold_set_hash": manifest["qrels_hash"],
        "index_version": manifest["index_version"],
        "model_profile": "local-qwen3.5-4b",
        "embedding_profile": "local-bge-m3",
        "started_at": manifest["started_at"],
        "ended_at": manifest["ended_at"],
        "environment": {
            "os_family": manifest["environment"]["os_family"],
            "python_version": manifest["environment"]["python_version"],
            "architecture": _public_architecture(manifest["environment"]["architecture"]),
        },
        "evaluation_mode": "retrieval_only",
        "status": "partial",
        "sample_count": len(cases),
    }
    runtime = {
        "effective_config": config,
        "git_sha": manifest["git_sha"],
        "corpus_hash": manifest["corpus_hash"],
        "gold_set_hash": manifest["qrels_hash"],
        "index_version": manifest["index_version"],
        "models": manifest["models"],
        "index_counts": manifest["index_counts"],
    }
    local_report = {
        "schema_version": 2,
        "dataset_version": exported_version,
        "runtime": runtime,
        "metrics": summary["metrics"],
        "metric_counts": summary["counts"],
        "cases": cases,
        "errors": [],
    }
    bundle = build_v2_bundle(local_report, export_manifest, config)
    destination = run_dir / f"bundle-v2-{variant_id}.json"
    _write(destination, bundle)
    return bundle, destination
