from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.app.application.agent_ports import SmartAgentPort
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.evidence_accumulator import EvidenceAccumulator
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


class _DeferredAgentTraceStore:
    """Keep Agent trace writes live, but commit its terminal row with the RAG run."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.terminal: tuple[str, str | None, int] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)

    def complete_run(self, run_id: str, status: str, error_code: str | None, cost_microunits: int = 0) -> bool:
        self.terminal = (status, error_code, cost_microunits)
        return True


class AnswerService:
    """Use case: answer one conversation message and record the whole run trace.

    Quick mode calls `RAGOrchestrator` directly. Smart mode delegates tool
    selection and the execution loop to the injected LangChain adapter; its
    tools use the same RAG Core retriever and evidence boundaries.
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
        document_resolver: Callable[[str], dict[str, Any] | None] | None = None,
        smart_agent: SmartAgentPort | None = None,
        local_query_enabled: bool = False,
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
        self.document_resolver = document_resolver
        self.smart_agent = smart_agent
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

        if self._is_cancelled(run_id):
            return self._cancelled_outcome(run_id, mode)

        smart_evidence = ()
        agent_terminal: tuple[str, str | None, int] | None = None
        if mode == "smart":
            answer, citations, error_code, trace, smart_evidence, agent_terminal = self._run_smart(conversation_id, content, scope, run_id)
        else:
            result = orchestrator.answer_query(content, scope, RagSettings(local_query_enabled=self.local_query_enabled), run_id=run_id)
            answer, citations, error_code = result.answer, result.citations, result.error_code
            trace = dict(result.trace.__dict__)

        if error_code == "CANCELLED" or self._is_cancelled(run_id):
            return self._cancelled_outcome(run_id, mode, trace)

        snapshots: tuple[Any, ...] = smart_evidence
        if not snapshots and citation_service.snapshots and error_code is None:
            snapshots = tuple(citation_service.snapshots[(run_id, label)] for label in citations)

        finalizer = getattr(self.runs, "finalize_answer", None)
        if finalizer is not None:
            committed = finalizer(
                run_id=run_id,
                conversation_id=conversation_id,
                answer=answer,
                citations=tuple(citations),
                snapshots=snapshots,
                error_code=error_code,
                mode=mode,
                agent_terminal=agent_terminal,
            )
            if not committed:
                return self._cancelled_outcome(run_id, mode, trace)
            return AnswerOutcome(run_id, answer, tuple(citations), error_code, trace)

        self.runs.append_event(run_id, "retrieval.completed", {"count": len(citations), "error_code": error_code, "mode": mode})
        if snapshots and error_code is None:
            self.runs.persist_evidence(run_id, snapshots)
            self.runs.append_event(run_id, "evidence.frozen", {"labels": [snapshot.label for snapshot in snapshots]})
        self.runs.append_event(run_id, "answer.completed" if error_code is None else "run.failed", {"error_code": error_code, "citations": list(citations)})
        if self.runs.complete_run(run_id, "completed" if error_code is None else "failed", error_code) is False:
            return self._cancelled_outcome(run_id, mode, trace)
        if error_code is None:
            self.runs.append_message(conversation_id, "assistant", answer)
        return AnswerOutcome(run_id, answer, tuple(citations), error_code, trace)

    def _is_cancelled(self, run_id: str) -> bool:
        checker = getattr(self.runs, "is_cancelled", None)
        return bool(checker(run_id)) if checker is not None else False

    def _cancelled_outcome(self, run_id: str, mode: str, trace: dict[str, Any] | None = None) -> AnswerOutcome:
        cancelled_trace = dict(trace or {})
        cancelled_trace["mode"] = mode
        cancelled_trace["error_code"] = "CANCELLED"
        cancel = getattr(self.runs, "cancel_run", None)
        if cancel is not None:
            cancel(run_id)
        else:
            self.runs.append_event(run_id, "run.failed", {"error_code": "CANCELLED", "citations": []})
            self.runs.complete_run(run_id, "cancelled", "CANCELLED")
        return AnswerOutcome(run_id, "", (), "CANCELLED", cancelled_trace)

    def _run_smart(self, conversation_id: str, content: str, scope: Scope, run_id: str) -> tuple[str, tuple[str, ...], str | None, dict[str, Any], tuple[Any, ...], tuple[str, str | None, int] | None]:
        evidence = EvidenceAccumulator()
        gateway = KnowledgeToolGateway(
            retriever=self.retriever,
            content_reader=self.content_reader,
            document_lister=self.document_lister,
            document_resolver=self.document_resolver,
            graph_query=self.graph_query,
            evidence_accumulator=evidence,
        )
        self.runs.append_event(run_id, "tool.started", {"tool": "search_knowledge"})
        if self.smart_agent is None:
            return "", (), "SMART_AGENT_UNAVAILABLE", {"mode": "smart", "steps": []}, (), None
        deferred_trace = _DeferredAgentTraceStore(self.agent_trace_store) if self.agent_trace_store is not None else None
        agent_result = self.smart_agent.run(
            conversation_id,
            content,
            scope,
            run_id=run_id,
            gateway=gateway,
            evidence=evidence,
            trace_store=deferred_trace,
        )
        self.runs.append_event(
            run_id,
            "tool.completed" if agent_result.error_code is None else "tool.failed",
            {"tool": "search_knowledge", "status": agent_result.status},
        )
        trace = {
            "mode": "smart",
            "steps": [step.__dict__ for step in agent_result.steps],
            "cost_microunits": agent_result.cost_microunits,
            "model_calls": agent_result.model_calls,
        }
        return agent_result.answer, tuple(agent_result.citations), agent_result.error_code, trace, tuple(agent_result.evidence), deferred_trace.terminal if deferred_trace is not None else None


__all__ = ["AnswerOutcome", "AnswerService"]
