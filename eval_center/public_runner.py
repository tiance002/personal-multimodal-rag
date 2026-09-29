"""Real isolated runs for the public benchmark dataset adapters."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from backend.app.adapters.models.usage import capture_usage
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.knowledge_gateway import EvidenceService, KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever, RetrievalItem
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.scope import Scope
from backend.app.domain.evidence import EvidenceResolver
from backend.app.workers.ingestion import run_once
from eval_center.isolated_index import create_isolated_database, read_index_snapshot
from eval_center.metrics import aggregate_metrics, ranking_metrics
from eval_center.public_data import PublicCase, PublicDataset, SOURCE_URLS, fold_chunk_ranking, load_public_dataset
from eval_center.public_trace import build_stage_context_trace
from eval_center.query_cache import RunQueryEmbeddingCache
from eval_center.runtime import committed_code_sha, effective_configuration, model_identities
from eval_center.verification import ExperimentInvalidError


RETRIEVAL_MODES = ("keyword", "vector", "hybrid")


class RecordingRetriever(HybridRetriever):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def retrieve(self, *args, **kwargs):
        result = super().retrieve(*args, **kwargs)
        self.records.append(result)
        return result


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_answer(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _character_f1(prediction: str, reference: str) -> float:
    from collections import Counter
    predicted = Counter(_normalize_answer(prediction))
    expected = Counter(_normalize_answer(reference))
    if not predicted or not expected:
        return float(predicted == expected)
    overlap = sum((predicted & expected).values())
    precision, recall = overlap / sum(predicted.values()), overlap / sum(expected.values())
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _answer_metrics(answer: str, references: tuple[str, ...]) -> dict[str, Any]:
    normalized = _normalize_answer(answer)
    normalized_references = [_normalize_answer(reference) for reference in references]
    exact = max((int(normalized == reference) for reference in normalized_references), default=None)
    char_f1 = max((_character_f1(answer, reference) for reference in references), default=None)
    return {"normalized_exact_match": exact, "character_f1": char_f1,
            "references_count": len(references), "judge": "NOT_EVALUATED",
            "citation_support": "NOT_EVALUATED"}


def _readback_citations(answer_result, repository, scope) -> dict[str, bool]:
    snapshots = {snapshot.label: snapshot for snapshot in answer_result.evidence}

    def read_quote(version_id: str, chunk_id: str | None) -> str:
        chunk = repository.get_chunk(chunk_id or "")
        if chunk is None or chunk.version_id != version_id:
            raise ValueError("citation source is unreadable")
        scope.assert_contains(chunk.knowledge_base_id, chunk.document_id)
        return chunk.content

    resolver = EvidenceResolver(snapshots, read_quote)
    readbacks = {}
    for label in answer_result.citations:
        try:
            resolver.resolve(label)
            readbacks[label] = True
        except Exception:
            readbacks[label] = False
    return readbacks


def _retrieval_modes(result) -> dict[str, list[str]]:
    modes = {
        "keyword": [hit.chunk_id for hit in result.candidate_rankings.get("keyword", ())],
        "vector": [hit.chunk_id for hit in result.candidate_rankings.get("vector", ())],
        "hybrid": [hit.chunk_id for hit in result.fused_ranking],
    }
    return {name: ranking for name, ranking in modes.items() if ranking or name in result.candidate_rankings}


def _items_for_rank(repository, chunk_ids: list[str], hits: dict[str, Any], top_k: int) -> list[RetrievalItem]:
    output = []
    for chunk_id in chunk_ids[:top_k]:
        chunk = repository.get_chunk(chunk_id)
        if chunk is None:
            raise ExperimentInvalidError("ranked_chunk_not_readable")
        output.append(RetrievalItem(chunk=chunk, hit=hits[chunk_id]))
    return output


def _case_row(
    case: PublicCase,
    result,
    container,
    index: dict[str, Any],
    context_builder: ContextBuilder,
    config: dict[str, Any],
    tokenizer: Any | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    chunk_to_document = {chunk_id: span.document_id for chunk_id, span in index["spans"].items()}
    stage_chunk_ranks = _retrieval_modes(result)
    mode_documents: dict[str, list[str]] = {}
    mode_context_documents: dict[str, list[str]] = {}
    mode_metrics: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    context_traces: dict[str, dict[str, Any]] = {}
    context_times: dict[str, float] = {}
    for mode, chunk_ids in stage_chunk_ranks.items():
        document_ranks = fold_chunk_ranking(chunk_ids, chunk_to_document)
        mode_documents[mode] = document_ranks
        context_started = time.perf_counter()
        hits = result.fused_ranking if mode == "hybrid" else result.candidate_rankings[mode]
        stage_context, trace = build_stage_context_trace(
            case.qid, mode, hits, index, context_builder,
            top_k=config["top_k"], effective_config=config,
        )
        context_times[mode] = (time.perf_counter() - context_started) * 1000
        selected_chunk_ids = stage_context["selected_chunk_ids"]
        context_docs = fold_chunk_ranking(selected_chunk_ids, chunk_to_document)
        if context_docs != stage_context["selected_document_ids"]:
            raise ExperimentInvalidError("context_source_mapping_mismatch")
        mode_context_documents[mode] = context_docs
        context_traces[mode] = trace
        mode_metrics[mode] = {}
        for k in sorted(set((3, 5, 8, 10, 20, config["top_k"]))):
            mode_metrics[mode][f"document_metrics_at_{k}"] = ranking_metrics(document_ranks, case.qrels, k=k)
            mode_metrics[mode][f"context_document_metrics_at_{k}"] = ranking_metrics(context_docs, case.qrels, k=k)
        judged = set(case.qrels)
        mode_metrics[mode]["judgment_coverage"] = {
            "judged_pool_documents": len(judged),
            "unjudged_pool_documents": len(index["document_ids"] - judged),
            "unjudged_pool_fraction": len(index["document_ids"] - judged) / len(index["document_ids"]),
            "retrieved_unjudged_documents_at_k": sum(doc_id not in judged for doc_id in document_ranks[:config["top_k"]]),
            "qrels_kind": case.qrels_kind,
        }
        contexts[mode] = stage_context
    metric_row: dict[str, Any] = {}
    for mode, stages in mode_metrics.items():
        for metric_set, values in stages.items():
            for metric_name, value in values.items():
                if isinstance(value, (int, float)) and value is not None:
                    metric_row[f"{mode}.{metric_set}.{metric_name}"] = value
    timing = result.stage_latency_ms
    metrics_by_mode: dict[str, dict[str, Any]] = {}
    for mode in mode_metrics:
        selected_metrics = {key: value for key, value in metric_row.items() if key.startswith(mode + ".")}
        selected_metrics.update({
            "keyword_retrieval_ms": timing.get("keyword_retrieval_ms"),
            "vector_retrieval_ms": timing.get("vector_retrieval_ms"),
            "embedding_ms": timing.get("embedding_ms"),
            "fusion_ms": timing.get("fusion_ms") if mode == "hybrid" else None,
            "context_building_ms": context_times[mode],
            "retrieval_ms": result.latency_ms,
        })
        metrics_by_mode[mode] = selected_metrics
    estimated_tokens = {}
    for mode, value in contexts.items():
        estimated_tokens[mode] = len(tokenizer.encode(value["text"])) if tokenizer is not None else None
    row = {
        "qid": case.qid,
        "split": case.split,
        "question": case.question,
        "qrels": case.qrels,
        "qrels_kind": case.qrels_kind,
        "qrels_count": len(case.qrels),
        "positive_qrels_count": sum(grade > 0 for grade in case.qrels.values()),
        "explicit_zero_qrels_count": sum(grade == 0 for grade in case.qrels.values()),
        "paired_source_doc_id": case.paired_source_doc_id,
        "chunk_rankings": stage_chunk_ranks,
        "document_rankings": mode_documents,
        "context_document_rankings": mode_context_documents,
        "context": contexts,
        "context_trace": context_traces,
        "estimated_context_tokens": estimated_tokens,
        "metrics": metrics_by_mode,
        "timings_ms": timing,
        "query_mode": "q0",
        "generation": "NOT_RUN",
    }
    return row, metrics_by_mode


def _source_content(document) -> str:
    title = document.title.strip()
    return f"# {title}\n\n{document.text}" if title else document.text


def _expected_normalized_text(content: str) -> str:
    """Mirror TextParser's newline normalization for the verified index binding."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def run_public_retrieval(
    *,
    repository_root: Path,
    data_root: Path,
    dataset_name: str,
    admin_url: str,
    output_root: Path | None = None,
    split: str = "development",
    phase: str = "baseline",
    profile: str = "standard",
    chunk_size: int = 1200,
    chunk_overlap: int = 120,
    top_k: int = 5,
    candidate_k: int = 32,
    rrf_k: int = 60,
    context_budget_chars: int = 8000,
    max_cases: int | None = None,
    generation_limit: int = 0,
    retrieval_variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ingest and retrieve through production components in a fresh local DB."""
    if profile not in {"smoke", "standard"}:
        raise ValueError("unsupported run profile")
    for value in (chunk_size, top_k, candidate_k, rrf_k, context_budget_chars):
        if type(value) is not int or value <= 0:
            raise ValueError("invalid run configuration")
    if type(chunk_overlap) is not int or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("invalid chunk overlap")
    if type(generation_limit) is not int or generation_limit < 0:
        raise ValueError("invalid generation limit")
    if generation_limit and dataset_name != "longbench-zh":
        raise ValueError("public Qwen QA is limited to LongBench Chinese")
    if split == "locked_holdout" and (phase != "validate" or profile != "standard" or max_cases is not None or generation_limit):
        raise ValueError("locked validation requires one complete standard retrieval run")
    if retrieval_variants is None:
        retrieval_variants = [{"variant_id": "baseline", "top_k": top_k,
                               "candidate_k": candidate_k, "rrf_k": rrf_k}]
    if not retrieval_variants or len({item.get("variant_id") for item in retrieval_variants}) != len(retrieval_variants):
        raise ValueError("invalid or duplicate retrieval variant")
    for item in retrieval_variants:
        if set(item) != {"variant_id", "top_k", "candidate_k", "rrf_k"}:
            raise ValueError("retrieval variant fields are invalid")
        if not isinstance(item["variant_id"], str) or not item["variant_id"]:
            raise ValueError("retrieval variant identity is invalid")
        if any(type(item[field]) is not int or item[field] <= 0 for field in ("top_k", "candidate_k", "rrf_k")):
            raise ValueError("retrieval variant values are invalid")
    code_sha = committed_code_sha(repository_root)
    dataset = load_public_dataset(dataset_name, data_root, split=split, phase=phase)
    validation_lock_path = Path(data_root) / "runs" / "locks" / f"{dataset_name}-locked-holdout.json"
    if split == "locked_holdout" and validation_lock_path.exists():
        raise ExperimentInvalidError("locked_split_already_used")
    documents = dataset.documents
    cases = dataset.cases
    if profile == "smoke":
        selected_cases = []
        requested_ids: set[str] = set()
        case_limit = min(len(cases), max_cases or 12)
        for case in cases:
            additions = set(case.qrels)
            if case.paired_source_doc_id is not None:
                additions.add(case.paired_source_doc_id)
            if len(requested_ids | additions) > 50:
                continue
            selected_cases.append(case)
            requested_ids.update(additions)
            if len(selected_cases) >= case_limit:
                break
        documents = tuple(document for document in documents if document.doc_id in requested_ids)
        remaining = max(0, 50 - len(documents))
        selected_ids = {document.doc_id for document in documents}
        documents += tuple(document for document in dataset.documents if document.doc_id not in selected_ids)[:remaining]
        present = {document.doc_id for document in documents}
        cases = tuple(case for case in selected_cases if set(case.qrels) <= present and
                      (case.paired_source_doc_id is None or case.paired_source_doc_id in present))
    elif max_cases is not None:
        cases = cases[:max_cases]
    if not cases or not documents:
        raise ExperimentInvalidError("empty_public_run_scope")
    token = uuid.uuid4().hex[:10]
    experiment_id = str(uuid.uuid4())
    db_name = f"rag_eval_trust_{token}_{dataset_name.replace('-', '_')[:10]}"
    database_url = create_isolated_database(admin_url, db_name)
    output_root = Path(output_root or (Path(data_root) / "runs"))
    output_dir = output_root / dataset_name / split / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{token}"
    env = {**os.environ, "RAG_DATABASE_URL": database_url, "RAG_CLOUD_ENABLED": "false", "RAG_LANGFUSE_ENABLED": "false"}
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(repository_root / "alembic.ini"), "upgrade", "head"],
                   cwd=repository_root, env=env, check=True)
    settings = Settings(database_url=database_url, storage_root=output_dir / "storage", cloud_enabled=False,
                        langfuse_enabled=False, inline_ingestion_enabled=False, max_chunk_chars=chunk_size,
                        chunk_overlap=chunk_overlap)
    container = build_container(settings, agent_model=None)
    try:
        models = model_identities(container.ollama)
        kb_id = container.store.create_knowledge_base(f"public-{token}", cloud_allowed=False)["id"]
        bindings: dict[str, dict[str, str]] = {}
        document_external_ids: set[str] = set()
        started_at = datetime.now(timezone.utc).isoformat()
        ingest_started = time.perf_counter()
        for number, document in enumerate(documents, start=1):
            content = _source_content(document)
            payload = content.encode("utf-8")
            stored = container.storage.put_stream(BytesIO(payload))
            file_suffix = hashlib.sha256(document.doc_id.encode("utf-8")).hexdigest()[:24]
            receipt = container.store.create_upload(kb_id, f"{file_suffix}.md", "text/markdown", stored)
            bindings[receipt["document_id"]] = {"document_id": document.doc_id,
                                                 "source_version": hashlib.sha256(payload).hexdigest(),
                                                 "text": _expected_normalized_text(content)}
            document_external_ids.add(document.doc_id)
            if number % 250 == 0:
                print(json.dumps({"event": "ingestion_queued", "dataset": dataset_name, "documents": number}), flush=True)
        worker_id = f"public-{token}"
        for number in range(len(documents)):
            job = run_once(container.store, worker_id=worker_id)
            if job is None or job.get("status") != "succeeded":
                raise ExperimentInvalidError("real_public_ingestion_failed")
            if (number + 1) % 250 == 0:
                print(json.dumps({"event": "ingestion_completed", "dataset": dataset_name,
                                  "documents": number + 1}), flush=True)
        ingestion_ms = (time.perf_counter() - ingest_started) * 1000
        index = read_index_snapshot(container.store, kb_id, bindings, container.ollama.embedding_model)
        if index["index_counts"]["documents"] != len(documents) or index["index_counts"]["embeddings"] != index["index_counts"]["chunks"]:
            raise ExperimentInvalidError("public_index_count_mismatch")
        index["document_ids"] = document_external_ids
        embedding_profile_id = container.store.get_embedding_profile_id(container.ollama.embedding_model, 1024)
        if embedding_profile_id is None:
            raise ExperimentInvalidError("embedding_profile_missing")
        query_cache = RunQueryEmbeddingCache(
            container.ollama, model_digest=models["embedding"]["digest"],
            profile_id=embedding_profile_id, dimensions=1024, scope_identity=kb_id,
        )
        context_builder = ContextBuilder(context_budget_chars)
        retrievers = {}
        gateways = {}
        actual_configs = {}
        for variant in retrieval_variants:
            variant_id = variant["variant_id"]
            retriever = RecordingRetriever(container.store, embedding_provider=query_cache,
                top_k=variant["top_k"], candidate_k=variant["candidate_k"], rrf_k=variant["rrf_k"])
            retrievers[variant_id] = retriever
            actual_configs[variant_id] = effective_configuration(retriever, container.store, context_builder,
                declared={"top_k": variant["top_k"], "candidate_k": variant["candidate_k"], "rrf_k": variant["rrf_k"],
                          "chunk_size": chunk_size, "chunk_overlap": chunk_overlap,
                          "context_budget_chars": context_budget_chars})
            gateways[variant_id] = KnowledgeGateway(
                retriever, evidence_service=EvidenceService(context_builder=context_builder))
        base_variant = retrieval_variants[0]["variant_id"]
        retriever = retrievers[base_variant]
        gateway = gateways[base_variant]
        scope = Scope.from_ids([kb_id])
        if generation_limit:
            warmup_error = None
            with capture_usage() as warmup_usage:
                try:
                    container.ollama.answer("Reply READY.", 120)
                except Exception as exc:
                    warmup_error = type(exc).__name__
        else:
            warmup_usage = None
            warmup_error = None
        try:
            import tiktoken
            tokenizer = tiktoken.get_encoding("cl100k_base")
        except (ImportError, OSError):
            tokenizer = None
        rows: list[dict[str, Any]] = []
        per_variant_metrics: dict[str, dict[str, list[dict[str, Any]]]] = {
            variant["variant_id"]: {mode: [] for mode in RETRIEVAL_MODES} for variant in retrieval_variants}
        if split == "locked_holdout":
            validation_lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_record = {"status": "started", "dataset": dataset_name,
                           "dataset_version": dataset.dataset_version, "run_id": token,
                           "git_sha": code_sha, "candidate_config": actual_configs[base_variant],
                           "query_set_hash": _canonical_hash([case.qid for case in cases]),
                           "started_at": datetime.now(timezone.utc).isoformat()}
            try:
                with validation_lock_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(lock_record, sort_keys=True, indent=2) + "\n")
            except FileExistsError as exc:
                raise ExperimentInvalidError("locked_split_already_used") from exc
        for number, case in enumerate(cases, start=1):
            variant_rows = {}
            metric_for_case = {}
            for variant in retrieval_variants:
                variant_id = variant["variant_id"]
                current_gateway = gateways[variant_id]
                started = time.perf_counter()
                with capture_usage() as usage:
                    result = current_gateway.retrieve_query(scope, case.question)
                variant_row, variant_metrics = _case_row(case, result, container, index, context_builder,
                                                          actual_configs[variant_id], tokenizer)
                variant_row["timings_ms"]["retrieval_context_ms"] = (time.perf_counter() - started) * 1000
                variant_row["model_usage"] = usage.to_dict()
                variant_rows[variant_id] = variant_row
                metric_for_case[variant_id] = variant_metrics
                for mode, metric in variant_metrics.items():
                    per_variant_metrics[variant_id][mode].append(metric)
            row = variant_rows[base_variant]
            row["variants"] = {variant_id: {
                "effective_config": actual_configs[variant_id],
                "document_rankings": detail["document_rankings"],
                "chunk_rankings": detail["chunk_rankings"],
                "context_document_rankings": detail["context_document_rankings"],
                "context_trace": detail["context_trace"],
                "metrics": detail["metrics"],
                "timings_ms": detail["timings_ms"],
                "estimated_context_tokens": detail["estimated_context_tokens"],
                "model_usage": detail["model_usage"],
            } for variant_id, detail in variant_rows.items()}
            if generation_limit and number <= generation_limit:
                retriever = retrievers[base_variant]
                retriever.records.clear()
                qa_started = time.perf_counter()
                with capture_usage() as answer_usage:
                    answer_result = LangChainQuickChain(gateways[base_variant], answer_gateway=container.ollama).invoke(
                        case.question, scope,
                        settings=QuickSettings(cloud_enabled=False, local_query_enabled=False,
                                               answer_timeout_seconds=120),
                        cloud_allowed_by_kb={kb_id: False},
                    )
                readbacks = _readback_citations(answer_result, container.store, scope)
                row["qa"] = {
                    "answer": answer_result.answer,
                    "error_code": answer_result.error_code,
                    "citations": [snapshot.model_dump() for snapshot in answer_result.evidence],
                    "citation_readbacks": readbacks,
                    "citation_readback_rate": (sum(readbacks.values()) / len(readbacks)) if readbacks else None,
                    "answer_metrics": _answer_metrics(answer_result.answer, case.answers),
                    "model_usage": answer_usage.to_dict(),
                    "retrieval_attempts": [{"latency_ms": attempt.latency_ms,
                                            "degradation_flags": list(attempt.degradation_flags),
                                            "returned_chunk_ids": [item.chunk.chunk_id for item in attempt.items]}
                                           for attempt in retriever.records],
                    "generation_latency_ms": sum(call.latency_ms for call in answer_usage.calls
                                                  if call.stage == "answer") or None,
                    "qa_latency_ms": (time.perf_counter() - qa_started) * 1000,
                    "context_tokens_estimated": (len(tokenizer.encode("\n".join(snapshot.quote for snapshot in answer_result.evidence)))
                                                  if tokenizer is not None and answer_result.evidence else None),
                    "citation_support": "NOT_EVALUATED",
                    "judge": "NOT_EVALUATED",
                }
                row["generation"] = "real_qwen" if any(call.stage == "answer" and call.status == "ok"
                                                         for call in answer_usage.calls) else "attempted_no_successful_answer_call"
            rows.append(row)
            if number % 25 == 0:
                print(json.dumps({"event": "queries_completed", "dataset": dataset_name,
                                  "split": split, "queries": number, "total": len(cases)}), flush=True)
        if model_identities(container.ollama) != models:
            raise ExperimentInvalidError("model_identity_changed_during_run")
        if committed_code_sha(repository_root) != code_sha:
            raise ExperimentInvalidError("code_changed_during_run")
        if read_index_snapshot(container.store, kb_id, bindings, container.ollama.embedding_model)["index_version"] != index["index_version"]:
            raise ExperimentInvalidError("index_changed_during_run")
        effective_qrels = {case.qid: case.qrels for case in cases}
        runtime_manifest = {
            "experiment_id": experiment_id,
            "git_sha": code_sha,
            "dataset": dataset_name,
            "dataset_version": dataset.dataset_version,
            "phase": phase,
            "source_urls": list(SOURCE_URLS[dataset_name]),
            "source_files": dataset.manifest["source_files"],
            "split": split,
            "profile": profile,
            "locked_split_scored": split == "locked_holdout",
            "corpus_hash": next(record["sha256"] for record in dataset.manifest["files"] if record["path"] == "corpus.jsonl"),
            "qrels_hash": _canonical_hash(effective_qrels),
            "query_set_hash": _canonical_hash([case.qid for case in cases]),
            "prepared_manifest_hash": _sha256((Path(data_root) / "prepared" / "adapters" / dataset_name / "manifest.json").read_bytes()),
            "models": models,
            "embedding_dimension": 1024,
            "effective_config": actual_configs[base_variant],
            "effective_configs": actual_configs,
            "retrieval_mode_ablation": "keyword/vector rankings are the real candidate sets from one production hybrid call; hybrid uses the production fused ranking. ContextBuilder is applied independently to each stage ranking.",
            "qrels_kind": sorted({case.qrels_kind for case in cases}),
            "index_version": index["index_version"],
            "index_counts": index["index_counts"],
            "sample_count": len(rows),
            "generation_case_count": min(generation_limit, len(rows)),
            "warmup_usage": warmup_usage.to_dict() if warmup_usage is not None else "NOT_RUN",
            "warmup_error": warmup_error,
            "query_embedding_cache": query_cache.stats(),
            "environment": {"os_family": platform.system().lower(), "python_version": platform.python_version(),
                            "architecture": platform.machine()},
            "evaluation_mode": "real_postgres_local_production_ingestion_retrieval",
            "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
        summary = {variant_id: {mode: aggregate_metrics(metrics) for mode, metrics in stages.items() if metrics}
                   for variant_id, stages in per_variant_metrics.items()}
        report = {"manifest": runtime_manifest, "metrics": summary, "cases": rows,
                  "database_name": db_name, "knowledge_base_id": kb_id,
                  "ingestion_ms": ingestion_ms, "chunk_to_document_count": len(index["spans"]),
                  "tokenizer": "cl100k_base (estimated for Qwen)" if tokenizer is not None else "unavailable"}
        runtime_manifest["run_hash"] = _canonical_hash({"manifest": runtime_manifest, "metrics": summary})
        _write(output_dir / "manifest.json", runtime_manifest)
        _write(output_dir / "metrics.json", summary)
        _write(output_dir / "report.json", report)
        with (output_dir / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        md = [f"# {dataset_name} {split} {profile} run", "", f"Code SHA: `{code_sha}`",
              f"Dataset version: `{dataset.dataset_version}`", f"Documents: {len(documents)}",
              f"Queries: {len(cases)}", f"Index counts: {index['index_counts']}",
              "", "Span-level recall: NOT_AVAILABLE for these document-level qrels.",
              "LongBench paired-context relevance is a derived diagnostic, not official retrieval qrels."]
        (output_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        if split == "locked_holdout":
            lock_record.update(status="completed", run_dir=str(output_dir.resolve()),
                               completed_at=datetime.now(timezone.utc).isoformat(),
                               run_manifest_sha256=_sha256((output_dir / "manifest.json").read_bytes()))
            _write(validation_lock_path, lock_record)
        return {"status": "REAL_PUBLIC_RETRIEVAL_COMPLETED", "dataset": dataset_name,
                "split": split, "profile": profile, "run_dir": str(output_dir),
                "git_sha": code_sha, "documents": len(documents), "queries": len(cases),
                "index_counts": index["index_counts"], "metrics": summary}
    finally:
        container.engine.dispose()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
