"""Bounded real retrieval comparison on approved development indexes only."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.adapters.models.usage import capture_usage
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.scope import Scope
from eval_center.metrics import ranking_metrics
from eval_center.public_data import DATASET_VERSIONS, fold_chunk_ranking, load_public_dataset
from eval_center.public_trace import build_stage_context_trace
from eval_center.query_cache import RunQueryEmbeddingCache
from eval_center.readonly_public_index import approved_run_directory, attach_completed_index
from eval_center.runtime import committed_source_provenance


MAX_QIDS = {"scifact": 24, "miracl-zh": 24, "longbench-zh": 12}
BASE_CONFIG = {"top_k": 5, "candidate_k": 32, "rrf_k": 60,
               "chunk_size": 1200, "chunk_overlap": 120,
               "context_budget_chars": 8000}
MODES = {
    "A-vector-only": {"enabled_sources": ("vector",), "source_weights": {}},
    "B-hybrid-rrf": {"enabled_sources": ("keyword", "vector"), "source_weights": {}},
    "C-vector-priority-0.8-0.2": {"enabled_sources": ("keyword", "vector"),
                                   "source_weights": {"keyword": 0.2, "vector": 0.8}},
}
KNOWN_CASES = {
    "scifact": ("1036", "1040", "1234", "752", "710"),
    "miracl-zh": ("1022676#0", "855039#0", "1059962#0", "1099346#0", "1205173#0"),
    "longbench-zh": (
        "multifieldqa_zh:87bc3d04c6f924a4b27560e78f8b446e19ec779ffaba1524",
        "multifieldqa_zh:198b2a1122828dd5539b9c9baf7b36849ecdeff4f804b2e6",
        "multifieldqa_zh:58fb54927d4416aabbbe68479eae60d25cac42c9cc7ce869",
        "dureader:91b4e9d1a4d0afdc7c5503c767797fd950169b9b38851c88",
    ),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _strata_from_completed_dev(dataset: str, data_root: Path) -> dict[str, str]:
    source = approved_run_directory(data_root, dataset) / "cases.jsonl"
    labels: dict[str, str] = {}
    with source.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            base = row["variants"]["baseline-t5-c32-r60"]
            positive = {doc for doc, grade in row["qrels"].items() if grade > 0}
            docs = base["document_rankings"]
            vector = docs.get("vector", [])[:10]
            hybrid = docs["hybrid"][:10]
            hybrid_full = set(docs["hybrid"])
            context = set(base["context_document_rankings"]["hybrid"])
            vector_best = min((vector.index(doc) for doc in positive if doc in vector), default=10000)
            hybrid_best = min((hybrid.index(doc) for doc in positive if doc in hybrid), default=10000)
            if vector_best < hybrid_best:
                label = "fusion_demoted"
            elif any(doc in hybrid_full and doc not in context for doc in positive):
                label = "context_missed"
            else:
                label = "ordinary"
            labels[row["qid"]] = label
    return labels


def fixed_cases(dataset: str, data_root: Path) -> tuple[tuple[Any, ...], dict[str, str]]:
    data = load_public_dataset(dataset, data_root, split="development", phase="optimize")
    by_qid = {case.qid: case for case in data.cases}
    labels = _strata_from_completed_dev(dataset, data_root)
    if set(labels) != set(by_qid):
        raise ValueError("completed development qid identity mismatch")
    selected: list[Any] = [by_qid[qid] for qid in KNOWN_CASES[dataset] if qid in by_qid]
    selected_ids = {case.qid for case in selected}
    quotas = {"fusion_demoted": 6, "context_missed": 6} if dataset != "longbench-zh" else {
        "fusion_demoted": 2, "context_missed": 2,
    }
    for group in ("fusion_demoted", "context_missed", "ordinary"):
        remaining = [case for case in data.cases if case.qid not in selected_ids and labels[case.qid] == group]
        remaining.sort(key=lambda case: hashlib.sha256(
            f"m1-m2-subset-v1\0{dataset}\0{case.qid}".encode("utf-8")
        ).hexdigest())
        needed = min(MAX_QIDS[dataset] - len(selected), quotas.get(group, MAX_QIDS[dataset]))
        chosen = remaining[:needed]
        selected.extend(chosen)
        selected_ids.update(case.qid for case in chosen)
    if len(selected) != MAX_QIDS[dataset] or len(selected_ids) != len(selected):
        raise ValueError("development subset incomplete")
    return tuple(selected), {case.qid: labels[case.qid] for case in selected}


def _stage_metrics(result, trace, index, qrels, ranking=None):
    spans = index["spans"]
    # Vector-only must be scored from its actual vector candidate ordering;
    # using the result's fused list here would silently turn A into B.
    ranking = result.fused_ranking if ranking is None else ranking
    fused = [hit.chunk_id for hit in ranking]
    docs = fold_chunk_ranking(fused, {chunk_id: span.document_id for chunk_id, span in spans.items()})
    context_docs = trace["selected_source_ids"]
    metrics = {}
    for k in (5, 10):
        rank = ranking_metrics(docs, qrels, k=k)
        ctx = ranking_metrics(context_docs, qrels, k=k)
        metrics[f"document_metrics_at_{k}"] = rank
        metrics[f"context_metrics_at_{k}"] = ctx
    return metrics, docs, context_docs


def run_m1_m2(*, data_root: Path, database_urls: dict[str, str], output_root: Path,
              hard_timeout_seconds: int = 20 * 60) -> dict[str, Any]:
    started = time.monotonic()
    source_provenance = committed_source_provenance(Path(__file__).resolve().parents[1])
    output_dir = output_root / "m1-m2" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=False)
    datasets = {}
    all_qids: dict[str, list[str]] = {}
    sample_strata = {}
    for dataset in ("scifact", "miracl-zh", "longbench-zh"):
        cases, strata = fixed_cases(dataset, data_root)
        all_qids[dataset] = [case.qid for case in cases]
        sample_strata[dataset] = strata
        datasets[dataset] = {"cases": cases, "attachment": None}
    if sum(map(len, all_qids.values())) > 60 or len(MODES) > 3:
        raise ValueError("m2 experiment cap exceeded")
    manifest = {
        "status": "RUNNING", "evaluation_mode": "DEVELOPMENT-DIAGNOSTIC-SUBSET",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": source_provenance["git_sha"],
        "source_provenance": source_provenance,
        "dataset_versions": DATASET_VERSIONS,
        "qid_sets": all_qids, "qid_set_hash": _hash(all_qids),
        "sample_strata": sample_strata,
        "configs": {name: {**BASE_CONFIG, "enabled_sources": list(spec["enabled_sources"]),
                           "source_weights": spec["source_weights"]}
                    for name, spec in MODES.items()},
        "limits": {"max_qids": 60, "max_retrievals": 180, "hard_timeout_seconds": hard_timeout_seconds},
        "corpus_embeddings": 0, "new_indexes": 0, "qwen_calls": 0, "judge_calls": 0,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cases_path = output_dir / "cases.jsonl"
    retrieval_count = 0
    completed_qids = 0
    cache_stats: dict[str, dict[str, int]] = {}
    retrieval_started_at: float | None = None
    last_progress = time.monotonic()
    try:
        for dataset, state in datasets.items():
            attachment = attach_completed_index(data_root=data_root, dataset=dataset,
                                                database_url=database_urls[dataset],
                                                embedding_model="bge-m3:latest")
            state["attachment"] = attachment
            manifest.setdefault("index_identities", {})[dataset] = {
                "database_name": attachment["database_name"], "schema_revision": attachment["schema_revision"],
                "index_version": attachment["index"]["index_version"],
                "index_counts": attachment["index"]["index_counts"],
                "model_digests": attachment["actual_models"],
            }
            cache = RunQueryEmbeddingCache(
                attachment["gateway"], model_digest=attachment["actual_models"]["embedding"]["digest"],
                profile_id=attachment["embedding_profile_id"], dimensions=1024,
                scope_identity=attachment["knowledge_base_id"],
            )
            context_builder = ContextBuilder(BASE_CONFIG["context_budget_chars"])
            cache_stats[dataset] = cache.stats()
            for case in state["cases"]:
                row = {"dataset": dataset, "qid": case.qid, "qrels_kind": case.qrels_kind,
                       "qrels_count": len(case.qrels), "modes": {}}
                for mode_name, spec in MODES.items():
                    if retrieval_started_at is None:
                        retrieval_started_at = time.monotonic()
                    if retrieval_count >= 180 or time.monotonic() - retrieval_started_at > hard_timeout_seconds:
                        raise TimeoutError("m2_hard_timeout")
                    if time.monotonic() - last_progress > 5 * 60:
                        raise TimeoutError("m2_no_progress_five_minutes")
                    retriever = HybridRetriever(
                        attachment["repository"], embedding_provider=cache,
                        top_k=BASE_CONFIG["top_k"], candidate_k=BASE_CONFIG["candidate_k"],
                        rrf_k=BASE_CONFIG["rrf_k"], enabled_sources=spec["enabled_sources"],
                        source_weights=spec["source_weights"],
                    )
                    with capture_usage() as usage:
                        retrieval_started = time.perf_counter()
                        result = retriever.retrieve(Scope.from_ids([attachment["knowledge_base_id"]]), case.question)
                        retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
                    ranks = result.fused_ranking if mode_name != "A-vector-only" else result.candidate_rankings.get("vector", ())
                    context, trace = build_stage_context_trace(
                        case.qid, mode_name, ranks, attachment["index"], context_builder,
                        top_k=BASE_CONFIG["top_k"], effective_config={**BASE_CONFIG, **spec},
                    )
                    metrics, docs, context_docs = _stage_metrics(
                        result, trace, attachment["index"], case.qrels, ranking=ranks
                    )
                    row["modes"][mode_name] = {
                        "retrieval_ms": retrieval_ms, "metrics": metrics,
                        "chunk_rankings": {name: [hit.chunk_id for hit in hits]
                                           for name, hits in result.candidate_rankings.items()},
                        "fused_ranking": [hit.chunk_id for hit in result.fused_ranking],
                        "document_ranking": docs, "context_document_ranking": context_docs,
                        "context_trace": trace, "model_usage": usage.to_dict(),
                    }
                    retrieval_count += 1
                    last_progress = time.monotonic()
                with cases_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                completed_qids += 1
                manifest["completed_qids"] = completed_qids
                manifest["retrieval_calls"] = retrieval_count
                cache_stats[dataset] = cache.stats()
                manifest["query_embedding_cache"] = cache_stats
                (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["status"] = "COMPLETED"
    except (Exception, KeyboardInterrupt) as exc:
        manifest["status"] = "INTERRUPTED"
        manifest["error_code"] = type(exc).__name__
        manifest["error_detail"] = str(exc)[:160] if isinstance(exc, TimeoutError) else None
        raise
    finally:
        for state in datasets.values():
            attachment = state["attachment"]
            if attachment is not None:
                attachment["engine"].dispose()
        manifest["ended_at"] = datetime.now(timezone.utc).isoformat()
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        manifest["retrieval_calls"] = retrieval_count
        manifest["completed_qids"] = completed_qids
        manifest["query_embedding_cache"] = cache_stats
        manifest["retrieval_elapsed_seconds"] = round(time.monotonic() - retrieval_started_at, 3) if retrieval_started_at else 0
        manifest["status"] = manifest.get("status", "INTERRUPTED")
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": manifest["status"], "run_dir": str(output_dir),
            "retrieval_calls": retrieval_count, "completed_qids": completed_qids,
            "elapsed_seconds": manifest["elapsed_seconds"], "manifest": manifest}
