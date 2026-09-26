from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.config import Settings
from backend.app.domain.scope import Scope


def _repositories_or_skip() -> tuple[PostgresKnowledgeRepository, PostgresAgentRepository]:
    database_url = os.getenv("RAG_DATABASE_URL", Settings().database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    return PostgresKnowledgeRepository(engine, storage=None), PostgresAgentRepository(engine)  # type: ignore[arg-type]


def test_rag_and_agent_terminal_states_are_not_overwritten_after_cancel() -> None:
    rag, agent = _repositories_or_skip()
    rag_run_id = rag.create_run(None, ["kb"], [], "original question")
    agent_run_id = str(uuid.uuid4())
    agent.create_run(agent_run_id, None, "original question", Scope.from_ids(["kb"]))
    try:
        assert rag.cancel_run(rag_run_id) is True
        assert agent.cancel_run(agent_run_id) is True
        rag.complete_run(rag_run_id, "completed")
        agent.complete_run(agent_run_id, "completed", None)

        with rag.engine.connect() as connection:
            rag_status, rag_q0 = connection.execute(text("SELECT status,q0 FROM rag_runs WHERE id=:id"), {"id": rag_run_id}).one()
            agent_status, agent_q0 = connection.execute(text("SELECT status,q0 FROM agent_runs WHERE id=:id"), {"id": agent_run_id}).one()

        assert (rag_status, rag_q0) == ("cancelled", "original question")
        assert (agent_status, agent_q0) == ("cancelled", "original question")
        assert rag.cancel_run(rag_run_id) is False
        assert agent.cancel_run(agent_run_id) is False
    finally:
        with rag.engine.begin() as connection:
            connection.execute(text("DELETE FROM agent_runs WHERE id=:id"), {"id": agent_run_id})
            connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": rag_run_id})
            connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": rag_run_id})


def test_cancelling_rag_run_also_cancels_its_agent_run() -> None:
    rag, agent = _repositories_or_skip()
    run_id = rag.create_run(None, ["kb"], [], "original question")
    agent.create_run(run_id, None, "original question", Scope.from_ids(["kb"]))
    try:
        assert rag.cancel_run(run_id) is True
        with rag.engine.connect() as connection:
            rag_status = connection.execute(text("SELECT status FROM rag_runs WHERE id=:id"), {"id": run_id}).scalar_one()
            agent_status = connection.execute(text("SELECT status FROM agent_runs WHERE id=:id"), {"id": run_id}).scalar_one()
            cancel_events = connection.execute(text("SELECT event_type,payload FROM retrieval_events WHERE run_id=:id ORDER BY seq"), {"id": run_id}).all()
        assert (rag_status, agent_status) == ("cancelled", "cancelled")
        assert cancel_events == [("run.failed", {"error_code": "CANCELLED", "citations": []})]
    finally:
        with rag.engine.begin() as connection:
            connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM agent_runs WHERE id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})


def test_cancelled_run_rejects_late_answer_and_success_artifacts() -> None:
    rag, agent = _repositories_or_skip()
    conversation = rag.create_conversation(["kb"])
    conversation_id = conversation["id"]
    run_id = rag.create_run(conversation_id, ["kb"], [], "original question")
    agent.create_run(run_id, conversation_id, "original question", Scope.from_ids(["kb"]))
    try:
        assert rag.cancel_run(run_id) is True
        committed = rag.finalize_answer(
            run_id=run_id,
            conversation_id=conversation_id,
            answer="late answer [E1]",
            citations=("E1",),
            snapshots=(),
            error_code=None,
            mode="smart",
            agent_terminal=("completed", None, 0),
        )
        assert committed is False
        with rag.engine.connect() as connection:
            statuses = (
                connection.execute(text("SELECT status FROM rag_runs WHERE id=:id"), {"id": run_id}).scalar_one(),
                connection.execute(text("SELECT status FROM agent_runs WHERE id=:id"), {"id": run_id}).scalar_one(),
            )
            assistant_count = connection.execute(text("SELECT count(*) FROM conversation_messages WHERE conversation_id=:id AND role='assistant'"), {"id": conversation_id}).scalar_one()
            success_event_count = connection.execute(text("SELECT count(*) FROM retrieval_events WHERE run_id=:id AND event_type IN ('answer.completed','evidence.frozen')"), {"id": run_id}).scalar_one()
        assert statuses == ("cancelled", "cancelled")
        assert assistant_count == 0
        assert success_event_count == 0
    finally:
        with rag.engine.begin() as connection:
            connection.execute(text("DELETE FROM agent_runs WHERE id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM conversation_messages WHERE conversation_id=:id"), {"id": conversation_id})
            connection.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id})


