"""Build a public benchmark report from provenance manifests and aggregates only."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASETS = ("scifact", "miracl-zh", "longbench-zh")
REPORT_NAME = "public-benchmark-optimization-report.md"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _number(value: Any, places: int = 4) -> str:
    if type(value) not in (int, float) or not math.isfinite(value):
        return "NOT AVAILABLE"
    return f"{value:.{places}f}"


def _metric_row(report: dict[str, Any], variant_id: str) -> dict[str, Any]:
    return report.get("metrics", {}).get(variant_id, {}).get("hybrid", {}).get("metrics", {})


def _run_records(data_root: Path, dataset: str) -> list[dict[str, Any]]:
    base = data_root / "runs" / dataset
    if not base.exists():
        return []
    records = []
    for manifest_path in base.glob("**/manifest.json"):
        try:
            manifest = _read_json(manifest_path)
            report_path = manifest_path.parent / "report.json"
            if manifest.get("dataset") != dataset or not report_path.is_file():
                continue
            report = _read_json(report_path)
            records.append({"manifest": manifest, "report": report, "path": manifest_path.parent})
        except (OSError, ValueError, TypeError):
            continue
    records.sort(key=lambda item: (item["manifest"].get("ended_at", ""), str(item["path"])))
    return records


def _candidate(data_root: Path, dataset: str) -> dict[str, Any] | None:
    paths = list((data_root / "runs" / "candidates" / dataset).glob("*.json")) \
        if (data_root / "runs" / "candidates" / dataset).exists() else []
    for path in sorted(paths, key=lambda item: item.stat().st_mtime_ns, reverse=True):
        try:
            value = _read_json(path)
        except (OSError, ValueError, TypeError):
            continue
        if value.get("profile") == "standard" and value.get("split") == "development":
            return value
    return None


def _qa_summary(records: list[dict[str, Any]]) -> str:
    qa_rows = [case["qa"] for record in records for case in record["report"].get("cases", [])
               if isinstance(case.get("qa"), dict)]
    if not qa_rows:
        return "NOT RUN"
    exact = [row.get("answer_metrics", {}).get("normalized_exact_match") for row in qa_rows]
    f1 = [row.get("answer_metrics", {}).get("character_f1") for row in qa_rows]
    readback = [row.get("citation_readback_rate") for row in qa_rows]

    def mean(values: list[Any]) -> str:
        numeric = [value for value in values if type(value) in (int, float) and math.isfinite(value)]
        return _number(sum(numeric) / len(numeric)) if numeric else "NOT AVAILABLE"

    succeeded = sum(record["manifest"].get("generation_case_count", 0) for record in records
                    if record["manifest"].get("phase") == "qa")
    successful_calls = sum(case.get("generation") == "real_qwen" for record in records
                           for case in record["report"].get("cases", []))
    return (f"{len(qa_rows)} cases; successful Qwen answer cases {successful_calls}; "
            f"normalized exact match {mean(exact)}; character F1 {mean(f1)}; "
            f"citation readback rate {mean(readback)}; configured QA cases {succeeded}.")


def _dataset_section(data_root: Path, dataset: str) -> list[str]:
    adapter_manifest_path = data_root / "prepared" / "adapters" / dataset / "manifest.json"
    lines = [f"## {dataset}", ""]
    if not adapter_manifest_path.is_file():
        lines.extend(["Adapter data: NOT AVAILABLE", "", "Run artifacts: NOT RUN", ""])
        return lines
    adapter = _read_json(adapter_manifest_path)
    split_counts = adapter.get("split_counts", {})
    lines.extend([
        f"- Dataset version: `{adapter.get('dataset_version', 'UNKNOWN')}`",
        f"- Indexed document pool: {adapter.get('document_count', 'UNKNOWN')}",
        f"- Cases: development {split_counts.get('development', 'UNKNOWN')}; locked holdout {split_counts.get('locked_holdout', 'UNKNOWN')}",
        f"- Qrels semantics: `{adapter.get('qrels_kind', 'UNKNOWN')}`",
    ])
    if dataset == "miracl-zh":
        lines.append(f"- Candidate pool ID: `{adapter.get('pool_id', 'UNKNOWN')}`; documents {adapter.get('candidate_documents', 'UNKNOWN')}; seed {adapter.get('sampling_seed', 'UNKNOWN')}")
        excluded = adapter.get("excluded_queries", {})
        lines.append(f"- Incomplete-positive exclusions: train {excluded.get('train', {}).get('missing_positive_queries', 'UNKNOWN')}; dev {excluded.get('dev', {}).get('missing_positive_queries', 'UNKNOWN')}")
    if dataset == "longbench-zh":
        lines.append("- Context relevance is a paired task-source diagnostic, not official retrieval qrels; span recall: `NOT_AVAILABLE`.")
    lines.append(f"- Source terms: {json.dumps(adapter.get('source_licenses', {}), ensure_ascii=False, sort_keys=True)}")
    lines.append("")

    records = _run_records(data_root, dataset)
    if not records:
        lines.extend(["Real retrieval runs: NOT RUN", "", "Qwen QA: NOT RUN", ""])
        return lines
    lines.extend([
        "| Phase | Split | Profile | Documents | Queries | Variant | nDCG@10 | Recall@5 | Context recall@5 | Retrieval ms | Git SHA |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|",
    ])
    for record in records:
        manifest, report = record["manifest"], record["report"]
        for variant_id, _config in manifest.get("effective_configs", {}).items():
            metrics = _metric_row(report, variant_id)
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | `{}` |".format(
                manifest.get("phase", "UNKNOWN"), manifest.get("split", "UNKNOWN"),
                manifest.get("profile", "UNKNOWN"), manifest.get("index_counts", {}).get("documents", "UNKNOWN"),
                manifest.get("sample_count", "UNKNOWN"), variant_id,
                _number(metrics.get("hybrid.document_metrics_at_10.ndcg_at_k")),
                _number(metrics.get("hybrid.document_metrics_at_5.recall_at_k")),
                _number(metrics.get("hybrid.context_document_metrics_at_5.recall_at_k")),
                _number(metrics.get("retrieval_ms"), 2), manifest.get("git_sha", "UNKNOWN")))
    lines.extend(["", f"Qwen QA: {_qa_summary(records)}", ""])

    candidate = _candidate(data_root, dataset)
    if candidate is None:
        lines.extend(["Development candidate: NOT SELECTED", ""])
    else:
        comparison = candidate.get("comparisons_vs_first_variant", {}).get(candidate.get("candidate_variant"), {})
        lines.extend([
            f"- Development candidate: `{candidate.get('candidate_variant', 'UNKNOWN')}` ({candidate.get('candidate_status', 'UNKNOWN')})",
            f"- Selection metric: `{candidate.get('selection_metric', 'UNKNOWN')}`",
            f"- Effective configuration: `{json.dumps(candidate.get('candidate_config', {}), sort_keys=True)}`",
            f"- Paired development cases: {comparison.get('paired_cases', 'UNKNOWN')}; mean delta {_number(comparison.get('mean_delta'))}; 95% bootstrap CI `{json.dumps(comparison.get('ci_95', []))}` ({comparison.get('bootstrap_repetitions', 'UNKNOWN')} resamples).",
        ])
        validation = candidate.get("locked_validation")
        if candidate.get("candidate_status") != "locked_validation_completed" or not isinstance(validation, dict):
            lines.append("- Locked holdout: NOT RUN")
        else:
            lines.append(f"- Locked holdout: COMPLETED once; run `{Path(validation.get('run_dir', '')).name}`; SHA `{validation.get('git_sha', 'UNKNOWN')}`.")
        lines.append("")
    sync_path = data_root / "reports" / f"sync-status-{dataset}.json"
    if sync_path.is_file():
        sync = _read_json(sync_path)
        lines.append(f"- Sanitized server sync: {sync.get('status', 'UNKNOWN')}; transfer {sync.get('remote_transfer', 'UNKNOWN')}; bundle SHA `{sync.get('bundle_sha256', 'UNKNOWN')}`.")
    else:
        lines.append("- Sanitized server sync: NOT RUN.")
    lines.append("")
    return lines


def render_public_report(data_root: Path) -> str:
    data_root = Path(data_root)
    lines = [
        "# Public dataset RAG benchmark report", "",
        f"Generated at {datetime.now(timezone.utc).isoformat()}.", "",
        "## Scope and execution", "",
        "The benchmark uses the production ingestion, chunking, BGE-M3 embeddings, isolated PostgreSQL/pgvector indexes, keyword/vector/hybrid retrieval, context building, and citation readback path. Raw datasets and per-question outputs stay in the external data root. No production RAG defaults were changed and no RAG product release was deployed.", "",
        "| Dataset | Evidence |", "|---|---|",
        "| SciFact | Official document-level BEIR qrels; span-level recall is unavailable. |",
        "| MIRACL Chinese | Fixed 6,000-document candidate pool from official shard 0; results apply only to this pool. |",
        "| LongBench Chinese | Context-to-question links are diagnostic only and are not official retrieval qrels. |", "",
    ]
    for dataset in DATASETS:
        lines.extend(_dataset_section(data_root, dataset))
    lines.extend([
        "## Interpretation and limits", "",
        "- The locked split is reportable only after a full standard validation run; the adapter records one-time use per dataset.",
        "- Retrieval metrics are document-level. No span-level recall is inferred from document IDs.",
        "- Qwen answer exact-match and character F1 are task diagnostics; judge and citation-support scoring remain `NOT_EVALUATED` unless a separately specified verifier actually ran.",
        "- Source licensing and attribution notes are dataset-specific. LongBench underlying item rights remain heterogeneous and unresolved.",
        "- Public server sync status is recorded separately after strict local bundle validation and deployed-code SHA comparison.", "",
        "## Artifacts", "",
        "Full source hashes, per-case outputs, model digests, index fingerprints, paired bootstrap comparisons, and run manifests are retained under the configured local data root. This report intentionally contains only aggregate/provenance fields.", "",
    ])
    return "\n".join(lines)


def write_public_report(data_root: Path, repository_root: Path) -> dict[str, str]:
    content = render_public_report(data_root)
    external_path = Path(data_root) / "reports" / REPORT_NAME
    repository_path = Path(repository_root) / "docs" / "reviews" / REPORT_NAME
    for path in (external_path, repository_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return {"external_path": str(external_path), "repository_path": str(repository_path)}
