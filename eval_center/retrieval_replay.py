"""Frozen Development replay against independently verified read-only indexes."""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import HybridRetriever, RetrievalItem
from backend.app.config import Settings
from backend.app.domain.scope import Scope
from eval_center.development_data import DevelopmentDataset, load_development_dataset
from eval_center.isolated_index import read_index_snapshot
from eval_center.metrics import aggregate_metrics, ranking_metrics
from eval_center.public_data import PublicCase, PublicDocument, fold_chunk_ranking
from eval_center.query_embedding_cache import EmbeddingCacheIdentity, RunScopedQueryEmbeddingCache
from eval_center.runtime import committed_code_sha, model_identities
from eval_center.verified_index import (
    ALLOWED_DATABASES,
    CLONE_CONTAINER_NAME,
    CLONE_PORT,
    CLONE_VOLUME_NAME,
    ORIGINAL_CONTAINER_NAME,
    IndexReuseRejected,
    index_code_identity,
    inspect_known_containers,
    read_only_database_url,
    require_read_only_connection,
    validate_clone_runtime,
    validate_index_identity,
)


BASELINE_GIT_SHA = "241d2c7ebdc4911e92a18bdc1e48307d85c87503"
DATASET_RUNS = {
    "scifact": ("20260928T060742Z-6f0c61007b", "rag_eval_trust_6f0c61007b_scifact"),
    "miracl-zh": ("20260928T071014Z-cffac0cd6a", "rag_eval_trust_cffac0cd6a_miracl_zh"),
    "longbench-zh": ("20260928T074111Z-442ee43dd3", "rag_eval_trust_442ee43dd3_longbench_"),
}
BASELINE_ARM = "baseline_equal_rrf"
CANDIDATE_ARM = "candidate_weighted_rrf_context_diversity"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReadOnlyContentStorage:
    """Resolve indexed source object keys without creating or modifying paths."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def path_for(self, storage_key: str) -> Path:
        relative = Path(storage_key)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("objects",):
            raise IndexReuseRejected("storage_key")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise IndexReuseRejected("source_object_missing")
        return path


@dataclass
class AttachedDevelopmentIndex:
    dataset: DevelopmentDataset
    run_manifest: dict[str, Any]
    run_manifest_sha256: str
    database: str
    engine: Any
    repository: PostgresKnowledgeRepository
    ollama: OllamaGateway
    model_identities: dict[str, Any]
    kb_id: str
    index: dict[str, Any]
    profile_id: str
    identity: dict[str, Any]
    identity_proof: dict[str, Any]
    source_bindings: dict[str, dict[str, str]]
    storage: ReadOnlyContentStorage

    def dispose(self) -> None:
        self.engine.dispose()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_frozen_qid_list(
    qids: Any,
    expected_sha256: Any,
    case_ids: set[str] | None = None,
    *,
    limit: int = 80,
) -> list[str]:
    """Validate the exact ordered QID list before any retrieval call."""
    if (not isinstance(qids, list) or not 0 < len(qids) <= limit
            or any(not isinstance(qid, str) or not qid for qid in qids)
            or len(set(qids)) != len(qids)):
        raise IndexReuseRejected("frozen_development_qids")
    if case_ids is not None and not set(qids) <= case_ids:
        raise IndexReuseRejected("frozen_development_qids")
    actual_sha256 = _canonical_sha256(qids)
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256) or actual_sha256 != expected_sha256:
        raise IndexReuseRejected("qid_list_sha256")
    return list(qids)


def validate_postflight_identity(
    expected_models: dict[str, Any],
    actual_models: dict[str, Any],
    expected_runtime: dict[str, Any],
    actual_runtime: dict[str, Any],
) -> dict[str, str]:
    if expected_models != actual_models:
        raise IndexReuseRejected("model_digest_postflight")
    if expected_runtime != actual_runtime:
        raise IndexReuseRejected("runtime_postflight")
    return {"status": "IDENTITY_UNCHANGED"}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return _sha256(payload.encode("utf-8"))


def _source_content(document: PublicDocument) -> str:
    title = document.title.strip()
    return f"# {title}\n\n{document.text}" if title else document.text


def _normalised_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _manifest_path(data_root: Path, dataset: str) -> Path:
    run_id, _ = DATASET_RUNS[dataset]
    return Path(data_root) / "runs" / dataset / "development" / run_id / "manifest.json"


def _external_run_id(manifest: dict[str, Any]) -> str:
    value = manifest.get("experiment_id") or manifest.get("run_id")
    if not isinstance(value, str) or not value:
        raise IndexReuseRejected("run_id")
    return value


def _profile_identity(profile: dict[str, Any]) -> str:
    fields = {key: profile[key] for key in (
        "provider", "model_name", "model_revision", "dimension", "distance", "fingerprint")
    }
    return _canonical_sha256(fields)


def _source_binding_sha256(bindings: dict[str, dict[str, str]]) -> str:
    rows = [{"source_document_id": row["document_id"], "source_version": row["source_version"],
             "normalized_content_sha256": _sha256(row["text"].encode("utf-8"))}
            for row in bindings.values()]
    rows.sort(key=lambda item: item["source_document_id"])
    return _canonical_sha256(rows)


def _strip_verified_index_diagnostics(index: dict[str, Any]) -> dict[str, Any]:
    """Drop raw indexed text and vector dumps after fingerprint verification."""
    index.pop("chunks", None)
    return index


def _read_database_index_metadata(connection, database: str, dataset: DevelopmentDataset):
    require_read_only_connection(connection, database)
    schema_rows = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars().all()
    if len(schema_rows) != 1:
        raise IndexReuseRejected("schema_revision")
    schema_revision = schema_rows[0]
    if schema_revision != "0013_message_run_link":
        raise IndexReuseRejected("schema_revision")

    doc_rows = [dict(row) for row in connection.execute(text("""
        SELECT d.knowledge_base_id::text AS kb_id,d.id::text AS document_id,d.file_name,d.media_type,
               v.id::text AS version_id,v.source_sha256,v.storage_key,v.normalized_content_sha256,
               v.parser_version,v.normalizer_version,v.chunker_version,v.chunk_strategy,v.index_status
        FROM documents d JOIN document_versions v ON v.id=d.active_version_id
        WHERE d.deleted_at IS NULL ORDER BY d.file_name
    """)).mappings()]
    if len(doc_rows) != len(dataset.documents):
        raise IndexReuseRejected("index_document_count")
    kb_ids = {row["kb_id"] for row in doc_rows}
    if len(kb_ids) != 1:
        raise IndexReuseRejected("knowledge_base_scope")
    kb_id = next(iter(kb_ids))

    profile_rows = [dict(row) for row in connection.execute(text("""
        SELECT DISTINCT p.id::text AS profile_id,p.provider,p.model_name,p.model_revision,
                        p.dimension,p.distance,p.fingerprint
        FROM chunks c JOIN chunk_embeddings ce ON ce.chunk_id=c.id
        JOIN embedding_profiles p ON p.id=ce.profile_id
        WHERE c.knowledge_base_id=CAST(:kb_id AS uuid)
        ORDER BY p.model_name,p.id
    """), {"kb_id": kb_id}).mappings()]
    if len(profile_rows) != 1:
        raise IndexReuseRejected("embedding_profile")
    profile = profile_rows[0]

    expected_by_filename: dict[str, tuple[PublicDocument, str, str]] = {}
    for document in dataset.documents:
        filename = hashlib.sha256(document.doc_id.encode("utf-8")).hexdigest()[:24] + ".md"
        content = _source_content(document)
        payload = content.encode("utf-8")
        normalized = _normalised_content(content)
        expected_by_filename[filename] = (document, _sha256(payload), normalized)
    if set(expected_by_filename) != {row["file_name"] for row in doc_rows}:
        raise IndexReuseRejected("source_document_binding")

    bindings: dict[str, dict[str, str]] = {}
    state_rows = set()
    for row in doc_rows:
        document, source_version, normalized = expected_by_filename[row["file_name"]]
        if (row["media_type"] != "text/markdown" or row["source_sha256"] != source_version
                or row["index_status"] != "ready" or row["normalized_content_sha256"] != _sha256(normalized.encode("utf-8"))):
            raise IndexReuseRejected("source_version_binding")
        state_rows.add((row["parser_version"], row["normalizer_version"], row["chunker_version"],
                        row["chunk_strategy"], row["index_status"]))
        bindings[row["document_id"]] = {
            "document_id": document.doc_id,
            "source_version": source_version,
            "text": normalized,
        }
    if state_rows != {("text/v1", "text/v1", "adaptive/v1", "heading_recursive", "ready")}:
        raise IndexReuseRejected("parser_chunker_or_ready_state")
    if (profile["provider"] != "ollama" or profile["model_name"] != "bge-m3:latest"
            or profile["model_revision"] != "local" or profile["dimension"] != 1024
            or profile["distance"] != "cosine" or profile["fingerprint"] != "ollama-local-1024"):
        raise IndexReuseRejected("embedding_profile")
    return kb_id, doc_rows, profile, bindings, schema_revision


def attach_verified_development_index(
    *,
    repository_root: Path,
    data_root: Path,
    dataset_name: str,
    admin_database_url: str,
    runtime: dict[str, Any],
    ollama: OllamaGateway,
) -> AttachedDevelopmentIndex:
    """Attach one already-built clone database using only a read-only SQL URL."""
    if dataset_name not in DATASET_RUNS:
        raise IndexReuseRejected("dataset")
    dataset = load_development_dataset(dataset_name, data_root)
    run_id, database = DATASET_RUNS[dataset_name]
    run_dir = Path(data_root) / "runs" / dataset_name / "development" / run_id
    run_manifest_path = run_dir / "manifest.json"
    run_manifest_bytes = run_manifest_path.read_bytes()
    run_manifest = json.loads(run_manifest_bytes)
    run_manifest_sha256 = _sha256(run_manifest_bytes)
    if (run_manifest.get("dataset") != dataset_name or run_manifest.get("split") != "development"
            or run_manifest.get("locked_split_scored") is not False
            or run_manifest.get("git_sha") != BASELINE_GIT_SHA
            or run_manifest.get("corpus_hash") != dataset.corpus_sha256
            or run_manifest.get("prepared_manifest_hash") != dataset.manifest_sha256):
        raise IndexReuseRejected("development_run_manifest")
    if run_manifest.get("effective_config") != {
        "candidate_k": 32, "chunk_overlap": 120, "chunk_size": 1200,
        "context_budget_chars": 8000, "rrf_k": 60, "top_k": 5,
    }:
        raise IndexReuseRejected("baseline_index_configuration")

    runtime_proof = validate_clone_runtime(runtime)
    if database not in ALLOWED_DATABASES:
        raise IndexReuseRejected("database")
    connection_url = read_only_database_url(admin_database_url, database)
    engine = create_engine(connection_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    storage = ReadOnlyContentStorage(run_dir / "storage")
    repository = PostgresKnowledgeRepository(
        engine,
        storage,
        parsers=ParserRegistry(),
        embedding_provider=None,
        max_chunk_chars=1200,
        chunk_overlap=120,
    )
    try:
        with engine.connect() as connection:
            kb_id, doc_rows, profile, bindings, schema_revision = _read_database_index_metadata(
                connection, database, dataset)
        index = read_index_snapshot(repository, kb_id, bindings, ollama.embedding_model)
        if (index["index_counts"] != run_manifest.get("index_counts")
                or index["index_version"] != run_manifest.get("index_version")):
            raise IndexReuseRejected("index_fingerprint_or_counts")
        actual_models = model_identities(ollama)
        if actual_models != run_manifest.get("models"):
            raise IndexReuseRejected("model_digest")
        code_identity = index_code_identity(repository_root, BASELINE_GIT_SHA)
        if code_identity["current_index_code_sha256"] != code_identity["baseline_index_code_sha256"]:
            raise IndexReuseRejected("index_code_compatibility")
        actual_source_binding = _source_binding_sha256(bindings)
        expected_source_rows = []
        for document in dataset.documents:
            content = _source_content(document)
            normalized = _normalised_content(content)
            expected_source_rows.append({
                "source_document_id": document.doc_id,
                "source_version": _sha256(content.encode("utf-8")),
                "normalized_content_sha256": _sha256(normalized.encode("utf-8")),
            })
        expected_source_rows.sort(key=lambda item: item["source_document_id"])
        expected_source_binding = _canonical_sha256(expected_source_rows)
        if actual_source_binding != expected_source_binding:
            raise IndexReuseRejected("source_binding_hash")
        effective_config = run_manifest["effective_config"]
        profile_signature = _profile_identity(profile)
        actual_identity = {
            "dataset": dataset_name,
            "dataset_version": dataset.dataset_version,
            "split": dataset.split,
            "run_id": _external_run_id(run_manifest),
            "run_manifest_sha256": run_manifest_sha256,
            "corpus_sha256": dataset.corpus_sha256,
            "development_cases_sha256": dataset.development_cases_sha256,
            "prepared_manifest_sha256": dataset.manifest_sha256,
            "parser_version": "text/v1",
            "chunker_version": "adaptive/v1",
            "chunk_size": effective_config["chunk_size"],
            "chunk_overlap": effective_config["chunk_overlap"],
            "embedding_model": profile["model_name"],
            "embedding_digest": actual_models["embedding"]["digest"],
            "embedding_profile": profile_signature,
            "embedding_dimension": profile["dimension"],
            "schema_revision": schema_revision,
            "ready_state": True,
            "index_fingerprint": index["index_version"],
            "index_counts": index["index_counts"],
            "source_binding_sha256": actual_source_binding,
            "index_code_sha256": code_identity["current_index_code_sha256"],
            "baseline_git_sha": run_manifest["git_sha"],
            "container_id": runtime_proof["clone_container_id"],
            "container_name": CLONE_CONTAINER_NAME,
            "volume_name": CLONE_VOLUME_NAME,
            "database": database,
            "port": CLONE_PORT,
            "database_read_only": True,
        }
        expected_profile = {
            "provider": "ollama",
            "model_name": run_manifest["models"]["embedding"]["name"],
            "model_revision": "local",
            "dimension": 1024,
            "distance": "cosine",
            "fingerprint": "ollama-local-1024",
        }
        expected_identity = {
            "dataset": dataset_name,
            "dataset_version": dataset.dataset_version,
            "split": "development",
            "run_id": _external_run_id(run_manifest),
            "run_manifest_sha256": run_manifest_sha256,
            "corpus_sha256": dataset.corpus_sha256,
            "development_cases_sha256": dataset.development_cases_sha256,
            "prepared_manifest_sha256": dataset.manifest_sha256,
            "parser_version": "text/v1",
            "chunker_version": "adaptive/v1",
            "chunk_size": effective_config["chunk_size"],
            "chunk_overlap": effective_config["chunk_overlap"],
            "embedding_model": run_manifest["models"]["embedding"]["name"],
            "embedding_digest": run_manifest["models"]["embedding"]["digest"],
            "embedding_profile": _profile_identity(expected_profile),
            "embedding_dimension": 1024,
            "schema_revision": "0013_message_run_link",
            "ready_state": True,
            "index_fingerprint": run_manifest["index_version"],
            "index_counts": run_manifest["index_counts"],
            "source_binding_sha256": expected_source_binding,
            "index_code_sha256": code_identity["baseline_index_code_sha256"],
            "baseline_git_sha": BASELINE_GIT_SHA,
            "container_id": runtime_proof["clone_container_id"],
            "container_name": CLONE_CONTAINER_NAME,
            "volume_name": CLONE_VOLUME_NAME,
            "database": database,
            "port": CLONE_PORT,
            "database_read_only": True,
        }
        proof = validate_index_identity(expected_identity, actual_identity)
        proof.update({
            "runtime": runtime_proof,
            "current_index_code_sha256": code_identity["current_index_code_sha256"],
            "baseline_index_code_sha256": code_identity["baseline_index_code_sha256"],
            "embedding_profile_id": profile["profile_id"],
            "source_binding_sha256": actual_source_binding,
        })
        # read_index_snapshot retains source text and serialized vectors for its
        # fingerprint. Discard them once identity verification is complete.
        index = _strip_verified_index_diagnostics(index)
        return AttachedDevelopmentIndex(
            dataset=dataset,
            run_manifest=run_manifest,
            run_manifest_sha256=run_manifest_sha256,
            database=database,
            engine=engine,
            repository=repository,
            ollama=ollama,
            model_identities=actual_models,
            kb_id=kb_id,
            index=index,
            profile_id=profile["profile_id"],
            identity=actual_identity,
            identity_proof=proof,
            source_bindings=bindings,
            storage=storage,
        )
    except IndexReuseRejected:
        engine.dispose()
        raise
    except Exception as exc:
        engine.dispose()
        raise IndexReuseRejected("index_validation") from exc


def _context_for_result(
    *, result, repository, case: PublicCase, index: dict[str, Any], context_builder: ContextBuilder,
    arm: str, top_k: int,
) -> dict[str, Any]:
    if arm == BASELINE_ARM:
        candidates = list(result.items)
        selected = context_builder.select(candidates)
        full_rank = list(result.fused_ranking)
    else:
        candidates = list(result.context_items)
        selected = context_builder.select(candidates, max_items=top_k, max_per_document=2)
        full_rank = list(result.fused_ranking)
    run_id = f"round1-{case.qid}-{arm}"
    citation_service = CitationService(InMemoryCitationStore())
    context, labels = context_builder.build(run_id, selected, citation_service)
    selected_ids = [citation_service.snapshots[(run_id, label)].chunk_id for label in labels]
    chunk_to_document = {chunk_id: span.document_id for chunk_id, span in index["spans"].items()}
    fused_ids = [hit.chunk_id for hit in full_rank]
    selected_documents = fold_chunk_ranking(selected_ids, chunk_to_document)
    fused_documents = fold_chunk_ranking(fused_ids, chunk_to_document)
    metrics_at_5 = ranking_metrics(fused_documents, case.qrels, k=5)
    metrics_at_10 = ranking_metrics(fused_documents, case.qrels, k=10)
    context_metrics = ranking_metrics(selected_documents, case.qrels, k=5)
    relevant = {doc_id for doc_id, grade in case.qrels.items() if grade > 0}
    full_hits = relevant.intersection(fused_documents)
    context_hits = relevant.intersection(selected_documents)
    return {
        "document_metrics_at_5": metrics_at_5,
        "document_ndcg_at_10": metrics_at_10["ndcg_at_k"],
        "context_document_metrics_at_5": context_metrics,
        "context_selected_count": len(selected_ids),
        "context_characters": len(context),
        "context_sha256": _sha256(context.encode("utf-8")),
        "context_document_ids": selected_documents,
        "fused_top5_document_ids": fused_documents[:5],
        "full_fused_positive_documents": len(full_hits),
        "positive_documents_missing_from_context": len(full_hits - context_hits),
    }


def _retrieval_arm(retriever: HybridRetriever, case: PublicCase, kb_id: str) -> tuple[Any, float]:
    started = time.perf_counter()
    result = retriever.retrieve(Scope.from_ids([kb_id]), case.question)
    if "VECTOR_UNAVAILABLE" in result.degradation_flags or not result.candidate_rankings.get("vector"):
        raise IndexReuseRejected("vector_retrieval_degraded")
    return result, (time.perf_counter() - started) * 1000


def _aggregate_retrieval(dataset_rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    metric_rows = [row[arm]["document_metrics_at_5"] for row in dataset_rows]
    context_rows = [row[arm]["context_document_metrics_at_5"] for row in dataset_rows]
    latencies = [row[arm]["retrieval_latency_ms"] for row in dataset_rows]
    context_latencies = [row[arm]["context_building_ms"] for row in dataset_rows]
    ndcg_at_10 = [row[arm]["document_ndcg_at_10"] for row in dataset_rows]
    return {
        "qid_count": len(dataset_rows),
        "document_recall_at_5": aggregate_metrics(metric_rows)["metrics"]["recall_at_k"],
        "document_mrr_at_5": aggregate_metrics(metric_rows)["metrics"]["mrr_at_k"],
        "document_ndcg_at_5": aggregate_metrics(metric_rows)["metrics"]["ndcg_at_k"],
        "document_ndcg_at_10": aggregate_metrics([{"document_ndcg_at_10": value} for value in ndcg_at_10])["metrics"]["document_ndcg_at_10"],
        "context_recall_at_5": aggregate_metrics(context_rows)["metrics"]["recall_at_k"],
        "context_mrr_at_5": aggregate_metrics(context_rows)["metrics"]["mrr_at_k"],
        "context_ndcg_at_5": aggregate_metrics(context_rows)["metrics"]["ndcg_at_k"],
        "retrieval_latency_ms": aggregate_metrics([{"retrieval_latency_ms": value} for value in latencies]),
        "context_building_ms": aggregate_metrics([{"context_building_ms": value} for value in context_latencies]),
        "full_fused_positive_document_misses_from_context": sum(
            row[arm]["positive_documents_missing_from_context"] for row in dataset_rows),
        "full_fused_positive_document_hits": sum(row[arm]["full_fused_positive_documents"] for row in dataset_rows),
        "hybrid_top5_hit_lost_in_context_qids": sum(
            row[arm]["document_metrics_at_5"]["hit_at_k"] == 1
            and row[arm]["context_document_metrics_at_5"]["hit_at_k"] == 0
            for row in dataset_rows),
    }


def _run_frozen_retrieval(attached: AttachedDevelopmentIndex, qids: list[str], candidate: dict[str, Any]):
    by_qid = {case.qid: case for case in attached.dataset.cases}
    if len(set(qids)) != len(qids) or any(qid not in by_qid for qid in qids):
        raise IndexReuseRejected("frozen_development_qids")
    selected_cases = [by_qid[qid] for qid in qids]
    provider = attached.ollama
    cache = RunScopedQueryEmbeddingCache(
        provider,
        EmbeddingCacheIdentity(
            model_digest=attached.model_identities["embedding"]["digest"],
            profile=attached.profile_id,
            dimension=1024,
        ),
    )
    baseline = HybridRetriever(
        attached.repository, embedding_provider=cache, top_k=5, candidate_k=32, rrf_k=60,
    )
    candidate_retriever = HybridRetriever(
        attached.repository, embedding_provider=cache,
        top_k=candidate["top_k"], candidate_k=candidate["candidate_k"], rrf_k=candidate["rrf_k"],
        source_weights={"keyword": candidate["keyword_weight"], "vector": candidate["vector_weight"]},
        context_candidate_k=candidate["context_candidate_k"],
        context_max_per_document=candidate["max_chunks_per_document_first_pass"],
    )
    context_builder = ContextBuilder(8000)
    rows = []
    qa_contexts = {}
    for index, case in enumerate(selected_cases, start=1):
        # Deterministic qid hashing balances cold and cache-hit latency between arms.
        baseline_first = hashlib.sha256(case.qid.encode("utf-8")).digest()[0] % 2 == 0
        if baseline_first:
            baseline_result, baseline_ms = _retrieval_arm(baseline, case, attached.kb_id)
            candidate_result, candidate_ms = _retrieval_arm(candidate_retriever, case, attached.kb_id)
        else:
            candidate_result, candidate_ms = _retrieval_arm(candidate_retriever, case, attached.kb_id)
            baseline_result, baseline_ms = _retrieval_arm(baseline, case, attached.kb_id)
        baseline_vector = [hit.chunk_id for hit in baseline_result.candidate_rankings.get("vector", ())]
        candidate_vector = [hit.chunk_id for hit in candidate_result.candidate_rankings.get("vector", ())]
        if baseline_vector != candidate_vector:
            raise IndexReuseRejected("vector_ranking_not_stable_across_arms")
        base_context_started = time.perf_counter()
        base_context = _context_for_result(
            result=baseline_result, repository=attached.repository, case=case, index=attached.index,
            context_builder=context_builder, arm=BASELINE_ARM, top_k=5,
        )
        base_context["context_building_ms"] = (time.perf_counter() - base_context_started) * 1000
        candidate_context_started = time.perf_counter()
        candidate_context = _context_for_result(
            result=candidate_result, repository=attached.repository, case=case, index=attached.index,
            context_builder=context_builder, arm=CANDIDATE_ARM, top_k=5,
        )
        candidate_context["context_building_ms"] = (time.perf_counter() - candidate_context_started) * 1000
        base_context["retrieval_latency_ms"] = baseline_ms
        candidate_context["retrieval_latency_ms"] = candidate_ms
        rows.append({"qid": case.qid, BASELINE_ARM: base_context, CANDIDATE_ARM: candidate_context,
                     "vector_top5_document_ids": fold_chunk_ranking(
                         baseline_vector[:5], {chunk_id: span.document_id for chunk_id, span in attached.index["spans"].items()}),
                     "vector_hit_at_5": ranking_metrics(
                         fold_chunk_ranking(baseline_vector, {chunk_id: span.document_id for chunk_id, span in attached.index["spans"].items()}),
                         case.qrels, k=5)["hit_at_k"]})
        qa_contexts[case.qid] = {
            "case": case,
            "baseline_result": baseline_result,
            "candidate_result": candidate_result,
        }
        if index % 10 == 0:
            print(json.dumps({"event": "development_qids_completed", "dataset": attached.dataset.name,
                              "completed": index, "total": len(selected_cases)}), flush=True)
    return {
        "dataset": attached.dataset.name,
        "qid_count": len(rows),
        "retrieval": {
            BASELINE_ARM: _aggregate_retrieval(rows, BASELINE_ARM),
            CANDIDATE_ARM: _aggregate_retrieval(rows, CANDIDATE_ARM),
        },
        "vector_positive_top5": sum(row["vector_hit_at_5"] or 0 for row in rows),
        "vector_to_baseline_hybrid_loss_count": sum(
            row["vector_hit_at_5"] == 1 and row[BASELINE_ARM]["document_metrics_at_5"]["hit_at_k"] == 0
            for row in rows),
        "vector_to_candidate_hybrid_loss_count": sum(
            row["vector_hit_at_5"] == 1 and row[CANDIDATE_ARM]["document_metrics_at_5"]["hit_at_k"] == 0
            for row in rows),
        "query_embedding_cache": cache.snapshot(),
        "per_qid": rows,
        "qa_contexts": qa_contexts,
    }


def _verify_postflight(
    attached: dict[str, AttachedDevelopmentIndex],
    ollama: OllamaGateway,
    expected_models: dict[str, Any],
    expected_runtime: dict[str, Any],
) -> dict[str, Any]:
    runtime_now = validate_clone_runtime(inspect_known_containers())
    models_now = model_identities(ollama)
    identity_status = validate_postflight_identity(expected_models, models_now, expected_runtime, runtime_now)
    indexes = {}
    for name, item in attached.items():
        try:
            with item.engine.connect() as connection:
                require_read_only_connection(connection, item.database)
            snapshot = read_index_snapshot(item.repository, item.kb_id, item.source_bindings, ollama.embedding_model)
            if (snapshot["index_counts"] != item.identity["index_counts"]
                    or snapshot["index_version"] != item.identity["index_fingerprint"]):
                raise IndexReuseRejected("index_changed_during_replay")
        except IndexReuseRejected:
            raise
        except Exception as exc:
            raise IndexReuseRejected("postflight_index_validation") from exc
        indexes[name] = {
            "status": "INDEX_UNCHANGED_READ_ONLY",
            "index_fingerprint": snapshot["index_version"],
            "index_counts": snapshot["index_counts"],
            "database_read_only": True,
        }
    return {**identity_status, "runtime": runtime_now, "model_identities": models_now, "indexes": indexes}


def run_frozen_development_replay(
    *,
    repository_root: Path,
    data_root: Path,
    qid_fixture_path: Path,
    admin_database_url: str,
    output_root: Path,
    run_qa_pilot: bool = False,
    preflight_only: bool = False,
) -> dict[str, Any]:
    """Preflight every fixed index before embedding; then run only frozen Dev QIDs."""
    fixture_bytes = Path(qid_fixture_path).read_bytes()
    fixture = json.loads(fixture_bytes)
    if fixture.get("schema_version") != "rag-retrieval-round1-qids-v1":
        raise IndexReuseRejected("qid_fixture_schema")
    candidate = fixture.get("candidate_config")
    required_candidate = {
        "fusion": "weighted_rrf", "vector_weight": 1.0, "keyword_weight": 0.25,
        "rrf_k": 60, "candidate_k": 32, "top_k": 5,
        "context_candidate_k": 10, "context_selected_max": 5,
        "max_chunks_per_document_first_pass": 2,
    }
    if candidate != required_candidate:
        raise IndexReuseRejected("frozen_candidate_config")
    if run_qa_pilot:
        raise IndexReuseRejected("qa_pilot_not_implemented")
    datasets_fixture = fixture.get("datasets")
    if not isinstance(datasets_fixture, dict) or set(datasets_fixture) != set(DATASET_RUNS):
        raise IndexReuseRejected("qid_fixture_datasets")
    for name, (source_run_id, _database) in DATASET_RUNS.items():
        dataset_entry = datasets_fixture[name]
        if (not isinstance(dataset_entry, dict) or dataset_entry.get("split") != "development"
                or dataset_entry.get("locked_split_scored") is not False
                or dataset_entry.get("source_run_id") != source_run_id
                or dataset_entry.get("baseline_git_sha") != BASELINE_GIT_SHA):
            raise IndexReuseRejected("qid_fixture_data_identity")
        validate_frozen_qid_list(dataset_entry.get("qids"), dataset_entry.get("qid_list_sha256"), limit=80)
        if len(dataset_entry["qids"]) != 80:
            raise IndexReuseRejected("frozen_development_qids")
    runtime = inspect_known_containers()
    runtime_proof = validate_clone_runtime(runtime)
    settings = Settings(cloud_enabled=False, langfuse_enabled=False, langfuse_capture_content=False)
    ollama = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_embedding_model)
    actual_models = model_identities(ollama)

    attached: dict[str, AttachedDevelopmentIndex] = {}
    try:
        # Validate all three complete indexes before any query embedding or answer call.
        for dataset_name in DATASET_RUNS:
            attached[dataset_name] = attach_verified_development_index(
                repository_root=repository_root,
                data_root=data_root,
                dataset_name=dataset_name,
                admin_database_url=admin_database_url,
                runtime=runtime,
                ollama=ollama,
            )
            if attached[dataset_name].model_identities != actual_models:
                raise IndexReuseRejected("cross_dataset_model_identity")
        for name, attached_index in attached.items():
            dataset_entry = datasets_fixture[name]
            if (dataset_entry.get("split") != "development" or dataset_entry.get("locked_split_scored") is not False
                    or dataset_entry.get("source_run_id") != DATASET_RUNS[name][0]
                    or dataset_entry.get("corpus_sha256") != attached_index.dataset.corpus_sha256
                    or dataset_entry.get("development_cases_sha256") != attached_index.dataset.development_cases_sha256
                    or dataset_entry.get("prepared_manifest_sha256") != attached_index.dataset.manifest_sha256
                    or dataset_entry.get("baseline_run_manifest_sha256") != attached_index.run_manifest_sha256
                    or len(dataset_entry.get("qids", [])) != 80):
                raise IndexReuseRejected("qid_fixture_data_identity")
            case_ids = {case.qid for case in attached_index.dataset.cases}
            validate_frozen_qid_list(dataset_entry["qids"], dataset_entry["qid_list_sha256"], case_ids, limit=80)

        current_sha = committed_code_sha(repository_root)
        if preflight_only:
            return {
                "schema_version": "rag-retrieval-round1-preflight-v1",
                "status": "PREFLIGHT_COMPLETE",
                "git_sha": current_sha,
                "qid_fixture_sha256": _sha256(fixture_bytes),
                "qid_selection_sha256": fixture["selection_sha256"],
                "qids_per_dataset": {name: len(entry["qids"]) for name, entry in datasets_fixture.items()},
                "indexes": {name: item.identity_proof for name, item in attached.items()},
                "runtime": runtime_proof,
                "model_identities": actual_models,
                "query_embedding_calls": 0,
                "corpus_embedding_calls": 0,
                "corpus_index_builds": 0,
                "database_writes": 0,
            }

        output_root = Path(output_root)
        output_root.mkdir(parents=True, exist_ok=False)
        preregistration = {
            "schema_version": "rag-retrieval-round1-prereg-v1",
            "git_sha": current_sha,
            "qid_fixture_sha256": _sha256(fixture_bytes),
            "qid_selection_sha256": fixture["selection_sha256"],
            "candidate_config": candidate,
            "split": "development",
            "qa_pilot_enabled": bool(run_qa_pilot),
            "qa_generation_call_limit": 24 if run_qa_pilot else 0,
            "warmup_calls": 0,
            "index_reuse": {name: item.identity_proof for name, item in attached.items()},
            "datasets": {name: {
                "run_manifest_sha256": item.run_manifest_sha256,
                "corpus_sha256": item.dataset.corpus_sha256,
                "development_cases_sha256": item.dataset.development_cases_sha256,
                "prepared_manifest_sha256": item.dataset.manifest_sha256,
                "qid_count": len(datasets_fixture[name]["qids"]),
                "qid_list_sha256": datasets_fixture[name]["qid_list_sha256"],
                "model_identities": item.model_identities,
                "index_identity_sha256": item.identity_proof["identity_sha256"],
            } for name, item in attached.items()},
            "runtime": runtime_proof,
            "query_embedding_cache": {
                "scope": "one cache per dataset run",
                "storage": "memory only",
                "key_fields": ["normalized_query_sha256", "exact_query_sha256", "model_digest", "profile", "dimension"],
            },
        }
        prereg_path = output_root / "preregistration.json"
        prereg_path.write_text(json.dumps(preregistration, sort_keys=True, indent=2) + "\n", encoding="utf-8")

        dataset_results = {}
        for dataset_name, attached_index in attached.items():
            dataset_result = _run_frozen_retrieval(
                attached_index, datasets_fixture[dataset_name]["qids"], candidate)
            dataset_results[dataset_name] = dataset_result
            # Do not retain contexts, questions, qrels, or answers in machine outputs.
            dataset_result.pop("qa_contexts", None)
            _write_sanitized_result(output_root, preregistration, dataset_results,
                                    qa_status="NOT_RUN")
            print(json.dumps({"event": "dataset_retrieval_complete", "dataset": dataset_name,
                              "qids": dataset_result["qid_count"],
                              "query_embedding_provider_calls": dataset_result["query_embedding_cache"]["provider_calls"]}),
                  flush=True)

        result = {
            "schema_version": "rag-retrieval-round1-result-v1",
            "status": "RETRIEVAL_COMPLETE",
            "git_sha": preregistration["git_sha"],
            "qid_fixture_sha256": preregistration["qid_fixture_sha256"],
            "qid_selection_sha256": preregistration["qid_selection_sha256"],
            "candidate_config": candidate,
            "datasets": dataset_results,
            "corpus_embedding_calls": 0,
            "corpus_index_builds": 0,
            "database_writes": 0,
            "locked_data_opened": False,
            "judge_calls": 0,
            "cloud_calls": 0,
            "qa": {"status": "NOT_RUN", "reason": "QA prompt text was not included in the frozen fixture"},
            "query_embedding_calls": sum(
                row["query_embedding_cache"]["provider_calls"] for row in dataset_results.values()),
            "postflight": _verify_postflight(attached, ollama, actual_models, runtime_proof),
        }
        # Drop potentially text-bearing objects before writing or returning.
        for row in result["datasets"].values():
            row.pop("qa_contexts", None)
        _write_sanitized_result(output_root, preregistration, dataset_results, qa_status=result["qa"]["status"],
                                status="RETRIEVAL_COMPLETE", postflight=result["postflight"])
        return result
    finally:
        for item in attached.values():
            item.dispose()


def _write_sanitized_result(
    output_root: Path,
    preregistration: dict[str, Any],
    datasets: dict[str, Any],
    qa_status: str,
    *,
    status: str = "IN_PROGRESS",
    postflight: dict[str, Any] | None = None,
):
    safe_datasets = {}
    for name, dataset_result in datasets.items():
        safe_datasets[name] = {key: value for key, value in dataset_result.items() if key != "qa_contexts"}
    result = {
        "schema_version": "rag-retrieval-round1-result-v1",
        "status": status,
        "git_sha": preregistration["git_sha"],
        "qid_fixture_sha256": preregistration["qid_fixture_sha256"],
        "qid_selection_sha256": preregistration["qid_selection_sha256"],
        "candidate_config": preregistration["candidate_config"],
        "index_reuse": {name: item for name, item in preregistration["index_reuse"].items()},
        "datasets": safe_datasets,
        "query_embedding_calls": sum(
            dataset.get("query_embedding_cache", {}).get("provider_calls", 0)
            for dataset in safe_datasets.values()),
        "query_embedding_input_items": sum(
            dataset.get("query_embedding_cache", {}).get("provider_input_items", 0)
            for dataset in safe_datasets.values()),
        "corpus_embedding_calls": 0,
        "corpus_index_builds": 0,
        "database_writes": 0,
        "locked_data_opened": False,
        "judge_calls": 0,
        "cloud_calls": 0,
        "qa": {
            "status": qa_status,
            "reason": "QA prompt text was not included in the frozen fixture" if qa_status == "NOT_RUN" else None,
            "generation_calls": 0,
        },
        "postflight": postflight,
    }
    path = output_root / "retrieval-results.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


__all__ = ["AttachedDevelopmentIndex", "ReadOnlyContentStorage", "attach_verified_development_index",
           "run_frozen_development_replay"]
