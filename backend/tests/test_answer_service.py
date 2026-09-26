from typing import Any

from backend.app.application.agent_ports import SmartAgentResult
from backend.app.application.answer_service import AnswerService
from backend.app.domain.agent_policy import AgentStep
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord, EvidenceSnapshot


class RecordingRunStore:
    """In-memory RunEventStore that records the exact event order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.messages: list[tuple[str, str, str]] = []
        self.evidence: list[tuple[str, list[str]]] = []
        self.hits: list[tuple[str, list[str]]] = []
        self.completed: list[tuple[str, str, str | None]] = []
        self._runs = 0

    def get_knowledge_base(self, kb_id: str):
        return {"id": kb_id, "cloud_allowed": False}

    def create_run(self, conversation_id, kb_scope, document_scope, q0) -> str:
        self._runs += 1
        return f"run-{self._runs}"

    def append_event(self, run_id, event_type, payload):
        self.events.append((run_id, event_type, payload))

    def append_message(self, conversation_id, role, content):
        self.messages.append((conversation_id, role, content))

    def persist_retrieval_hits(self, run_id, items):
        self.hits.append((run_id, [item.chunk.chunk_id for item in items]))

    def persist_evidence(self, run_id, snapshots: list[EvidenceSnapshot]):
        self.evidence.append((run_id, [snapshot.label for snapshot in snapshots]))

    def complete_run(self, run_id, status, error_code=None):
        self.completed.append((run_id, status, error_code))

    def event_names(self) -> list[str]:
        return [event for _, event, _ in self.events]


def _conversation():
    return {"id": "conv-1", "knowledge_base_scope": ["kb"], "document_scope": []}


class SmartAgentStub:
    def run(self, conversation_id, question, scope, *, run_id, gateway, evidence, trace_store=None, limits=None):
        tool_result = gateway.invoke("search_knowledge", {"query": question}, scope)
        snapshots = evidence.freeze()
        return SmartAgentResult(
            run_id,
            "completed",
            "基于证据 E1 的智能回答。",
            (AgentStep(1, "search_knowledge", "completed", "question_redacted", f"items={len(tool_result.data['items'])}"),),
            ("E1",),
            snapshots,
        )


def test_quick_answer_records_the_frozen_event_sequence():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "证据片段", {"start": 0}))
    runs = RecordingRunStore()

    outcome = AnswerService(retriever=HybridRetriever(repository), runs=runs).answer(_conversation(), "证据")

    assert outcome.error_code is None
    assert runs.event_names() == ["run.created", "retrieval.started", "retrieval.completed", "evidence.frozen", "answer.completed"]
    assert runs.evidence == [("run-1", ["E1"])]
    assert runs.hits == [("run-1", ["c"])]
    assert runs.completed == [("run-1", "completed", None)]
    assert [role for _, role, _ in runs.messages] == ["user", "assistant"]


def test_no_evidence_run_fails_without_fabricating_an_answer():
    runs = RecordingRunStore()

    outcome = AnswerService(retriever=HybridRetriever(InMemoryRetrievalRepository()), runs=runs).answer(_conversation(), "没有命中")

    assert outcome.error_code == "NO_CANDIDATES"
    assert outcome.answer == ""
    assert runs.event_names() == ["run.created", "retrieval.started", "retrieval.completed", "run.failed"]
    assert runs.evidence == []
    assert all(role != "assistant" for _, role, _ in runs.messages)
    assert runs.completed == [("run-1", "failed", "NO_CANDIDATES")]


def test_unsupported_model_answer_persists_no_citation_or_assistant_message():
    class UnsupportedModel:
        def answer(self, prompt, timeout_seconds):
            return "未经引用的成本是2000元。"

    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "成本1000元。", {"start": 0}))
    runs = RecordingRunStore()

    outcome = AnswerService(retriever=HybridRetriever(repository), runs=runs, answer_gateway=UnsupportedModel()).answer(_conversation(), "成本")

    assert outcome.error_code == "UNSUPPORTED_ANSWER"
    assert outcome.citations == ()
    assert runs.evidence == []
    assert all(role != "assistant" for _, role, _ in runs.messages)


def test_smart_mode_records_a_single_tool_step_through_the_smart_agent_port():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "证据片段", {"start": 0}))
    runs = RecordingRunStore()

    outcome = AnswerService(retriever=HybridRetriever(repository), runs=runs, smart_agent=SmartAgentStub()).answer(_conversation(), "证据", "smart")

    assert outcome.error_code is None
    assert runs.event_names() == [
        "run.created",
        "retrieval.started",
        "tool.started",
        "tool.completed",
        "retrieval.completed",
        "evidence.frozen",
        "answer.completed",
    ]
    assert outcome.trace["mode"] == "smart"
    assert [step["tool_name"] for step in outcome.trace["steps"]] == ["search_knowledge"]


def test_cloud_permission_is_read_for_every_selected_knowledge_base():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "证据片段", {}))
    runs = RecordingRunStore()
    seen: list[str] = []

    def record(kb_id: str):
        seen.append(kb_id)
        return {"id": kb_id, "cloud_allowed": False}

    runs.get_knowledge_base = record  # type: ignore[method-assign]
    conversation = {"id": "conv-1", "knowledge_base_scope": ["kb-a", "kb-b"], "document_scope": []}
    repository.add(ChunkRecord("c2", "kb-a", "doc", "ver", "证据片段", {}))
    repository.add(ChunkRecord("c3", "kb-b", "doc", "ver", "证据片段", {}))

    outcome = AnswerService(retriever=HybridRetriever(repository), runs=runs).answer(conversation, "证据")

    assert outcome.error_code is None
    assert sorted(seen) == ["kb-a", "kb-b"]
