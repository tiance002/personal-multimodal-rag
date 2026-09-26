from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.graph import GraphService
from backend.app.config import Settings
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult


def _engine_or_skip():
    database_url = os.getenv("RAG_DATABASE_URL", Settings().database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    return engine


class FixedEmbeddingProvider:
    def __init__(self, model_name: str) -> None:
        self.embedding_model = model_name

    def embed(self, texts, timeout_seconds):
        vector = [1.0] + [0.0] * 1023
        return EmbeddingResult([vector for _ in texts], self.embedding_model, 1024, 0.1)


def test_postgres_vector_candidates_require_the_exact_profile(tmp_path: Path) -> None:
    engine = _engine_or_skip()
    storage = ContentAddressedStorage(tmp_path / "storage")
    repository_a = PostgresKnowledgeRepository(engine, storage, embedding_provider=FixedEmbeddingProvider("profile-a-model"))
    repository_b = PostgresKnowledgeRepository(engine, storage, embedding_provider=FixedEmbeddingProvider("profile-b-model"))
    suffix = tmp_path.name
    knowledge_base = repository_a.create_knowledge_base(f"profile-{suffix}")
    try:
        first = repository_a.create_upload(
            knowledge_base["id"],
            "first.txt",
            "text/plain",
            storage.put_stream(io.BytesIO(b"first profile content")),
        )
        second = repository_b.create_upload(
            knowledge_base["id"],
            "second.txt",
            "text/plain",
            storage.put_stream(io.BytesIO(b"second profile content")),
        )
        repository_a.process_job(first["job_id"])
        repository_b.process_job(second["job_id"])
        profile_a = repository_a.get_embedding_profile_id("profile-a-model", 1024)
        profile_b = repository_a.get_embedding_profile_id("profile-b-model", 1024)

        hits_a = repository_a.vector_candidates(Scope.from_ids([knowledge_base["id"]]), [1.0] + [0.0] * 1023, 10, profile_id=profile_a)
        hits_b = repository_a.vector_candidates(Scope.from_ids([knowledge_base["id"]]), [1.0] + [0.0] * 1023, 10, profile_id=profile_b)

        assert profile_a and profile_b and profile_a != profile_b
        assert [hit.chunk_id for hit in hits_a] == [str(repository_a.list_chunks(first["document_id"])[0]["id"])]
        assert [hit.chunk_id for hit in hits_b] == [str(repository_a.list_chunks(second["document_id"])[0]["id"])]
    finally:
        repository_a.delete_knowledge_base(knowledge_base["id"])


def test_postgres_graph_query_excludes_out_of_scope_deleted_old_and_not_ready_data(tmp_path: Path) -> None:
    engine = _engine_or_skip()
    repository = PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path / "storage"))
    graph_repository = PostgresGraphRepository(engine, repository.storage)
    suffix = tmp_path.name
    knowledge_base = repository.create_knowledge_base(f"graph-scope-{suffix}")

    def ingest(file_name: str, content: bytes) -> tuple[dict[str, object], dict[str, object]]:
        receipt = repository.create_upload(
            knowledge_base["id"],
            file_name,
            "text/markdown",
            repository.storage.put_stream(io.BytesIO(content)),
        )
        assert repository.process_job(receipt["job_id"])["status"] == "succeeded"
        return receipt, repository.get_document(receipt["document_id"]) or {}

    try:
        first, first_document = ingest("first.md", b"# First\n\nfirst content\n\n# Second\n\nsecond content")
        second, second_document = ingest("second.md", b"# Second\n\nsecond content")
        third, third_document = ingest("third.md", b"# Third\n\nthird content")
        GraphService(graph_repository).build(first["document_id"], str(first_document["active_version_id"]))
        GraphService(graph_repository).build(second["document_id"], str(second_document["active_version_id"]))
        GraphService(graph_repository).build(third["document_id"], str(third_document["active_version_id"]))

        with engine.begin() as connection:
            connection.execute(text("UPDATE documents SET deleted_at=clock_timestamp() WHERE id=:id"), {"id": second["document_id"]})
            connection.execute(
                text("UPDATE document_versions SET graph_status='disabled' WHERE id=:id"),
                {"id": third_document["active_version_id"]},
            )

        replacement = repository.create_upload(
            knowledge_base["id"],
            "first.md",
            "text/markdown",
            repository.storage.put_stream(io.BytesIO(b"# Replacement\n\nreplacement content\n\n# End\n\nend content")),
        )
        assert repository.process_job(replacement["job_id"])["status"] == "succeeded"
        current_first = repository.get_document(first["document_id"]) or {}
        GraphService(graph_repository).build(first["document_id"], str(current_first["active_version_id"]))

        rows = graph_repository.query_graph(Scope.from_ids([knowledge_base["id"]]), "replacement")
        scoped_rows = graph_repository.query_graph(Scope.from_ids([knowledge_base["id"]], [first["document_id"]]), "replacement")

        assert rows
        assert {row.document_id for row in rows} == {first["document_id"]}
        assert {row.version_id for row in rows} == {str(current_first["active_version_id"])}
        assert {row.document_id for row in scoped_rows} == {first["document_id"]}
    finally:
        repository.delete_knowledge_base(knowledge_base["id"])
