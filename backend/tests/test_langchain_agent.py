from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.agent_policy import ALLOWED_READ_TOOLS
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


class ScriptedChatModel(BaseChatModel):
    calls: int = 0
    bound_names: list[str] = []
    final_answer: str = "基于证据回答 [chunk-1]"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_names = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": "question"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content=self.final_answer)
        return ChatResult(generations=[ChatGeneration(message=message)])


class TraceStore:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.created: list[str] = []
        self.steps: list[Any] = []
        self.completed: list[tuple[str, str, str | None]] = []

    def create_run(self, run_id, conversation_id, question, scope):
        self.created.append(run_id)

    def is_cancelled(self, run_id: str) -> bool:
        return self.cancelled

    def append_step(self, run_id, step):
        self.steps.append(step)

    def complete_run(self, run_id, status, error_code, cost_microunits=0):
        self.completed.append((run_id, status, error_code))


def _gateway() -> KnowledgeToolGateway:
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("chunk-1", "kb", "doc", "version", "question server evidence", {"start": 0}))
    return KnowledgeToolGateway(retriever=HybridRetriever(repository))


def test_langchain_adapter_hides_graph_tool_by_default_and_freezes_evidence() -> None:
    model = ScriptedChatModel()
    adapter = LangChainAgentAdapter(model)
    base_gateway = _gateway()
    evidence = EvidenceAccumulator()
    result = adapter.run(
        "conversation",
        "question",
        Scope.from_ids(["kb"]),
        run_id="run-1",
        gateway=KnowledgeToolGateway(retriever=base_gateway.retriever, evidence_accumulator=evidence),
        evidence=evidence,
        trace_store=TraceStore(),
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.answer.endswith("[E1]")
    assert result.citations == ("E1",)
    assert result.model_calls == 2
    assert [snapshot.chunk_id for snapshot in result.evidence] == ["chunk-1"]
    assert set(model.bound_names) == set(ALLOWED_READ_TOOLS - {"query_knowledge_graph"})
    schema_keys = set().union(*(schema.keys() for schema in adapter.last_tool_schemas))
    assert not {"knowledge_base_id", "version_id", "scope", "run_id", "cloud_allowed"} & schema_keys


def test_langchain_adapter_registers_graph_tool_only_when_scope_allows_it() -> None:
    model = ScriptedChatModel()
    adapter = LangChainAgentAdapter(model)
    base_gateway = _gateway()
    evidence = EvidenceAccumulator()
    result = adapter.run(
        "conversation",
        "question",
        Scope.from_ids(["kb"]),
        run_id="run-graph",
        gateway=KnowledgeToolGateway(
            retriever=base_gateway.retriever,
            graph_query=lambda scope, entity_name, depth: [],
            evidence_accumulator=evidence,
        ),
        evidence=evidence,
        trace_store=TraceStore(),
        graph_enabled=True,
    )

    assert result.status == "completed"
    assert set(model.bound_names) == set(ALLOWED_READ_TOOLS)


def test_langchain_adapter_honors_persistent_cancellation_before_model() -> None:
    trace = TraceStore(cancelled=True)
    model = ScriptedChatModel()
    adapter = LangChainAgentAdapter(model)
    evidence = EvidenceAccumulator()

    result = adapter.run(
        "conversation",
        "question",
        Scope.from_ids(["kb"]),
        run_id="run-2",
        gateway=_gateway(),
        evidence=evidence,
        trace_store=trace,
    )

    assert result.status == "cancelled"
    assert result.error_code == "CANCELLED"
    assert model.calls == 0
    assert trace.completed == [("run-2", "cancelled", "CANCELLED")]


def test_smart_answer_without_reference_is_not_auto_cited() -> None:
    model = ScriptedChatModel(final_answer="这是一个没有引用的事实回答")
    evidence = EvidenceAccumulator()
    result = LangChainAgentAdapter(model).run(
        "conversation", "question", Scope.from_ids(["kb"]), run_id="run-3",
        gateway=KnowledgeToolGateway(retriever=_gateway().retriever, evidence_accumulator=evidence),
        evidence=evidence, trace_store=TraceStore(),
    )

    assert result.status == "failed"
    assert result.error_code == "UNSUPPORTED_ANSWER"
    assert result.citations == ()


def test_smart_compound_question_rejects_missing_target() -> None:
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("chunk-1", "kb", "doc", "v", "A方案成本1000元。 question", {"start": 0}))
    evidence = EvidenceAccumulator()
    result = LangChainAgentAdapter(ScriptedChatModel(final_answer="A方案成本1000元 [chunk-1]；B方案成本2000元 [chunk-1]")).run(
        "conversation", "A 与 B 两种方案各自的成本是多少？", Scope.from_ids(["kb"]), run_id="run-4",
        gateway=KnowledgeToolGateway(retriever=HybridRetriever(repository), evidence_accumulator=evidence),
        evidence=evidence, trace_store=TraceStore(),
    )

    assert result.status == "failed"
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
