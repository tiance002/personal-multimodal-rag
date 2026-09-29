"""Pre-registered, fail-closed Development A/B/C retrieval review.

This evaluation-only runner never creates or changes an index. It accepts only
the verified localhost clone endpoint and writes question-free result traces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.scope import Scope
from eval_center.metrics import ranking_metrics
from eval_center.public_data import DATASET_VERSIONS, DEFAULT_DATA_ROOT, fold_chunk_ranking, load_public_dataset
from eval_center.public_trace import build_stage_context_trace
from eval_center.query_cache import RunQueryEmbeddingCache
from eval_center.readonly_public_index import attach_completed_index
from eval_center.runtime import committed_source_provenance
from eval_center.verification import ExperimentInvalidError


DATASETS = ("scifact", "miracl-zh", "longbench-zh")
SEED = 20260929
SAMPLE_SIZES = {"scifact": 20, "miracl-zh": 30, "longbench-zh": 10}
BASE_CONFIG = {"top_k": 5, "candidate_k": 32, "rrf_k": 60,
               "chunk_size": 1200, "chunk_overlap": 120,
               "context_budget_chars": 8000}
MODES = {
    "A-vector-only": {"enabled_sources": ("vector",), "source_weights": {}},
    "B-hybrid-rrf": {"enabled_sources": ("keyword", "vector"), "source_weights": {}},
    "C-vector-priority-0.8-0.2": {
        "enabled_sources": ("keyword", "vector"),
        "source_weights": {"keyword": 0.2, "vector": 0.8},
    },
}
M0_DIAGNOSTIC_QIDS = {
    "scifact": ("1036", "1040", "1234", "752"),
    "miracl-zh": ("1022676#0", "1059962#0", "1099346#0", "1205173#0", "855039#0"),
    "longbench-zh": (
        "dureader:91b4e9d1a4d0afdc7c5503c767797fd950169b9b38851c88",
        "multifieldqa_zh:198b2a1122828dd5539b9c9baf7b36849ecdeff4f804b2e6",
        "multifieldqa_zh:58fb54927d4416aabbbe68479eae60d25cac42c9cc7ce869",
        "multifieldqa_zh:87bc3d04c6f924a4b27560e78f8b446e19ec779ffaba1524",
    ),
}
EXPECTED_CLONE = "rag-eval-trust0928-db-clone-20260929t091523z"
EXPECTED_CLONE_VOLUME = "rag-eval-trust0928-db-clone-20260929t091523z"
EXPECTED_PORT = 25437
MAX_RETRIEVALS = 180
MAX_QUERY_EMBEDDINGS = 60
MAX_RUNTIME_SECONDS = 20 * 60
NO_PROGRESS_SECONDS = 5 * 60
STATEMENT_TIMEOUT_MS = 300_000


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    payload = (json.dumps(row, ensure_ascii=False, sort_keys=True,
                          allow_nan=False) + "\n").encode("utf-8")
    with path.open("ab") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _read_development_qids(data_root: Path, dataset: str) -> tuple[list[str], str]:
    directory = Path(data_root) / "prepared" / "adapters" / dataset
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("dataset") != dataset or
            manifest.get("dataset_version") != DATASET_VERSIONS[dataset]):
        raise ExperimentInvalidError("development_dataset_identity_mismatch")
    case_record = next((row for row in manifest.get("files", [])
                        if row.get("path") == "cases/development.jsonl"), None)
    cases_path = directory / "cases" / "development.jsonl"
    if not case_record or not cases_path.is_file():
        raise ExperimentInvalidError("development_cases_missing")
    payload = cases_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if len(payload) != case_record.get("bytes") or digest != case_record.get("sha256"):
        raise ExperimentInvalidError("development_cases_hash_mismatch")
    qids: list[str] = []
    for line in payload.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("split") != "development" or not isinstance(row.get("qid"), str):
            raise ExperimentInvalidError("development_case_identity_mismatch")
        qids.append(row["qid"])
    if len(qids) != len(set(qids)):
        raise ExperimentInvalidError("development_qid_duplicate")
    return qids, digest


def _read_previous_qids(manifest_path: Path) -> tuple[dict[str, set[str]], str]:
    payload = manifest_path.read_bytes()
    manifest = json.loads(payload)
    qid_sets = manifest.get("qid_sets")
    if manifest.get("status") != "COMPLETED" or not isinstance(qid_sets, dict):
        raise ExperimentInvalidError("previous_m1_m2_manifest_invalid")
    result: dict[str, set[str]] = {}
    for dataset in DATASETS:
        qids = qid_sets.get(dataset)
        if not isinstance(qids, list) or any(not isinstance(qid, str) for qid in qids):
            raise ExperimentInvalidError("previous_m1_m2_qids_invalid")
        if len(qids) != len(set(qids)):
            raise ExperimentInvalidError("previous_m1_m2_qids_duplicate")
        result[dataset] = set(qids)
    return result, hashlib.sha256(payload).hexdigest()


def select_qids(qids: Iterable[str], exclusions: set[str], *, dataset: str,
                size: int, seed: int = SEED) -> tuple[list[str], dict[str, str]]:
    """Uniform deterministic selection using only eligible Development IDs."""
    values = list(qids)
    if len(values) != len(set(values)) or any(not isinstance(qid, str) or not qid for qid in values):
        raise ValueError("invalid_development_qids")
    eligible = [qid for qid in values if qid not in exclusions]
    ordered = sorted(eligible, key=lambda qid: (
        hashlib.sha256(f"{dataset}|{qid}|{seed}".encode("utf-8")).hexdigest(), qid
    ))
    if len(ordered) < size:
        raise ExperimentInvalidError("insufficient_eligible_development_qids")
    selected = ordered[:size]
    return selected, {qid: hashlib.sha256(
        f"{dataset}|{qid}|{seed}".encode("utf-8")).hexdigest() for qid in selected}


def freeze_preregistration(*, data_root: Path, m1_m2_manifest: Path,
                           output_dir: Path) -> dict[str, Any]:
    """Freeze sample and settings before attaching to a database or calling a model."""
    source_provenance = committed_source_provenance(Path(__file__).resolve().parents[1])
    m1_qids, m1_hash = _read_previous_qids(m1_m2_manifest)
    selected: dict[str, list[str]] = {}
    selection_digests: dict[str, dict[str, str]] = {}
    dev_hashes: dict[str, str] = {}
    candidate_counts: dict[str, int] = {}
    excluded: dict[str, list[str]] = {}
    for dataset in DATASETS:
        dev_qids, dev_hashes[dataset] = _read_development_qids(data_root, dataset)
        m0 = set(M0_DIAGNOSTIC_QIDS[dataset])
        blocked = m1_qids[dataset] | m0
        if not m0 <= set(dev_qids) or not m1_qids[dataset] <= set(dev_qids):
            raise ExperimentInvalidError("excluded_qid_outside_development")
        chosen, digests = select_qids(dev_qids, blocked, dataset=dataset,
                                      size=SAMPLE_SIZES[dataset])
        selected[dataset] = chosen
        selection_digests[dataset] = digests
        candidate_counts[dataset] = len(dev_qids)
        excluded[dataset] = sorted(blocked)
    cases_bytes = b"".join(
        (json.dumps({"dataset": dataset, "qid": qid,
                     "selection_sha256": selection_digests[dataset][qid]},
                    ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for dataset in DATASETS for qid in selected[dataset]
    )
    qid_hash = _canonical_hash(selected)
    prereg = {
        "schema": "m3-random-development-abc-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_command": [sys.executable, "-m", "eval_center.public_m3_abc", *sys.argv[1:]],
        "seed": SEED,
        "selection_rule": "sort SHA256(dataset|qid|seed), ascending, take first requested count",
        "split": "development",
        "dataset_versions": {name: DATASET_VERSIONS[name] for name in DATASETS},
        "development_cases_sha256": dev_hashes,
        "development_candidate_counts": candidate_counts,
        "excluded_qids": excluded,
        "m1_m2_manifest_path": str(m1_m2_manifest),
        "m1_m2_manifest_sha256": m1_hash,
        "source_provenance": source_provenance,
        "m0_diagnostic_qids": {key: list(value) for key, value in M0_DIAGNOSTIC_QIDS.items()},
        "qid_sets": selected,
        "qid_set_sha256": qid_hash,
        "cases_jsonl_sha256": hashlib.sha256(cases_bytes).hexdigest(),
        "configs": {
            name: {**BASE_CONFIG, "enabled_sources": list(spec["enabled_sources"]),
                  "source_weights": spec["source_weights"]}
            for name, spec in MODES.items()
        },
        "execution_order": "per dataset QID index modulo 3: ABC, BCA, CAB",
        "limits": {"max_qids": 60, "max_retrievals": MAX_RETRIEVALS,
                   "max_successful_query_embeddings": MAX_QUERY_EMBEDDINGS,
                   "max_runtime_seconds": MAX_RUNTIME_SECONDS,
                   "stop_after_no_progress_seconds": NO_PROGRESS_SECONDS},
        "forbidden_work": ["corpus_embeddings", "new_index", "download", "generation",
                           "judge", "locked_holdout", "retry", "sample_expansion"],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    cases_path = output_dir / "cases.jsonl"
    prereg_path = output_dir / "preregistration.json"
    _write_new(cases_path, cases_bytes)
    prereg_bytes = (json.dumps(prereg, ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + "\n").encode("utf-8")
    _write_new(prereg_path, prereg_bytes)
    prereg["preregistration_sha256"] = hashlib.sha256(prereg_bytes).hexdigest()
    prereg["cases_path"] = str(cases_path)
    prereg["preregistration_path"] = str(prereg_path)
    return prereg


def verify_frozen_sample(preregistration_path: Path, cases_path: Path,
                         data_root: Path,
                         expected_preregistration_sha256: str | None = None) -> dict[str, Any]:
    prereg_bytes = preregistration_path.read_bytes()
    prereg = json.loads(prereg_bytes)
    prereg_hash = hashlib.sha256(prereg_bytes).hexdigest()
    if (expected_preregistration_sha256 is not None and
            prereg_hash != expected_preregistration_sha256):
        raise ExperimentInvalidError("preregistration_hash_mismatch")
    case_bytes = cases_path.read_bytes()
    if hashlib.sha256(case_bytes).hexdigest() != prereg.get("cases_jsonl_sha256"):
        raise ExperimentInvalidError("preregistered_cases_hash_mismatch")
    if prereg.get("seed") != SEED or prereg.get("split") != "development":
        raise ExperimentInvalidError("preregistration_identity_mismatch")
    expected_configs = {
        name: {**BASE_CONFIG, "enabled_sources": list(spec["enabled_sources"]),
               "source_weights": spec["source_weights"]}
        for name, spec in MODES.items()
    }
    expected_limits = {"max_qids": 60, "max_retrievals": MAX_RETRIEVALS,
                       "max_successful_query_embeddings": MAX_QUERY_EMBEDDINGS,
                       "max_runtime_seconds": MAX_RUNTIME_SECONDS,
                       "stop_after_no_progress_seconds": NO_PROGRESS_SECONDS}
    if (prereg.get("configs") != expected_configs or prereg.get("limits") != expected_limits
            or prereg.get("dataset_versions") != {name: DATASET_VERSIONS[name] for name in DATASETS}
            or prereg.get("execution_order") != "per dataset QID index modulo 3: ABC, BCA, CAB"):
        raise ExperimentInvalidError("preregistration_config_mismatch")
    if committed_source_provenance(Path(__file__).resolve().parents[1]) != prereg.get("source_provenance"):
        raise ExperimentInvalidError("preregistered_source_provenance_mismatch")
    historical_path = Path(prereg.get("m1_m2_manifest_path", ""))
    historical_qids, historical_hash = _read_previous_qids(historical_path)
    if historical_hash != prereg.get("m1_m2_manifest_sha256"):
        raise ExperimentInvalidError("historical_m1_m2_manifest_changed")
    for dataset in DATASETS:
        expected_exclusions = sorted(historical_qids[dataset] | set(M0_DIAGNOSTIC_QIDS[dataset]))
        if prereg.get("excluded_qids", {}).get(dataset) != expected_exclusions:
            raise ExperimentInvalidError("preregistration_exclusion_mismatch")
    qid_sets: dict[str, list[str]] = {dataset: [] for dataset in DATASETS}
    rows = [json.loads(line) for line in case_bytes.decode("utf-8").splitlines() if line.strip()]
    for row in rows:
        if row.get("dataset") not in qid_sets or not isinstance(row.get("qid"), str):
            raise ExperimentInvalidError("preregistered_case_invalid")
        qid_sets[row["dataset"]].append(row["qid"])
    if qid_sets != prereg.get("qid_sets") or _canonical_hash(qid_sets) != prereg.get("qid_set_sha256"):
        raise ExperimentInvalidError("preregistered_qid_set_mismatch")
    for dataset in DATASETS:
        current_qids, current_hash = _read_development_qids(data_root, dataset)
        if current_hash != prereg["development_cases_sha256"][dataset]:
            raise ExperimentInvalidError("development_cases_changed_after_preregistration")
        selected, digests = select_qids(
            current_qids, set(prereg["excluded_qids"][dataset]), dataset=dataset,
            size=SAMPLE_SIZES[dataset], seed=prereg["seed"],
        )
        if selected != qid_sets[dataset]:
            raise ExperimentInvalidError("preregistered_sample_not_hash_ordered")
        expected_digests = {row["qid"]: row["selection_sha256"] for row in rows
                            if row["dataset"] == dataset}
        if expected_digests != digests:
            raise ExperimentInvalidError("preregistered_selection_digest_mismatch")
    return {**prereg, "preregistration_sha256": prereg_hash,
            "cases_sha256": hashlib.sha256(case_bytes).hexdigest()}


def _verify_clone_container(name: str, volume: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(["docker", "inspect", name], check=True,
                                   capture_output=True, text=True, timeout=15)
        rows = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ExperimentInvalidError("clone_container_inspect_failed") from exc
    if len(rows) != 1 or rows[0].get("Name", "").lstrip("/") != name:
        raise ExperimentInvalidError("clone_container_identity_mismatch")
    item = rows[0]
    mounts = [mount for mount in item.get("Mounts", [])
              if mount.get("Destination") in ("/var/lib/postgresql/data", "/var/lib/postgresql")]
    if len(mounts) != 1 or mounts[0].get("Name") != volume:
        raise ExperimentInvalidError("clone_volume_identity_mismatch")
    if not item.get("State", {}).get("Running"):
        raise ExperimentInvalidError("verified_clone_not_running")
    bindings = item.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
    normalized = sorted((row.get("HostIp"), row.get("HostPort")) for row in bindings)
    if normalized != [("127.0.0.1", str(EXPECTED_PORT))]:
        raise ExperimentInvalidError("clone_endpoint_identity_mismatch")
    return {"container_id": item.get("Id"), "container_name": name,
            "volume_name": volume, "endpoint": f"127.0.0.1:{EXPECTED_PORT}",
            "running": True}


def _database_urls(admin_url: str, expected_identities: dict[str, Any]) -> dict[str, str]:
    parsed = make_url(admin_url)
    if (parsed.get_backend_name() != "postgresql" or parsed.host != "127.0.0.1"
            or parsed.port != EXPECTED_PORT or parsed.database != "postgres"):
        raise ExperimentInvalidError("clone_admin_endpoint_mismatch")
    result = {}
    for dataset in DATASETS:
        target = expected_identities[dataset]["database_name"]
        if not target.startswith("rag_eval_trust_"):
            raise ExperimentInvalidError("expected_database_name_invalid")
        result[dataset] = parsed.set(database=target).render_as_string(hide_password=False)
    return result


def _verify_hnsw(attachment: dict[str, Any]) -> dict[str, Any]:
    with attachment["engine"].connect() as connection:
        readonly = connection.execute(text("SHOW transaction_read_only")).scalar_one()
        rows = connection.execute(text("""
            SELECT i.indisvalid, i.indisready, pg_get_indexdef(c.oid) AS index_definition
            FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname='chunk_embeddings_hnsw_idx'
        """)).mappings().all()
    if readonly != "on" or len(rows) != 1:
        raise ExperimentInvalidError("readonly_hnsw_index_missing")
    row = dict(rows[0])
    definition = row["index_definition"].lower()
    if not row["indisvalid"] or not row["indisready"] or "using hnsw" not in definition or "vector_cosine_ops" not in definition:
        raise ExperimentInvalidError("readonly_hnsw_index_invalid")
    return {"name": "chunk_embeddings_hnsw_idx", "valid": True,
            "ready": True, "definition_sha256": hashlib.sha256(
                row["index_definition"].encode("utf-8")).hexdigest()}


def validate_retrieval_result(retriever: HybridRetriever, result: Any, mode: str):
    """Reject fallback, missing vector, source drift, and config drift before scoring."""
    if mode not in MODES:
        raise ExperimentInvalidError("unknown_m3_mode")
    spec = MODES[mode]
    expected_config = {key: BASE_CONFIG[key] for key in ("top_k", "candidate_k", "rrf_k")}
    actual = getattr(result, "effective_config", None)
    if actual != expected_config or retriever.effective_config() != expected_config:
        raise ExperimentInvalidError("retrieval_config_mismatch")
    if tuple(retriever.enabled_sources) != tuple(spec["enabled_sources"]):
        raise ExperimentInvalidError("retriever_source_config_mismatch")
    if retriever.source_weights != spec["source_weights"]:
        raise ExperimentInvalidError("retriever_weight_config_mismatch")
    expected_sources = set(spec["enabled_sources"])
    if set(getattr(result, "sources", ())) != expected_sources:
        raise ExperimentInvalidError("retrieval_source_set_mismatch")
    if getattr(result, "degradation_flags", ()):
        raise ExperimentInvalidError("retrieval_degraded")
    rankings = getattr(result, "candidate_rankings", {})
    vector_hits = rankings.get("vector", ())
    if not vector_hits or len(vector_hits) > BASE_CONFIG["candidate_k"]:
        raise ExperimentInvalidError("vector_candidates_missing")
    if mode == "A-vector-only":
        return vector_hits
    fused = getattr(result, "fused_ranking", ())
    if not fused:
        raise ExperimentInvalidError("fused_candidates_missing")
    return fused


def _check_expected_index(dataset: str, attachment: dict[str, Any], expected: dict[str, Any]) -> None:
    current = {
        "database_name": attachment["database_name"],
        "schema_revision": attachment["schema_revision"],
        "index_version": attachment["index"]["index_version"],
        "index_counts": attachment["index"]["index_counts"],
        "model_digests": attachment["actual_models"],
    }
    if current != expected[dataset]:
        raise ExperimentInvalidError("clone_index_or_model_identity_mismatch")


def _query_cache_state(before: dict[str, int], after: dict[str, int]) -> str:
    if after["cache_hits"] > before["cache_hits"]:
        return "WARM_CACHE_HIT"
    if after["actual_embedding_calls"] > before["actual_embedding_calls"]:
        return "COLD_EMBEDDING"
    return "NO_EMBEDDING_CALL"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def _bootstrap_mean_interval(values: list[float], seed_material: str,
                             samples: int = 2000) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        return [values[0], values[0]]
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    means = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples)]
    return [_percentile(means, 0.025), _percentile(means, 0.975)]


def summarize_results(results_path: Path) -> dict[str, Any]:
    """Build per-dataset descriptive metrics and paired exploratory deltas."""
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    completed = [row for row in rows if row.get("status") == "COMPLETE"]
    output: dict[str, Any] = {"valid_rows": len(completed), "invalid_rows": len(rows) - len(completed),
                              "datasets": {}}
    metric_names = ("ndcg_at_10", "recall_at_5", "mrr_at_5",
                    "context_source_recall_at_5", "source_diversity",
                    "parent_diversity", "context_chars")
    pairs = (("A-vector-only", "B-hybrid-rrf"),
             ("B-hybrid-rrf", "C-vector-priority-0.8-0.2"),
             ("A-vector-only", "C-vector-priority-0.8-0.2"))
    for dataset in DATASETS:
        data_rows = [row for row in completed if row["dataset"] == dataset]
        by_mode = {mode: {row["qid"]: row for row in data_rows if row["mode"] == mode}
                   for mode in MODES}
        dataset_summary: dict[str, Any] = {"mode_metrics": {}, "paired_deltas": {},
                                           "positive_transitions": {}}
        for mode in MODES:
            mode_rows = list(by_mode[mode].values())
            metrics: dict[str, Any] = {"n": len(mode_rows)}
            for name in metric_names:
                values = [row["metrics"].get(name) for row in mode_rows
                          if isinstance(row.get("metrics", {}).get(name), (int, float))]
                metrics[name] = statistics.mean(values) if values else None
            metrics["judged_to_unjudged_ratio"] = (
                statistics.mean([row["judged_to_unjudged_ratio"] for row in mode_rows])
                if dataset == "miracl-zh" and mode_rows else None
            )
            metrics["cache_timing"] = {}
            for cache_state in ("COLD_EMBEDDING", "WARM_CACHE_HIT"):
                state_rows = [row for row in mode_rows if row.get("cache_state") == cache_state]
                retrieval_times = [row["metrics"]["retrieval_ms"] for row in state_rows
                                   if isinstance(row.get("metrics", {}).get("retrieval_ms"), (int, float))]
                embedding_times = [row["metrics"]["embedding_ms"] for row in state_rows
                                   if isinstance(row.get("metrics", {}).get("embedding_ms"), (int, float))]
                metrics["cache_timing"][cache_state] = {
                    "n": len(state_rows),
                    "retrieval_ms_p50": _percentile(retrieval_times, 0.5),
                    "embedding_ms_p50": _percentile(embedding_times, 0.5),
                }
            dataset_summary["mode_metrics"][mode] = metrics
        for left, right in pairs:
            pair_key = f"{left}__vs__{right}"
            common = sorted(set(by_mode[left]) & set(by_mode[right]))
            paired: dict[str, Any] = {"n": len(common), "metrics": {}}
            for name in metric_names:
                deltas = []
                for qid in common:
                    lvalue = by_mode[left][qid]["metrics"].get(name)
                    rvalue = by_mode[right][qid]["metrics"].get(name)
                    if isinstance(lvalue, (int, float)) and isinstance(rvalue, (int, float)):
                        deltas.append(float(rvalue) - float(lvalue))
                paired["metrics"][name] = {
                    "mean_delta": statistics.mean(deltas) if deltas else None,
                    "bootstrap_95_percentile_interval": _bootstrap_mean_interval(
                        deltas, f"{dataset}|{left}|{right}|{name}"),
                    "n": len(deltas),
                }
            dataset_summary["paired_deltas"][pair_key] = paired
            for field in ("positive_document_hits_at_5", "positive_context_hits"):
                entered: list[int] = []
                exited: list[int] = []
                for qid in common:
                    left_hits = set(by_mode[left][qid].get(field, []))
                    right_hits = set(by_mode[right][qid].get(field, []))
                    entered.append(len(right_hits - left_hits))
                    exited.append(len(left_hits - right_hits))
                dataset_summary["positive_transitions"].setdefault(pair_key, {})[field] = {
                    "paired_qids": len(common),
                    "mean_entered_per_qid": statistics.mean(entered) if entered else None,
                    "mean_exited_per_qid": statistics.mean(exited) if exited else None,
                    "total_entered": sum(entered), "total_exited": sum(exited),
                }
        output["datasets"][dataset] = dataset_summary
    return output


def render_report(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    """Render sanitized aggregate results; excludes questions, QIDs, and chunk IDs."""
    lines = [
        "# M3 Development 随机样本 A/B/C 检索复核",
        "",
        f"- 状态：`{manifest.get('status', 'UNKNOWN')}`",
        f"- 预注册样本：SciFact 20、MIRACL-ZH 30、LongBench-ZH 10；seed `{SEED}`，按 `SHA256(dataset|qid|seed)` 排序。",
        f"- 有效模式结果：{summary['valid_rows']}；无效尝试：{summary['invalid_rows']}；检索尝试：{manifest.get('retrieval_attempts', 0)}。",
        f"- 成功查询 Embedding：{manifest.get('successful_query_embeddings', 0)}；语料 Embedding、新索引、下载、生成、Judge、Locked 查询：均为 0。",
        f"- 总耗时：{manifest.get('elapsed_seconds', 'NOT_RECORDED')} 秒；检索阶段：{manifest.get('retrieval_elapsed_seconds', 'NOT_RECORDED')} 秒。",
        f"- Token 用量：`{manifest.get('model_tokens', 'NOT_MEASURED')}`。",
        "- 源码身份：`{}` / Tree `{}` / Patch SHA256 `{}`。".format(
            manifest.get("source_provenance", {}).get("git_sha", "UNKNOWN"),
            manifest.get("source_provenance", {}).get("tree_oid", "UNKNOWN"),
            manifest.get("source_provenance", {}).get("patch_sha256", "UNKNOWN")),
        f"- Clone：`{manifest.get('clone_identity', {}).get('container_name', 'UNKNOWN')}` / ID `{manifest.get('clone_identity', {}).get('container_id', 'UNKNOWN')}`，endpoint `{manifest.get('clone_identity', {}).get('endpoint', 'UNKNOWN')}`。",
        "",
    ]
    lines.extend(["| 数据集 | 数据库 | index hash | Embedding digest | Chat digest | HNSW verified |",
                  "|---|---|---|---|---|---|"])
    for dataset in DATASETS:
        identity = manifest.get("index_identities", {}).get(dataset, {})
        models = identity.get("model_digests", {})
        lines.append("| {} | `{}` | `{}` | `{}` | `{}` | {} |".format(
            dataset, identity.get("database_name", "UNKNOWN"), identity.get("index_version", "UNKNOWN"),
            models.get("embedding", {}).get("digest", "UNKNOWN"),
            models.get("chat", {}).get("digest", "UNKNOWN"),
            identity.get("hnsw", {}).get("valid", False)))
    lines.extend(["", "## 各数据集结果（分开报告）", ""])
    for dataset in DATASETS:
        info = summary["datasets"][dataset]
        lines.extend([f"### {dataset}", "",
                      "| 模式 | n | nDCG@10 | Recall@5 | MRR@5 | Context source Recall@5 | 平均来源数 | 平均 Context 字符 | judged/unjudged 比率* |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
        for mode, values in info["mode_metrics"].items():
            def fmt(value):
                return "—" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                mode, values["n"], fmt(values["ndcg_at_10"]), fmt(values["recall_at_5"]),
                fmt(values["mrr_at_5"]), fmt(values["context_source_recall_at_5"]),
                fmt(values["source_diversity"]), fmt(values["context_chars"]),
                fmt(values["judged_to_unjudged_ratio"])))
        lines.extend(["", "Paired exploratory deltas (right − left; percentile bootstrap interval for the mean):", "",
                      "| 比较 | 指标 | n | 平均差 | 95% 区间 |", "|---|---|---:|---:|---:|"])
        for pair, paired in info["paired_deltas"].items():
            for metric in ("ndcg_at_10", "recall_at_5", "mrr_at_5", "context_source_recall_at_5"):
                value = paired["metrics"][metric]
                interval = value["bootstrap_95_percentile_interval"]
                delta = value["mean_delta"]
                lines.append("| {} | {} | {} | {} | {} |".format(
                    pair, metric, value["n"], "—" if delta is None else f"{delta:.4f}",
                    "—" if interval is None else f"[{interval[0]:.4f}, {interval[1]:.4f}]"))
        lines.extend(["", "正例进入/退出（按固定 qrels 的来源文档集合计数）:", "",
                      "| 比较 | 排名/Context | 配对 QID 数 | 进入总数 | 退出总数 |",
                      "|---|---|---:|---:|---:|"])
        for pair, fields in info["positive_transitions"].items():
            for field, values in fields.items():
                label = "Top 5 文档" if field == "positive_document_hits_at_5" else "Context 来源"
                lines.append("| {} | {} | {} | {} | {} |".format(
                    pair, label, values["paired_qids"], values["total_entered"], values["total_exited"]))
        lines.extend(["", "Embedding 缓存冷热计时（每个 QID 的 A/B/C 共用一次成功查询向量）:", "",
                      "| 模式 | 状态 | n | retrieval p50 ms | embedding p50 ms |",
                      "|---|---|---:|---:|---:|"])
        for mode, values in info["mode_metrics"].items():
            for state, timings in values["cache_timing"].items():
                retrieval_p50 = timings["retrieval_ms_p50"]
                embedding_p50 = timings["embedding_ms_p50"]
                lines.append("| {} | {} | {} | {} | {} |".format(
                    mode, state, timings["n"],
                    "—" if retrieval_p50 is None else f"{retrieval_p50:.2f}",
                    "—" if embedding_p50 is None else f"{embedding_p50:.2f}"))
        lines.append("")
        if dataset == "miracl-zh":
            lines.append("* MIRACL judged/unjudged 比率按固定 6,000 文档候选池计算；该结果不外推到完整 MIRACL 语料。")
            lines.append("")
        if dataset == "longbench-zh":
            lines.append("LongBench 是 paired task-context 诊断，不是官方检索 qrels 或完整语料检索。")
            lines.append("")
    lines.extend([
        "## 运行边界与解释",
        "",
        "A 是独立 Vector-only 调用；B 是当前无权重 Hybrid RRF；C 固定 Keyword 0.2 / Vector 0.8，RRF k=60。三者 candidate depth 32、top_k 5、chunk 1200/120、context budget 8,000 字符。",
        "每个 QID 轮换 ABC、BCA、CAB；缓存冷/热计时单独记录，不能据此宣称模式延迟优劣。正例进入/退出统计只描述固定 qrels 下的检索与 Context 变化。",
        "小样本区间仅作探索性不确定性描述；结果不能说明答案正确性、引用语义正确性或 Locked Holdout 性能。未测量 token 数。",
        "",
    ])
    return "\n".join(lines)


def _score(mode: str, case: Any, result: Any, rankings: Any,
           attachment: dict[str, Any], context_builder: ContextBuilder,
           dataset: str) -> dict[str, Any]:
    chunk_ids = [hit.chunk_id for hit in rankings]
    chunk_to_document = {chunk_id: span.document_id
                         for chunk_id, span in attachment["index"]["spans"].items()}
    document_rank = fold_chunk_ranking(chunk_ids, chunk_to_document)
    context_start = time.perf_counter()
    context, trace = build_stage_context_trace(
        case.qid, mode, rankings, attachment["index"], context_builder,
        top_k=BASE_CONFIG["top_k"],
        effective_config={**BASE_CONFIG, **MODES[mode]},
    )
    context_elapsed_ms = (time.perf_counter() - context_start) * 1000
    context_documents = fold_chunk_ranking(context["selected_chunk_ids"], chunk_to_document)
    if context_documents != trace["selected_source_ids"]:
        raise ExperimentInvalidError("context_source_mapping_mismatch")
    positives = {doc for doc, grade in case.qrels.items() if grade > 0}
    doc_metrics_5 = ranking_metrics(document_rank, case.qrels, k=5)
    doc_metrics_10 = ranking_metrics(document_rank, case.qrels, k=10)
    context_metrics_5 = ranking_metrics(context_documents, case.qrels, k=5)
    total_pool = len(attachment["index"]["document_ids"])
    judged = len(case.qrels)
    selected_parents = trace["selected_parent_ids"]
    timing = result.stage_latency_ms
    return {
        "status": "COMPLETE", "dataset": dataset,
        "qid": case.qid, "mode": mode, "qrels_kind": case.qrels_kind,
        "qrels_count": judged, "positive_qrels_count": len(positives),
        "judged_pool_documents": judged,
        "unjudged_pool_documents": max(0, total_pool - judged),
        "judged_to_unjudged_ratio": (judged / max(1, total_pool - judged)),
        "document_ranking": document_rank,
        "context_source_ids": context_documents,
        "positive_document_hits_at_5": [doc for doc in document_rank[:5] if doc in positives],
        "positive_context_hits": [doc for doc in context_documents if doc in positives],
        "metrics": {
            "ndcg_at_10": doc_metrics_10["ndcg_at_k"],
            "recall_at_5": doc_metrics_5["recall_at_k"],
            "mrr_at_5": doc_metrics_5["mrr_at_k"],
            "context_source_recall_at_5": context_metrics_5["recall_at_k"],
            "source_diversity": len(set(trace["selected_source_ids"])),
            "parent_diversity": len(set(selected_parents)),
            "context_chars": trace["rendered_chars"],
            "context_building_ms": context_elapsed_ms,
            "embedding_ms": timing.get("embedding_ms"),
            "keyword_retrieval_ms": timing.get("keyword_retrieval_ms"),
            "vector_retrieval_ms": timing.get("vector_retrieval_ms"),
            "fusion_ms": timing.get("fusion_ms"),
            "retrieval_ms": result.latency_ms,
        },
        "chunk_ranking": chunk_ids,
        "context_trace": trace,
        "tokens": "NOT_MEASURED",
    }


def run_m3_abc(*, data_root: Path, output_dir: Path, preregistration_path: Path,
               cases_path: Path, admin_url: str,
               m1_m2_manifest_path: Path,
               expected_preregistration_sha256: str,
               execution_command: list[str]) -> dict[str, Any]:
    frozen = verify_frozen_sample(preregistration_path, cases_path, data_root,
                                  expected_preregistration_sha256)
    provenance = committed_source_provenance(Path(__file__).resolve().parents[1])
    if provenance != frozen.get("source_provenance"):
        raise ExperimentInvalidError("preregistered_source_provenance_mismatch")
    if _file_hash(m1_m2_manifest_path) != frozen["m1_m2_manifest_sha256"]:
        raise ExperimentInvalidError("historical_m1_m2_manifest_changed")
    historical = json.loads(m1_m2_manifest_path.read_text(encoding="utf-8"))
    expected_indices = historical.get("index_identities", {})
    if set(expected_indices) != set(DATASETS):
        raise ExperimentInvalidError("historical_index_identities_missing")
    clone_identity = _verify_clone_container(EXPECTED_CLONE, EXPECTED_CLONE_VOLUME)
    database_urls = _database_urls(admin_url, expected_indices)
    if output_dir.exists():
        raise ExperimentInvalidError("m3_output_directory_already_exists")
    output_dir.mkdir(parents=True, exist_ok=False)
    results_path = output_dir / "results.jsonl"
    _write_new(results_path, b"")
    manifest: dict[str, Any] = {
        "schema": "m3-random-development-abc-run-v1", "status": "RUNNING",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_mode": "DEVELOPMENT-EXPLORATORY-RETRIEVAL-ONLY",
        "execution_command": execution_command,
        "source_provenance": provenance,
        "preregistration_sha256": frozen["preregistration_sha256"],
        "cases_sha256": frozen["cases_sha256"],
        "qid_set_sha256": frozen["qid_set_sha256"],
        "qid_sets": frozen["qid_sets"],
        "dataset_versions": frozen["dataset_versions"],
        "clone_identity": clone_identity,
        "configs": frozen["configs"], "limits": frozen["limits"],
        "retrieval_attempts": 0, "completed_mode_rows": 0,
        "completed_qids": 0, "successful_query_embeddings": 0,
        "corpus_embeddings": 0, "new_indexes": 0, "downloads": 0,
        "generation_calls": 0, "judge_calls": 0, "locked_queries": 0,
        "model_tokens": "NOT_MEASURED", "database_writes": 0,
        "index_identities": {}, "query_embedding_cache": {},
        "results_path": str(results_path),
    }
    _write_json_atomic(output_dir / "manifest.json", manifest)
    attachments: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    retrieval_started: float | None = None
    last_progress = time.monotonic()
    failed = False
    try:
        # Verify every database/index/model identity before the first embedding call.
        for dataset in DATASETS:
            expected = expected_indices[dataset]
            attachment = attach_completed_index(
                data_root=data_root, dataset=dataset, database_url=database_urls[dataset],
                embedding_model=expected["model_digests"]["embedding"]["name"],
                statement_timeout_ms=STATEMENT_TIMEOUT_MS,
            )
            attachments[dataset] = attachment
            _check_expected_index(dataset, attachment, expected)
            hnsw = _verify_hnsw(attachment)
            manifest["index_identities"][dataset] = {
                **expected, "hnsw": hnsw,
                "readonly": True,
            }
        _write_json_atomic(output_dir / "manifest.json", manifest)

        for dataset in DATASETS:
            prepared = load_public_dataset(dataset, data_root, split="development", phase="optimize")
            selected_qids = frozen["qid_sets"][dataset]
            cases_by_qid = {case.qid: case for case in prepared.cases if case.qid in set(selected_qids)}
            if set(cases_by_qid) != set(selected_qids):
                raise ExperimentInvalidError("frozen_development_cases_missing")
            attachment = attachments[dataset]
            context_builder = ContextBuilder(BASE_CONFIG["context_budget_chars"])
            mode_names = tuple(MODES)
            cache_totals = {"cache_hits": 0, "cache_misses": 0,
                            "actual_embedding_calls": 0, "failed_embedding_calls": 0}
            for qid_index, qid in enumerate(selected_qids):
                case = cases_by_qid[qid]
                # Scope cache to this QID; successful embeddings can be reused only within A/B/C.
                cache = RunQueryEmbeddingCache(
                    attachment["gateway"],
                    model_digest=attachment["actual_models"]["embedding"]["digest"],
                    profile_id=attachment["embedding_profile_id"], dimensions=1024,
                    scope_identity=attachment["knowledge_base_id"],
                )
                rotation = qid_index % len(mode_names)
                ordered_modes = mode_names[rotation:] + mode_names[:rotation]
                for mode in ordered_modes:
                    now = time.monotonic()
                    if retrieval_started is None:
                        retrieval_started = now
                    if manifest["retrieval_attempts"] >= MAX_RETRIEVALS:
                        raise TimeoutError("retrieval_attempt_cap")
                    if now - retrieval_started >= MAX_RUNTIME_SECONDS:
                        raise TimeoutError("retrieval_hard_timeout")
                    if now - last_progress >= NO_PROGRESS_SECONDS:
                        raise TimeoutError("retrieval_no_progress_five_minutes")
                    spec = MODES[mode]
                    retriever = HybridRetriever(
                        attachment["repository"], embedding_provider=cache,
                        top_k=BASE_CONFIG["top_k"], candidate_k=BASE_CONFIG["candidate_k"],
                        rrf_k=BASE_CONFIG["rrf_k"], enabled_sources=spec["enabled_sources"],
                        source_weights=spec["source_weights"],
                    )
                    before_cache = cache.stats()
                    manifest["retrieval_attempts"] += 1
                    _write_json_atomic(output_dir / "manifest.json", manifest)
                    try:
                        retrieval_start = time.perf_counter()
                        result = retriever.retrieve(Scope.from_ids([attachment["knowledge_base_id"]]),
                                                    case.question)
                        retrieval_elapsed_ms = (time.perf_counter() - retrieval_start) * 1000
                        ranks = validate_retrieval_result(retriever, result, mode)
                        row = _score(mode, case, result, ranks, attachment, context_builder,
                                     dataset)
                        row["metrics"]["retrieval_ms"] = retrieval_elapsed_ms
                        row["dataset"] = dataset
                        row["execution_order"] = list(ordered_modes)
                        row["cache_state"] = _query_cache_state(before_cache, cache.stats())
                    except Exception as exc:
                        failed_cache = cache.stats()
                        cache_totals = {
                            key: cache_totals[key] + failed_cache[key] - before_cache[key]
                            for key in cache_totals
                        }
                        manifest["successful_query_embeddings"] += max(
                            0,
                            failed_cache["actual_embedding_calls"] - before_cache["actual_embedding_calls"]
                            - failed_cache["failed_embedding_calls"] + before_cache["failed_embedding_calls"],
                        )
                        _append_jsonl(results_path, {
                            "status": "INVALID", "dataset": dataset, "qid": qid,
                            "mode": mode, "invalid_code": getattr(exc, "code", None) or
                            (str(exc) if isinstance(exc, ExperimentInvalidError) else type(exc).__name__),
                            "execution_order": list(ordered_modes),
                        })
                        manifest["query_embedding_cache"][dataset] = cache_totals
                        manifest["status"] = "BLOCKED" if isinstance(exc, ExperimentInvalidError) else "INTERRUPTED"
                        failed = True
                        raise
                    _append_jsonl(results_path, row)
                    after_cache = cache.stats()
                    cache_totals = {key: cache_totals[key] + after_cache[key] - before_cache[key]
                                    for key in cache_totals}
                    manifest["successful_query_embeddings"] += after_cache["actual_embedding_calls"] - before_cache["actual_embedding_calls"]
                    if manifest["successful_query_embeddings"] > MAX_QUERY_EMBEDDINGS:
                        raise ExperimentInvalidError("query_embedding_cap_exceeded")
                    manifest["completed_mode_rows"] += 1
                    last_progress = time.monotonic()
                    manifest["query_embedding_cache"][dataset] = cache_totals
                    manifest["completed_qids"] = manifest["completed_mode_rows"] // 3
                    _write_json_atomic(output_dir / "manifest.json", manifest)
        manifest["status"] = "COMPLETED"
    except (Exception, KeyboardInterrupt) as exc:
        if not failed:
            manifest["status"] = "BLOCKED" if isinstance(exc, ExperimentInvalidError) else "INTERRUPTED"
            manifest["error_code"] = getattr(exc, "code", None) or type(exc).__name__
            failed = True
    finally:
        after_indices: dict[str, Any] = {}
        for dataset, attachment in attachments.items():
            try:
                # Same readonly index snapshot and identity check after retrieval.
                from eval_center.isolated_index import read_index_snapshot
                current = read_index_snapshot(
                    attachment["repository"], attachment["knowledge_base_id"],
                    attachment["bindings"], attachment["gateway"].embedding_model,
                )
                after_indices[dataset] = current["index_version"]
                if current["index_version"] != attachment["index"]["index_version"]:
                    manifest["status"] = "BLOCKED"
                    manifest["post_run_index_drift"] = dataset
            except Exception as exc:
                manifest["status"] = "BLOCKED"
                manifest["post_run_snapshot_error"] = type(exc).__name__
        manifest["post_run_index_versions"] = after_indices
        manifest["ended_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        manifest["retrieval_elapsed_seconds"] = round(time.monotonic() - retrieval_started, 3) if retrieval_started else 0
        for attachment in attachments.values():
            attachment["engine"].dispose()
        summary = summarize_results(results_path)
        manifest["summary"] = summary
        report_path = output_dir / "report.md"
        report_payload = render_report(manifest, summary).encode("utf-8")
        _write_new(report_path, report_payload)
        manifest["report_path"] = str(report_path)
        manifest["report_sha256"] = hashlib.sha256(report_payload).hexdigest()
        _write_json_atomic(output_dir / "manifest.json", manifest)
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description="Frozen M3 Development A/B/C retrieval review")
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    freeze.add_argument("--m1-m2-manifest", type=Path, required=True)
    freeze.add_argument("--output-dir", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    run.add_argument("--m1-m2-manifest", type=Path, required=True)
    run.add_argument("--preregistration", type=Path, required=True)
    run.add_argument("--cases", type=Path, required=True)
    run.add_argument("--expected-preregistration-sha256", required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--admin-env", default="RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL")
    args = parser.parse_args()
    if args.command == "freeze":
        print(json.dumps(freeze_preregistration(data_root=args.data_root,
                                                m1_m2_manifest=args.m1_m2_manifest,
                                                output_dir=args.output_dir),
                         ensure_ascii=False, sort_keys=True))
        return 0
    admin_url = os.environ.get(args.admin_env)
    if not admin_url:
        print("BLOCKED: clone_admin_url_missing", file=sys.stderr)
        return 2
    result = run_m3_abc(data_root=args.data_root, output_dir=args.output_dir,
                        preregistration_path=args.preregistration, cases_path=args.cases,
                        admin_url=admin_url, m1_m2_manifest_path=args.m1_m2_manifest,
                        expected_preregistration_sha256=args.expected_preregistration_sha256,
                        execution_command=[sys.executable, "-m", "eval_center.public_m3_abc",
                                           *sys.argv[1:]])
    print(json.dumps({"status": result["status"], "manifest": str(args.output_dir / "manifest.json"),
                      "retrieval_attempts": result["retrieval_attempts"],
                      "completed_mode_rows": result["completed_mode_rows"],
                      "completed_qids": result["completed_qids"]}, ensure_ascii=False))
    return 0 if result["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(_main())
