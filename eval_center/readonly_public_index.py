"""Evaluation-only attachment to already completed public benchmark indexes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.config import Settings
from eval_center.isolated_index import guarded_database_url, read_index_snapshot
from eval_center.public_data import DATASET_VERSIONS, load_public_dataset
from eval_center.public_runner import _expected_normalized_text, _source_content
from eval_center.runtime import model_identities
from eval_center.verification import ExperimentInvalidError


_APPROVED_RUNS = {
    "scifact": "20260928T060742Z-6f0c61007b",
    "miracl-zh": "20260928T071014Z-cffac0cd6a",
    "longbench-zh": "20260928T074111Z-442ee43dd3",
}


class ReadOnlyStorage:
    """Only resolve existing content-addressed objects; never mkdir or write."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ExperimentInvalidError("completed_run_storage_missing")

    def path_for(self, storage_key: str) -> Path:
        relative = Path(storage_key)
        path = (self.root / relative).resolve()
        if (relative.is_absolute() or relative.parts[:1] != ("objects",)
                or not path.is_relative_to(self.root) or not path.is_file()):
            raise ExperimentInvalidError("completed_run_source_missing")
        return path


def approved_run_directory(data_root: Path, dataset: str) -> Path:
    run_id = _APPROVED_RUNS.get(dataset)
    if run_id is None:
        raise ExperimentInvalidError("unapproved_public_dataset")
    return Path(data_root) / "runs" / dataset / "development" / run_id


def validate_completed_run(run_dir: Path, dataset: str) -> dict[str, Any]:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        cases = (run_dir / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentInvalidError("incomplete_public_run") from exc
    if (manifest.get("dataset") != dataset or manifest.get("dataset_version") != DATASET_VERSIONS[dataset]
            or manifest.get("split") != "development" or manifest.get("profile") != "standard"
            or manifest.get("phase") != "optimize" or not manifest.get("ended_at")
            or manifest.get("sample_count") != len([line for line in cases if line.strip()])
            or not isinstance(manifest.get("index_version"), str)
            or not isinstance(manifest.get("index_counts"), dict)
            or not (run_dir / "report.json").is_file()):
        raise ExperimentInvalidError("incomplete_public_run")
    if (run_dir / "interrupted.json").exists() or (run_dir / "failure.json").exists():
        raise ExperimentInvalidError("incomplete_public_run")
    models = manifest.get("models", {})
    embedding = models.get("embedding", {})
    if not isinstance(embedding.get("digest"), str) or len(embedding["digest"]) != 64:
        raise ExperimentInvalidError("index_model_identity_missing")
    return manifest


def _readonly_engine(database_url: str, *, statement_timeout_ms: int | None = None) -> Engine:
    url = guarded_database_url(database_url)
    options = ["-c default_transaction_read_only=on"]
    if statement_timeout_ms is not None:
        if type(statement_timeout_ms) is not int or statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms must be a positive integer")
        options.append(f"-c statement_timeout={statement_timeout_ms}")
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "options": " ".join(options),
            "application_name": "public-benchmark-evaluation-readonly",
        },
    )


def _verify_readonly(engine: Engine) -> None:
    with engine.connect() as connection:
        if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise ExperimentInvalidError("readonly_transaction_not_enabled")
        try:
            connection.execute(text("CREATE TEMP TABLE evaluation_readonly_probe(id integer)"))
        except Exception as exc:
            if "read-only" not in str(exc).lower() and "read only" not in str(exc).lower():
                raise ExperimentInvalidError("readonly_write_probe_unexpected_error") from exc
        else:
            raise ExperimentInvalidError("readonly_write_probe_succeeded")


