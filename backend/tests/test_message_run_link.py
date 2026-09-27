from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.config import Settings


def _repository_or_skip() -> PostgresKnowledgeRepository:
    database_url = os.getenv("RAG_DATABASE_URL", Settings().database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    return PostgresKnowledgeRepository(engine, storage=None)  # type: ignore[arg-type]


def test_reopened_conversation_keeps_run_link_and_citation_labels() -> None:
    """History readback must re-expose the same run_id and citation labels.

    The link is stored explicitly (`conversation_messages.run_id`); labels are
    read back from the frozen `answer.completed` event, never inferred from the
    answer text.
    """
    repository = _repository_or_skip()
    conversation = repository.create_conversation(["kb"])
    conversation_id = conversation["id"]
    run_id = repository.create_run(conversation_id, ["kb"], [], "what is the cost?")
    try:
        committed = repository.finalize_answer(
            run_id=run_id,
            conversation_id=conversation_id,
            answer="成本是 1000 元 [E1][E2]。",
            citations=("E1", "E2"),
            snapshots=(),
            error_code=None,
            mode="quick",
        )
        assert committed is True

        messages = repository.list_messages(conversation_id)
        assistant = next(message for message in messages if message["role"] == "assistant")
        assert assistant["run_id"] == run_id
        assert assistant["citations"] == ["E1", "E2"]

        # The link is authoritative and survives a fresh read of the same row.
        assert all(message["run_id"] is None for message in messages if message["role"] != "assistant")
    finally:
        with repository.engine.begin() as connection:
            connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM conversation_messages WHERE conversation_id=:id"), {"id": conversation_id})
            connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id})