def test_successful_smart_finalization_commits_both_terminal_rows_and_answer() -> None:
    rag, agent = _repositories_or_skip()
    conversation = rag.create_conversation(["kb"])
    conversation_id = conversation["id"]
    run_id = rag.create_run(conversation_id, ["kb"], [], "original question")
    agent.create_run(run_id, conversation_id, "original question", Scope.from_ids(["kb"]))
    try:
        committed = rag.finalize_answer(
            run_id=run_id,
            conversation_id=conversation_id,
            answer="supported answer",
            citations=(),
            snapshots=(),
            error_code=None,
            mode="smart",
            agent_terminal=("completed", None, 7),
        )
        assert committed is True
        with rag.engine.connect() as connection:
            rag_status = connection.execute(text("SELECT status FROM rag_runs WHERE id=:id"), {"id": run_id}).scalar_one()
            agent_status, cost = connection.execute(text("SELECT status,cost_microunits FROM agent_runs WHERE id=:id"), {"id": run_id}).one()
            assistants = connection.execute(text("SELECT content FROM conversation_messages WHERE conversation_id=:id AND role='assistant'"), {"id": conversation_id}).scalars().all()
            events = connection.execute(text("SELECT event_type FROM retrieval_events WHERE run_id=:id ORDER BY seq"), {"id": run_id}).scalars().all()
        assert (rag_status, agent_status, cost) == ("completed", "completed", 7)
        assert assistants == ["supported answer"]
        assert events == ["retrieval.completed", "answer.completed"]
        assert rag.cancel_run(run_id) is False
    finally:
        with rag.engine.begin() as connection:
            connection.execute(text("DELETE FROM agent_runs WHERE id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})
            connection.execute(text("DELETE FROM conversation_messages WHERE conversation_id=:id"), {"id": conversation_id})
            connection.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id})


def test_concurrent_cancel_and_smart_finalization_commit_one_terminal_outcome() -> None:
    rag, agent = _repositories_or_skip()
    for _ in range(5):
        conversation = rag.create_conversation(["kb"])
        conversation_id = conversation["id"]
        run_id = rag.create_run(conversation_id, ["kb"], [], "original question")
        agent.create_run(run_id, conversation_id, "original question", Scope.from_ids(["kb"]))
        barrier = Barrier(2)

        def cancel() -> bool:
            barrier.wait(timeout=10)
            return rag.cancel_run(run_id)

        def finish() -> bool:
            barrier.wait(timeout=10)
            return rag.finalize_answer(
                run_id=run_id,
                conversation_id=conversation_id,
                answer="supported answer",
                citations=(),
                snapshots=(),
                error_code=None,
                mode="smart",
                agent_terminal=("completed", None, 0),
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                cancelled_future = pool.submit(cancel)
                finished_future = pool.submit(finish)
                cancelled = cancelled_future.result(timeout=15)
                finished = finished_future.result(timeout=15)

            with rag.engine.connect() as connection:
                rag_status = connection.execute(text("SELECT status FROM rag_runs WHERE id=:id"), {"id": run_id}).scalar_one()
                agent_status = connection.execute(text("SELECT status FROM agent_runs WHERE id=:id"), {"id": run_id}).scalar_one()
                events = connection.execute(text("SELECT event_type FROM retrieval_events WHERE run_id=:id ORDER BY seq"), {"id": run_id}).scalars().all()
                assistants = connection.execute(text("SELECT content FROM conversation_messages WHERE conversation_id=:id AND role='assistant'"), {"id": conversation_id}).scalars().all()

            assert cancelled is not finished
            if cancelled:
                assert (rag_status, agent_status) == ("cancelled", "cancelled")
                assert events == ["run.failed"]
                assert assistants == []
            else:
                assert (rag_status, agent_status) == ("completed", "completed")
                assert events == ["retrieval.completed", "answer.completed"]
                assert assistants == ["supported answer"]
        finally:
            with rag.engine.begin() as connection:
                connection.execute(text("DELETE FROM agent_runs WHERE id=:id"), {"id": run_id})
                connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
                connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})
                connection.execute(text("DELETE FROM conversation_messages WHERE conversation_id=:id"), {"id": conversation_id})
                connection.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id})