def attach_completed_index(
    *,
    data_root: Path,
    dataset: str,
    database_url: str,
    embedding_model: str,
    chunk_size: int = 1200,
    chunk_overlap: int = 120,
    statement_timeout_ms: int | None = None,
) -> dict[str, Any]:
    run_dir = approved_run_directory(data_root, dataset)
    manifest = validate_completed_run(run_dir, dataset)
    prepared = load_public_dataset(dataset, data_root, split="development", phase="optimize")
    token = run_dir.name.rsplit("-", 1)[-1]
    database_name = f"rag_eval_trust_{token}_{dataset.replace('-', '_')[:10]}"
    if make_url(database_url).database != database_name:
        raise ExperimentInvalidError("database_identity_mismatch")
    engine = _readonly_engine(database_url, statement_timeout_ms=statement_timeout_ms)
    _verify_readonly(engine)
    with engine.connect() as connection:
        schema_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        db_name = connection.execute(text("SELECT current_database()")).scalar_one()
        knowledge_bases = connection.execute(text("""
            SELECT id::text AS id, cloud_allowed
            FROM knowledge_bases WHERE deleted_at IS NULL
        """)).mappings().all()
        if len(knowledge_bases) != 1 or knowledge_bases[0]["cloud_allowed"]:
            raise ExperimentInvalidError("knowledge_base_identity_mismatch")
        knowledge_base_id = knowledge_bases[0]["id"]
        rows = connection.execute(text("""
            SELECT d.id::text AS db_document_id, d.file_name, v.source_sha256, v.storage_key,
                   v.normalized_content_sha256, v.index_status, d.media_type
            FROM documents d JOIN document_versions v ON v.id=d.active_version_id
            WHERE d.knowledge_base_id=CAST(:kb AS uuid) AND d.deleted_at IS NULL
        """), {"kb": knowledge_base_id}).mappings().all()
    if db_name != database_name or not rows or schema_revision != "0013_message_run_link":
        raise ExperimentInvalidError("database_identity_mismatch")
    if manifest.get("effective_config", {}).get("chunk_size") != chunk_size or manifest.get("effective_config", {}).get("chunk_overlap") != chunk_overlap:
        raise ExperimentInvalidError("chunker_config_mismatch")
    prepared_corpus_hash = next((record["sha256"] for record in prepared.manifest["files"]
                                 if record["path"] == "corpus.jsonl"), None)
    if prepared_corpus_hash != manifest.get("corpus_hash"):
        raise ExperimentInvalidError("corpus_hash_mismatch")
    by_file_name: dict[str, Any] = {}
    for document in prepared.documents:
        content = _source_content(document)
        source_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        file_name = f"{hashlib.sha256(document.doc_id.encode('utf-8')).hexdigest()[:24]}.md"
        if file_name in by_file_name:
            raise ExperimentInvalidError("duplicate_source_binding")
        by_file_name[file_name] = (document, source_sha, _expected_normalized_text(content))
    bindings: dict[str, dict[str, str]] = {}
    for row in rows:
        match = by_file_name.get(row["file_name"])
        if match is None or row["index_status"] != "ready":
            raise ExperimentInvalidError("source_version_binding_mismatch")
        document, source_sha, text_value = match
        if row["source_sha256"] != source_sha:
            raise ExperimentInvalidError("source_version_binding_mismatch")
        bindings[row["db_document_id"]] = {
            "document_id": document.doc_id,
            "source_version": row["source_sha256"],
            "text": text_value,
        }
    if len(bindings) != len(prepared.documents):
        raise ExperimentInvalidError("document_count_identity_mismatch")
    storage = ReadOnlyStorage(run_dir / "storage")
    settings = Settings(database_url=database_url, storage_root=run_dir / "storage",
                         cloud_enabled=False, langfuse_enabled=False,
                         max_chunk_chars=chunk_size, chunk_overlap=chunk_overlap)
    gateway = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, embedding_model)
    actual_models = model_identities(gateway)
    if actual_models["embedding"] != manifest["models"]["embedding"]:
        raise ExperimentInvalidError("embedding_model_digest_mismatch")
    repository = PostgresKnowledgeRepository(engine, storage, embedding_provider=gateway,
                                              max_chunk_chars=chunk_size, chunk_overlap=chunk_overlap)
    snapshot = read_index_snapshot(repository, knowledge_base_id, bindings, embedding_model)
    if snapshot["index_version"] != manifest["index_version"] or snapshot["index_counts"] != manifest["index_counts"]:
        raise ExperimentInvalidError("index_identity_mismatch")
    if manifest.get("embedding_dimension") != 1024:
        raise ExperimentInvalidError("embedding_dimension_mismatch")
    profile_id = repository.get_embedding_profile_id(embedding_model, 1024)
    if profile_id is None:
        raise ExperimentInvalidError("embedding_profile_missing")
    return {
        "dataset": dataset,
        "run_dir": run_dir,
        "manifest": manifest,
        "database_name": database_name,
        "knowledge_base_id": knowledge_base_id,
        "schema_revision": schema_revision,
        "engine": engine,
        "repository": repository,
        "gateway": gateway,
        "embedding_profile_id": profile_id,
        "actual_models": actual_models,
        "index": {**snapshot, "document_ids": {document.doc_id for document in prepared.documents}},
        "bindings": bindings,
        "database_writes": 0,
        "corpus_embeddings": 0,
        "new_indexes": 0,
    }
