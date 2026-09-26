from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.domain.agent_policy import AgentLimits, AgentStep, estimate_tokens
from backend.app.domain.scope import Scope


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    status: str
    answer: str = ""
    steps: tuple[AgentStep, ...] = ()
    citations: tuple[str, ...] = ()
    error_code: str | None = None
    cost_microunits: int = 0


class AgentTraceStore(Protocol):
    def create_run(self, run_id: str, conversation_id: str, question: str, scope: Scope) -> None: ...
    def append_step(self, run_id: str, step: AgentStep) -> None: ...
    def complete_run(self, run_id: str, status: str, error_code: str | None, cost_microunits: int = 0) -> None: ...
    def cancel_run(self, run_id: str) -> None: ...


class AgentRuntime:
    """Bounded, read-only agent that reuses the shared RAG pipeline.

    Smart mode does not reimplement retrieval: it invokes the closed read-tool
    set, then delegates the answer to the same `RAGOrchestrator` quick mode uses.
    """

    def __init__(self, orchestrator: RAGOrchestrator, tools: KnowledgeToolGateway, trace_store: AgentTraceStore | None = None) -> None:
        self.orchestrator = orchestrator
        self.tools = tools
        self.trace_store = trace_store
        self.runs: dict[str, AgentRunResult] = {}

    def run(self, conversation_id: str, question: str, scope: Scope, limits: AgentLimits | None = None, *, run_id: str | None = None) -> AgentRunResult:
        limits = limits or AgentLimits()
        run_id = run_id or str(uuid.uuid4())
        if self.trace_store is not None:
            self.trace_store.create_run(run_id, conversation_id, question, scope)
        if limits.max_steps < 1:
            return self._finish(run_id, AgentRunResult(run_id, "failed", error_code="AGENT_STEP_LIMIT"))

        started = time.perf_counter()
        try:
            tool_result = self.tools.invoke("search_knowledge", {"question": question}, scope)
            step = AgentStep(1, "search_knowledge", "completed", "question_redacted", f"items={len(tool_result.data.get('items', []))}")
            if self.trace_store is not None:
                self.trace_store.append_step(run_id, step)
            if time.perf_counter() - started > limits.max_seconds:
                return self._finish(run_id, AgentRunResult(run_id, "failed", steps=(step,), error_code="AGENT_TIME_LIMIT"))

            answer = self.orchestrator.answer_query(question, scope, RagSettings(local_query_enabled=False), run_id=run_id)
            token_count = estimate_tokens(answer.answer)
            cost_microunits = int(getattr(answer, "cost_microunits", 0) or 0)
            error_code = answer.error_code
            if token_count > limits.max_tokens:
                error_code = "AGENT_TOKEN_LIMIT"
            if cost_microunits > limits.max_cost_microunits:
                error_code = "AGENT_COST_LIMIT"
            if time.perf_counter() - started > limits.max_seconds:
                error_code = "AGENT_TIME_LIMIT"
            return self._finish(
                run_id,
                AgentRunResult(run_id, "completed" if error_code is None else "failed", answer.answer, (step,), answer.citations, error_code, cost_microunits),
            )
        except Exception as exc:
            return self._finish(run_id, AgentRunResult(run_id, "failed", error_code=str(exc)))

    def cancel(self, run_id: str) -> AgentRunResult:
        current = self.runs.get(run_id)
        if current is None:
            current = AgentRunResult(run_id, "cancelled", error_code="CANCELLED")
        elif current.status != "cancelled":
            current = AgentRunResult(current.run_id, "cancelled", current.answer, current.steps, current.citations, "CANCELLED")
        self.runs[run_id] = current
        if self.trace_store is not None:
            self.trace_store.cancel_run(run_id)
        return current

    def _finish(self, run_id: str, result: AgentRunResult) -> AgentRunResult:
        self.runs[run_id] = result
        if self.trace_store is not None:
            self.trace_store.complete_run(run_id, result.status, result.error_code, result.cost_microunits)
        return result
