from __future__ import annotations

from typing import Any

from backend.app.application.answer_service import AnswerService
from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


class PersistentCancellationTrace:
    def __init__(self) -> None:
        self.cancelled = True
        self.completed: list[tuple[str, str, str | None]] = []
        self.steps: list[Any] = []

    def create_run(self, run_id, conversation_id, question, scope):
        return None

    def is_cancelled(self, run_id: str) -> bool:
        return self.cancelled

    def append_step(self, run_id, step):
        self.steps.append(step)

    def complete_run(self, run_id, status, error_code, cost_microunits=0):
        self.completed.append((run_id, status, error_code))

    def cancel_run(self, run_id):
        self.cancelled = True
        return True


def test_agent_checks_persistent_cancellation_before_first_tool() -> None:
    trace = PersistentCancellationTrace()
    runtime = LangChainAgentAdapter(None)
    evidence = EvidenceAccumulator()

    result = runtime.run(
        "conversation",
        "question",
        Scope.from_ids(["kb"]),
        run_id="run-1",
        gateway=KnowledgeToolGateway(),
        evidence=evidence,
        trace_store=trace,
    )

    assert result.status == "cancelled"
    assert result.error_code == "CANCELLED"
    assert trace.steps == []
    assert trace.completed == [("run-1", "cancelled", "CANCELLED")]


class CancelBeforeCommitStore:
    def __init__(self) -> None:
        self.checks = 0
        self.events: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.completed: list[tuple[str, str, str | None]] = []

    def get_knowledge_base(self, kb_id):
        return {"id": kb_id, "cloud_allowed": False}

    def create_run(self, conversation_id, kb_scope, document_scope, q0):
        return "run-1"

    def is_cancelled(self, run_id: str) -> bool:
        self.checks += 1
        return self.checks >= 2

    def append_event(self, run_id, event_type, payload):
        self.events.append(event_type)

    def append_message(self, conversation_id, role, content):
        self.messages.append((role, content))

    def persist_retrieval_hits(self, run_id, items):
        return None

    def persist_evidence(self, run_id, snapshots):
        raise AssertionError("cancelled run must not freeze evidence")

    def complete_run(self, run_id, status, error_code=None):
        self.completed.append((run_id, status, error_code))


def test_answer_drops_late_model_result_after_persistent_cancellation() -> None:
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("chunk", "kb", "doc", "version", "evidence", {"start": 0}))
    runs = CancelBeforeCommitStore()
    service = AnswerService(retriever=HybridRetriever(repository), runs=runs)

    outcome = service.answer({"id": "conversation", "knowledge_base_scope": ["kb"], "document_scope": []}, "question")

    assert outcome.error_code == "CANCELLED"
    assert "answer.completed" not in runs.events
    assert "evidence.frozen" not in runs.events
    assert all(role != "assistant" for role, _ in runs.messages)
    assert runs.completed == [("run-1", "cancelled", "CANCELLED")]


class CancelAtFinalizeStore:
    def __init__(self) -> None:
        self.cancelled = False
        self.events: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.evidence: list[str] = []

    def get_knowledge_base(self, kb_id):
        return {"id": kb_id, "cloud_allowed": False}

    def create_run(self, conversation_id, kb_scope, document_scope, q0):
        return "run-1"

    def is_cancelled(self, run_id):
        return self.cancelled

    def append_event(self, run_id, event_type, payload):
        self.events.append(event_type)

    def append_message(self, conversation_id, role, content):
        self.messages.append((role, content))

    def persist_retrieval_hits(self, run_id, items):
        return None

    def persist_evidence(self, run_id, snapshots):
        self.evidence.extend(snapshot.label for snapshot in snapshots)

    def complete_run(self, run_id, status, error_code=None):
        return False

    def finalize_answer(self, **kwargs):
        self.cancelled = True
        return False


def test_answer_does_not_emit_success_when_cancel_wins_final_commit() -> None:
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("chunk", "kb", "doc", "version", "evidence", {"start": 0}))
    runs = CancelAtFinalizeStore()

    outcome = AnswerService(retriever=HybridRetriever(repository), runs=runs).answer(
        {"id": "conversation", "knowledge_base_scope": ["kb"], "document_scope": []}, "evidence"
    )

    assert outcome.error_code == "CANCELLED"
    assert outcome.answer == ""
    assert runs.evidence == []
    assert "answer.completed" not in runs.events
    assert all(role != "assistant" for role, _ in runs.messages)


class ExternalCancellationStore(CancelBeforeCommitStore):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    def is_cancelled(self, run_id: str) -> bool:
        self.checks += 1
        if self.checks == 2:
            self.cancel_run(run_id)
        return self.cancelled

    def cancel_run(self, run_id: str) -> bool:
        if self.cancelled:
            return False
        self.cancelled = True
        self.events.append("run.failed")
        return True


def test_external_cancellation_emits_terminal_event_once() -> None:
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("chunk", "kb", "doc", "version", "evidence", {"start": 0}))
    runs = ExternalCancellationStore()

    outcome = AnswerService(retriever=HybridRetriever(repository), runs=runs).answer(
        {"id": "conversation", "knowledge_base_scope": ["kb"], "document_scope": []}, "evidence"
    )

    assert outcome.error_code == "CANCELLED"
    assert runs.events.count("run.failed") == 1
    assert "answer.completed" not in runs.events
