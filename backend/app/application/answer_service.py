from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.app.application.agent_runtime import AgentRuntime
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.scope import Scope
from backend.app.ports.persistence import RunEventStore


@dataclass(frozen=True)
class AnswerOutcome:
    run_id: str
    answer: str
    citations: tuple[str, ...]
    error_code: str | None
    trace: dict[str, Any]


class AnswerService:
    """Use case: answer one conversation message and record the whole run trace.

    Quick and smart mode share a single `RAGOrchestrator`; smart mode only adds
    the closed read-tool step in front of it and never reimplements retrieval.
    The SSE event order is part of the frozen contract, so it is owned here
    instead of by the HTTP layer.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        runs: RunEventStore,
        local_query_gateway: Any | None = None,
        answer_gateway: Any | None = None,
        budget_gate: Any | None = None,
        graph_query: Callable[[Scope, str, int], Any] | None = None,
        agent_trace_store: Any | None = None,
        content_reader: Callable[[str], str] | None = None,
        document_lister: Callable[[str], list[dict[str, Any]]] | None = None,
        local_query_enabled: bool = True,
    ) -> None:
        self.retriever = retriever
        self.runs = runs
        self.local_query_gateway = local_query_gateway
        self.answer_gateway = answer_gateway
        self.budget_gate = budget_gate
        self.graph_query = graph_query
        self.agent_trace_store = agent_trace_store
        self.content_reader = content_reader
        self.document_lister = document_lister
        self.local_query_enabled = local_query_enabled

    def answer(self, conversation: dict[str, Any], content: str, mode: str = "quick") -> AnswerOutcome:
        kb_scope = list(conversation["knowledge_base_scope"])
        document_scope = list(conversation["document_scope"] or [])
        conversation_id = str(conversation["id"])

        run_id = self.runs.create_run(conversation_id, kb_scope, document_scope, content)
        self.runs.append_event(run_id, "run.created", {"run_id": run_id})
        self.runs.append_message(conversation_id, "user", content)
        self.runs.append_event(run_id, "retrieval.started", {"scope": kb_scope, "document_scope": document_scope})

        citation_service = CitationService(InMemoryCitationStore())
        cloud_allowed_by_kb = {
            kb_id: bool((self.runs.get_knowledge_base(kb_id) or {}).get("cloud_allowed", False))
            for kb_id in kb_scope
        }
        orchestrator = RAGOrchestrator(
            self.retriever,
            citation_service,
            local_query_gateway=self.local_query_gateway,
            answer_gateway=self.answer_gateway,
            cloud_allowed_by_kb=cloud_allowed_by_kb,
            budget_gate=self.budget_gate,
            on_retrieval=self.runs.persist_retrieval_hits,
        )
        scope = Scope.from_ids(kb_scope, document_scope)

        if mode == "smart":
            answer, citations, error_code, trace = self._run_smart(orchestrator, conversation_id, content, scope, run_id)
        else:
            result = orchestrator.answer_query(content, scope, RagSettings(local_query_enabled=self.local_query_enabled), run_id=run_id)
            answer, citations, error_code = result.answer, result.citations, result.error_code
            trace = dict(result.trace.__dict__)

        self.runs.append_event(run_id, "retrieval.completed", {"count": len(citations), "error_code": error_code, "mode": mode})
        if citation_service.snapshots:
            snapshots = list(citation_service.snapshots.values())
            self.runs.persist_evidence(run_id, snapshots)
            self.runs.append_event(run_id, "evidence.frozen", {"labels": [snapshot.label for snapshot in snapshots]})
        self.runs.append_event(run_id, "answer.completed" if error_code is None else "run.failed", {"error_code": error_code, "citations": list(citations)})
        self.runs.complete_run(run_id, "completed" if error_code is None else "failed", error_code)
        if error_code is None:
            self.runs.append_message(conversation_id, "assistant", answer)
        return AnswerOutcome(run_id, answer, tuple(citations), error_code, trace)

    def _run_smart(self, orchestrator: RAGOrchestrator, conversation_id: str, content: str, scope: Scope, run_id: str) -> tuple[str, tuple[str, ...], str | None, dict[str, Any]]:
        gateway = KnowledgeToolGateway(
            retriever=self.retriever,
            content_reader=self.content_reader,
            document_lister=self.document_lister,
            graph_query=self.graph_query,
        )
        self.runs.append_event(run_id, "tool.started", {"tool": "search_knowledge"})
        agent_result = AgentRuntime(orchestrator, gateway, self.agent_trace_store).run(conversation_id, content, scope, run_id=run_id)
        self.runs.append_event(
            run_id,
            "tool.completed" if agent_result.error_code is None else "tool.failed",
            {"tool": "search_knowledge", "status": agent_result.status},
        )
        trace = {
            "mode": "smart",
            "steps": [step.__dict__ for step in agent_result.steps],
            "cost_microunits": agent_result.cost_microunits,
        }
        return agent_result.answer, tuple(agent_result.citations), agent_result.error_code, trace


__all__ = ["AnswerOutcome", "AnswerService"]
