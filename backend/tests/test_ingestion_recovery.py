from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.config import Settings


def _repository_or_skip(tmp_path: Path) -> PostgresKnowledgeRepository:
    database_url = os.getenv("RAG_DATABASE_URL", Settings().database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    return PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path))


def _purge(repository: PostgresKnowledgeRepository, kb_id: str) -> None:
    """Delete every row created for one knowledge base, child-first for FKs."""
    version_filter = "version_id IN (SELECT id FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE knowledge_base_id=:kb))"
    chunk_filter = "chunk_id IN (SELECT id FROM chunks WHERE knowledge_base_id=:kb)"
    with repository.engine.begin() as connection:
        connection.execute(text(f"DELETE FROM chunk_terms WHERE {chunk_filter}"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM chunk_embeddings WHERE {chunk_filter}"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM chunk_assets WHERE {version_filter}"), {"kb": kb_id})
        connection.execute(text("DELETE FROM chunks WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_sections WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_assets WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM ingestion_jobs WHERE {version_filter}"), {"kb": kb_id})
        connection.execute(
            text("DELETE FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE knowledge_base_id=:kb)"),
            {"kb": kb_id},
        )
        connection.execute(text("DELETE FROM documents WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM knowledge_bases WHERE id=:kb"), {"kb": kb_id})


def test_a_failed_new_version_does_not_hide_behind_the_ready_active_version(tmp_path: Path) -> None:
    """Refreshing must still surface the newest ingestion failure.

    The active version stays `ready` (it is still the served one), but the
    document row must expose the newest version's failure so the UI cannot
    silently show the old version as if nothing happened.
    """
    repository = _repository_or_skip(tmp_path)
    suffix = uuid.uuid4().hex[:10]
    knowledge_base = repository.create_knowledge_base(f"recovery-{suffix}")
    kb_id = knowledge_base["id"]
    try:
        first = repository.create_upload(
            kb_id, f"{suffix}.md", "text/markdown", repository.storage.put_stream(io.BytesIO(b"# Title\nFirst fact"))
        )
        repository.process_job(first["job_id"])

        before = repository.list_documents(kb_id)[0]
        assert before["index_status"] == "ready"
        assert before["latest_version_no"] == 1
        assert before["latest_index_status"] == "ready"
        assert before["latest_job"]["status"] == "succeeded"

        # A new immutable version that parses to empty text fails indexing.
        second = repository.create_version(
            first["document_id"], f"{suffix}.md", "text/markdown", repository.storage.put_stream(io.BytesIO(b""))
        )
        repository.process_job(second["job_id"])

        after = repository.list_documents(kb_id)[0]
        assert after["index_status"] == "ready"  # active version 1 is still served
        assert after["latest_version_no"] == 2
        assert after["latest_index_status"] == "failed"
        assert after["latest_job"]["id"] == second["job_id"]
        assert after["latest_job"]["status"] == "failed"
        assert after["latest_job"]["error_code"] == "EMPTY_TEXT"
        assert after["latest_job"]["attempts"] >= 1
        assert after["latest_job"]["max_attempts"] >= after["latest_job"]["attempts"]
    finally:
        _purge(repository, kb_id)
