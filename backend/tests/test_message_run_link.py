from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.config import Settings
from backend.app.domain.evidence import freeze_evidence
from backend.app.main import create_app


def _repository_or_skip(tmp_path: Path) -> PostgresKnowledgeRepository:
    database_url = os.getenv("RAG_DATABASE_URL", Settings().database_url)
    engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    return PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path))


def _purge_test_data(repository: PostgresKnowledgeRepository, kb_id: str, conversation_id: str, run_id: str) -> None:
    version_filter = "version_id IN (SELECT id FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE knowledge_base_id=:kb))"
    chunk_filter = "chunk_id IN (SELECT id FROM chunks WHERE knowledge_base_id=:kb)"
    with repository.engine.begin() as connection:
        connection.execute(text("DELETE FROM answer_evidence WHERE run_id=:id"), {"id": run_id})
        connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
        connection.execute(text("DELETE FROM conversation_messages WHERE conversation_id=:id"), {"id": conversation_id})
        connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})
        connection.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id})
        connection.execute(text(f"DELETE FROM chunk_terms WHERE {chunk_filter}"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM chunk_embeddings WHERE {chunk_filter}"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM chunk_assets WHERE {version_filter}"), {"kb": kb_id})
        connection.execute(text("DELETE FROM chunks WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_sections WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_assets WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM ingestion_jobs WHERE {version_filter}"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE knowledge_base_id=:kb)"), {"kb": kb_id})
        connection.execute(text("DELETE FROM documents WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM knowledge_bases WHERE id=:kb"), {"kb": kb_id})


def test_reopened_conversation_reads_real_frozen_e1_and_leaves_legacy_null_run_unlinked(tmp_path: Path) -> None:
    """A fresh repository must read E1 from its actual chunk/version/locator.

    An older assistant row with run_id NULL and an E1-looking answer must
    remain unlinked even when a newer run in the same conversation has E1.
    """
    repository = _repository_or_skip(tmp_path)
    suffix = uuid.uuid4().hex[:10]
    kb_id = repository.create_knowledge_base(f"history-e1-{suffix}")["id"]
    conversation_id = ""
    run_id = ""
    try:
        receipt = repository.create_upload(
            kb_id,
            f"{suffix}.md",
            "text/markdown",
            repository.storage.put_stream(io.BytesIO(b"# Budget\nAX-731 costs 1000 units.")),
        )
        repository.process_job(receipt["job_id"])
        with repository.engine.connect() as connection:
            chunk = connection.execute(
                text("SELECT id,version_id,content,locator FROM chunks WHERE document_id=:id ORDER BY chunk_index LIMIT 1"),
                {"id": receipt["document_id"]},
            ).mappings().one()
        quote = chunk["content"]
        snapshot = freeze_evidence("E1", str(chunk["version_id"]), str(chunk["id"]), quote, chunk["locator"])

        conversation_id = repository.create_conversation([kb_id])["id"]
        repository.append_message(conversation_id, "user", "旧问题")
        repository.append_message(conversation_id, "assistant", "迁移前旧回答 [E1]。", run_id=None)
        run_id = repository.create_run(conversation_id, [kb_id], [], "what is the cost?")
        assert repository.finalize_answer(
            run_id=run_id,
            conversation_id=conversation_id,
            answer="AX-731 costs 1000 units [E1].",
            citations=("E1",),
            snapshots=(snapshot,),
            error_code=None,
            mode="quick",
        ) is True

        # Recreate the read-side adapter as a page refresh would do.
        fresh_engine = create_engine(os.getenv("RAG_DATABASE_URL", Settings().database_url), pool_pre_ping=True)
        try:
            fresh_repository = PostgresKnowledgeRepository(fresh_engine, repository.storage)
            messages = fresh_repository.list_messages(conversation_id)
            legacy = next(message for message in messages if message["content"] == "迁移前旧回答 [E1]。")
            assistant = next(message for message in messages if message["run_id"] == run_id)
            citation = fresh_repository.get_citation(assistant["run_id"], assistant["citations"][0])
            settings = Settings()
            with TestClient(create_app(settings, container=SimpleNamespace(settings=settings, store=fresh_repository))) as client:
                history_response = client.get(f"/api/v1/conversations/{conversation_id}/messages")
                citation_response = client.get(f"/api/v1/runs/{run_id}/citations/E1")
        finally:
            fresh_engine.dispose()

        assert legacy["run_id"] is None
        assert legacy["citations"] == []
        assert assistant["citations"] == ["E1"]
        assert citation is not None
        assert citation["quote"] == quote
        assert citation["chunk_id"] == str(chunk["id"])
        assert citation["version_id"] == str(chunk["version_id"])
        assert citation["locator"] == chunk["locator"]
        assert citation["current_status"] == "current"
        assert history_response.status_code == 200
        assert next(message for message in history_response.json()["data"] if message["run_id"] == run_id)["citations"] == ["E1"]
        assert citation_response.status_code == 200
        assert citation_response.json()["data"]["chunk_id"] == str(chunk["id"])
        assert citation_response.json()["data"]["version_id"] == str(chunk["version_id"])
        assert citation_response.json()["data"]["locator"] == chunk["locator"]
        assert all(message["run_id"] is None for message in messages if message["role"] == "user")
    finally:
        if conversation_id and run_id:
            _purge_test_data(repository, kb_id, conversation_id, run_id)
        repository.engine.dispose()
