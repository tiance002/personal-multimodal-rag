from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from typing import Any

from backend.app.application.agent_ports import SmartAgentPort
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.scope import Scope
from backend.app.ports.persistence import RunEventStore
from backend.app.application.run_metrics import collect_metrics, current_metrics
from backend.app.application.follow_up import resolve_follow_up


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
    """Unified run, cancellation and final-commit boundary for both modes.

    Quick and Smart are execution modes over one KnowledgeGateway/RAG Core:
    Quick uses a fixed LangChain Runnable sequence, while Smart uses the
    LangChain Agent adapter and the same retrieval/evidence/validation services.
    """

    def __init__(
        self,
        *,
        knowledge_gateway: KnowledgeGateway | None = None,
        quick_chain: LangChainQuickChain | None = None,
        retriever: HybridRetriever | None = None,
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
        observability: Any | None = None,
    ) -> None:
        if knowledge_gateway is None:
            if retriever is None:
                raise ValueError("knowledge_gateway or retriever is required")
            knowledge_gateway = KnowledgeGateway(retriever)
        self.knowledge_gateway = knowledge_gateway
        self.runs = runs
        self.local_query_gateway = local_query_gateway
        self.budget_gate = budget_gate
        self.graph_query = graph_query
        self.agent_trace_store = agent_trace_store
        self.content_reader = content_reader
        self.document_lister = document_lister
        self.document_resolver = document_resolver
        self.smart_agent = smart_agent
        self.local_query_enabled = local_query_enabled
        self.observability = observability
        self.quick_chain = quick_chain or LangChainQuickChain(
            knowledge_gateway,
            answer_gateway=answer_gateway,
            budget_gate=budget_gate,
        )

    def answer(self, conversation: dict[str, Any], content: str, mode: str = "quick") -> AnswerOutcome:
        with collect_metrics("pending") as metrics:
            outcome = self._answer(conversation, content, mode)
            metrics.query_id = outcome.run_id
            row = metrics.snapshot(citations=outcome.citations, error=outcome.error_code)
            return replace(outcome, trace={**outcome.trace, "metrics": row})

    def _answer(self, conversation: dict[str, Any], content: str, mode: str = "quick") -> AnswerOutcome:
        kb_scope = list(conversation["knowledge_base_scope"])
        document_scope = list(conversation["document_scope"] or [])
        conversation_id = str(conversation["id"])

        run_id = self.runs.create_run(conversation_id, kb_scope, document_scope, content)
        if (metrics := current_metrics()) is not None:
            metrics.query_id = run_id
        self.runs.append_event(run_id, "run.created", {"run_id": run_id})
        self.runs.append_message(conversation_id, "user", content)
        self.runs.append_event(run_id, "retrieval.started", {"scope": kb_scope, "document_scope": document_scope})

        cloud_allowed_by_kb = {
            kb_id: bool((self.runs.get_knowledge_base(kb_id) or {}).get("cloud_allowed", False))
            for kb_id in kb_scope
        }
        scope = Scope.from_ids(kb_scope, document_scope)
        previous_reader = getattr(self.runs, "last_completed_question", None)
        previous = previous_reader(conversation_id, kb_scope, document_scope) if previous_reader is not None else None
        execution_question, follow_up_used = resolve_follow_up(content, previous)

        if self._is_cancelled(run_id):
            return self._cancelled_outcome(run_id, mode)

        trace_context = (
            self.observability.trace(
                run_id=run_id,
                conversation_id=conversation_id,
                mode=mode,
                question=content,
                knowledge_base_ids=kb_scope,
                cloud_allowed_by_kb=cloud_allowed_by_kb,
            )
            if self.observability is not None
            else nullcontext(None)
        )
        with trace_context as langfuse_run:
            smart_evidence: tuple[Any, ...] = ()
            agent_terminal: tuple[str, str | None, int] | None = None
            privacy_evidence_only = self.quick_chain._privacy_configuration(execution_question)
            if mode == "smart" and not privacy_evidence_only:
                answer, citations, error_code, trace, smart_evidence, agent_terminal = self._run_smart(
                    conversation_id,
                    execution_question,
                    scope,
                    run_id,
                )
                snapshots: tuple[Any, ...] = smart_evidence
            else:
                result = self.quick_chain.invoke(
                    execution_question,
                    scope,
                    settings=QuickSettings(
                        local_query_enabled=self.local_query_enabled and not privacy_evidence_only,
                    ),
                    run_id=run_id,
                    cloud_allowed_by_kb=cloud_allowed_by_kb,
                    local_query_gateway=self.local_query_gateway,
                    on_retrieval=self.runs.persist_retrieval_hits,
                )
                answer, citations, error_code = result.answer, result.citations, result.error_code
                trace = dict(result.trace.__dict__)
                snapshots = result.evidence
                if privacy_evidence_only:
                    trace.update(requested_mode=mode, execution_mode="evidence_only")

            if error_code == "CANCELLED" or self._is_cancelled(run_id):
                if langfuse_run is not None:
                    langfuse_run.finish(answer="", citations=(), error_code="CANCELLED", run_trace=trace)
                return self._cancelled_outcome(run_id, mode, trace)

            trace["follow_up_context_used"] = follow_up_used
            if (metrics := current_metrics()) is not None:
                self.runs.append_event(run_id, "run.metrics", metrics.snapshot(citations=citations, error=error_code))
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
                    if langfuse_run is not None:
                        langfuse_run.finish(answer="", citations=(), error_code="CANCELLED", run_trace=trace)
                    return self._cancelled_outcome(run_id, mode, trace)
                outcome = AnswerOutcome(run_id, answer, tuple(citations), error_code, trace)
                if langfuse_run is not None:
                    langfuse_run.finish(answer=answer, citations=tuple(citations), error_code=error_code, run_trace=trace)
                return outcome

            self.runs.append_event(
                run_id,
                "retrieval.completed",
                {"count": len(snapshots), "error_code": error_code, "mode": mode},
            )
            if snapshots and error_code is None:
                self.runs.persist_evidence(run_id, snapshots)
                self.runs.append_event(run_id, "evidence.frozen", {"labels": [snapshot.label for snapshot in snapshots]})
            self.runs.append_event(
                run_id,
                "answer.completed" if error_code is None else "run.failed",
                {"error_code": error_code, "citations": list(citations)},
            )
            if self.runs.complete_run(run_id, "completed" if error_code is None else "failed", error_code) is False:
                if langfuse_run is not None:
                    langfuse_run.finish(answer="", citations=(), error_code="CANCELLED", run_trace=trace)
                return self._cancelled_outcome(run_id, mode, trace)
            if error_code is None:
                self.runs.append_message(conversation_id, "assistant", answer)
            outcome = AnswerOutcome(run_id, answer, tuple(citations), error_code, trace)
            if langfuse_run is not None:
                langfuse_run.finish(answer=answer, citations=tuple(citations), error_code=error_code, run_trace=trace)
            return outcome

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

    def _graph_enabled_for_scope(self, scope: Scope) -> bool:
        """Expose graph tools only when every selected KB explicitly opts in."""
        getter = getattr(self.runs, "get_knowledge_base", None)
        if getter is None or not scope.knowledge_base_ids:
            return False
        return all(bool((getter(kb_id) or {}).get("graph_enabled", False)) for kb_id in scope.knowledge_base_ids)

    def _run_smart(
        self,
        conversation_id: str,
        content: str,
        scope: Scope,
        run_id: str,
    ) -> tuple[str, tuple[str, ...], str | None, dict[str, Any], tuple[Any, ...], tuple[str, str | None, int] | None]:
        from backend.app.application.evidence_accumulator import EvidenceAccumulator

        evidence = EvidenceAccumulator()
        graph_enabled = self._graph_enabled_for_scope(scope)
        gateway = KnowledgeToolGateway(
            knowledge_gateway=self.knowledge_gateway,
            content_reader=self.content_reader,
            document_lister=self.document_lister,
            document_resolver=self.document_resolver,
            graph_query=self.graph_query if graph_enabled else None,
            evidence_accumulator=evidence,
            on_retrieval=lambda items: self.runs.persist_retrieval_hits(run_id, items),
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
            graph_enabled=graph_enabled,
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
        return (
            agent_result.answer,
            tuple(agent_result.citations),
            agent_result.error_code,
            trace,
            tuple(agent_result.evidence),
            deferred_trace.terminal if deferred_trace is not None else None,
        )


__all__ = ["AnswerOutcome", "AnswerService"]
