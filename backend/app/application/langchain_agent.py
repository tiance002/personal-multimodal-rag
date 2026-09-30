from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from backend.app.application.agent_ports import SmartAgentResult
from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.model_usage import call_stage, record_call
from backend.app.application.run_metrics import current_metrics
from backend.app.domain.agent_policy import ALLOWED_READ_TOOLS, AgentLimits, AgentStep, estimate_tokens
from backend.app.domain.models import EvidenceSnapshot
from backend.app.domain.scope import Scope


class SmartAgentUnavailable(RuntimeError):
    """The configured local chat model cannot provide Smart mode."""


class _AgentAbort(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ListDocumentsArgs(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)


class ReadDocumentArgs(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)


class QueryKnowledgeGraphArgs(BaseModel):
    entity_name: str = Field(min_length=1, max_length=500)
    depth: int = Field(default=1, ge=1, le=3)


@dataclass(frozen=True)
class _ToolContext:
    gateway: KnowledgeToolGateway
    scope: Scope
    guard: Callable[[], None]
    record: Callable[[str, dict[str, Any], Any], None]
    graph_enabled: bool = False


class LangChainAgentAdapter:
    """Thin Smart-mode adapter around LangChain's create_agent.

    Scope, run identity, evidence accumulation and cancellation are closed over
    by the server.  The model only receives business inputs for the four
    read-only tools; it cannot choose a knowledge base, version, provider or
    external destination.
    """

    def __init__(self, model: Any | None, *, observability: Any | None = None) -> None:
        self.model = model
        self.observability = observability
        self.last_tool_names: tuple[str, ...] = ()
        self.last_tool_schemas: tuple[dict[str, Any], ...] = ()

    def run(
        self,
        conversation_id: str,
        question: str,
        scope: Scope,
        *,
        run_id: str,
        gateway: KnowledgeToolGateway,
        evidence: EvidenceAccumulator,
        graph_enabled: bool = False,
        trace_store: Any | None = None,
        limits: AgentLimits | None = None,
    ) -> SmartAgentResult:
        limits = limits or AgentLimits()
        if trace_store is not None:
            trace_store.create_run(run_id, conversation_id, question, scope)
        if self._is_cancelled(trace_store, run_id):
            return self._finish(trace_store, SmartAgentResult(run_id, "cancelled", error_code="CANCELLED"))
        if self.model is None:
            return self._finish(trace_store, SmartAgentResult(run_id, "failed", error_code="SMART_AGENT_UNAVAILABLE"))
        if limits.max_steps < 1:
            return self._finish(trace_store, SmartAgentResult(run_id, "failed", error_code="AGENT_STEP_LIMIT"))

        started = time.perf_counter()
        model_calls = 0
        model_started: float | None = None
        usage_start = 0

        def record_model(status: str, message: Any = None) -> None:
            nonlocal model_started
            if model_started is None:
                return
            usage = getattr(message, "usage_metadata", {}) or {}
            metadata = getattr(message, "response_metadata", {}) or {}
            response = {
                "prompt_eval_count": usage.get("input_tokens", metadata.get("prompt_eval_count")),
                "eval_count": usage.get("output_tokens", metadata.get("eval_count")),
            }
            latency = (time.perf_counter() - model_started) * 1000
            with call_stage("answer"):
                record_call(model=self._model_name() or "NOT_AVAILABLE", status=status,
                            response=response, latency_ms=latency)
            if (metrics := current_metrics()) is not None:
                metrics.record_generation(path="LOCAL", provider="ollama", model=self._model_name() or "NOT_AVAILABLE",
                                          usage_start=usage_start, latency_ms=latency, status=status)
            model_started = None
        cancelled = lambda: self._is_cancelled(trace_store, run_id)
        steps: list[AgentStep] = []

        def guard() -> None:
            if cancelled():
                raise _AgentAbort("CANCELLED")
            if time.perf_counter() - started > limits.max_seconds:
                raise _AgentAbort("AGENT_TIME_LIMIT")

        def record(tool_name: str, args: dict[str, Any], data: Any) -> None:
            step = AgentStep(
                len(steps) + 1,
                tool_name,
                "completed",
                "keys=" + ",".join(sorted(args)),
                self._summarize(data),
            )
            steps.append(step)
            if trace_store is not None:
                trace_store.append_step(run_id, step)

        context = _ToolContext(gateway, scope, guard, record, graph_enabled)
        tools = self._build_tools(context)
        self.last_tool_names = tuple(tool.name for tool in tools)
        self.last_tool_schemas = tuple(tool.args for tool in tools)

        try:
            from langchain.agents import create_agent
            from langchain.agents.middleware import ToolCallLimitMiddleware, after_model, before_model

            @before_model
            def check_before_model(state: Any, runtime: Any) -> None:
                nonlocal model_calls, model_started, usage_start
                guard()
                model_calls += 1
                model_started = time.perf_counter()
                metrics = current_metrics()
                usage_start = len(metrics.usage.calls) if metrics is not None else 0

            @after_model
            def check_after_model(state: Any, runtime: Any) -> None:
                messages = state.get("messages", [])
                record_model("ok", messages[-1] if messages else None)
                guard()

            agent = create_agent(
                self.model,
                tools,
                system_prompt=(
                    "You are a local knowledge assistant. Use only the provided read-only tools. "
                    "The server has already fixed the knowledge scope; never ask for or invent scope, "
                    "version, provider, filesystem, shell, network, or cloud parameters. Search before "
                    "stating document facts. Every factual sentence in the final answer must end with a "
                    "citation to an authorized search chunk, using its server-provided citation_label "
                    "in square brackets, for example [E1]. A read_document result alone is not a citation. "
                    "If no searched chunk supports an answer, say 资料不足. Never invent a citation."
                ),
                middleware=[
                    check_before_model,
                    check_after_model,
                    ToolCallLimitMiddleware(run_limit=limits.max_steps, exit_behavior="error"),
                ],
            )
            callback = self.observability.langchain_callback() if self.observability is not None else None
            invoke_config: dict[str, Any] = {
                "recursion_limit": max(25, limits.max_steps * 4 + 5),
                "run_name": "smart-agent",
            }
            if callback is not None:
                invoke_config["callbacks"] = [callback]
            agent_observation = (
                self.observability.observation(
                    name="smart-agent-execution",
                    as_type="agent",
                    input={"question": question},
                    metadata={"tool_names": list(self.last_tool_names), "model": self._model_name()},
                )
                if self.observability is not None
                else nullcontext(None)
            )
            with agent_observation as agent_span:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": question}]},
                    config=invoke_config,
                )
                if agent_span is not None:
                    details: dict[str, Any] = {
                        "model_calls": model_calls,
                        "tool_call_count": len(steps),
                        "model": self._model_name(),
                        "tool_names": list(self.last_tool_names),
                    }
                    usage = self._usage_details(result)
                    if usage:
                        details.update(usage)
                    fields: dict[str, Any] = {"metadata": details}
                    candidate_answer = self._last_answer(result)
                    if self.observability.capture_content:
                        fields["output"] = {"answer": candidate_answer}
                    agent_span.update(**fields)
            guard()
            answer = self._last_answer(result)
            if not answer:
                return self._finish(trace_store, SmartAgentResult(run_id, "failed", steps=tuple(steps), error_code="MODEL_EMPTY", model_calls=model_calls))
            guard()
            token_count = estimate_tokens(answer)
            if token_count > limits.max_tokens:
                return self._finish(trace_store, SmartAgentResult(run_id, "failed", steps=tuple(steps), error_code="AGENT_TOKEN_LIMIT", model_calls=model_calls))
            cost_microunits = self._cost_microunits(result)
            if cost_microunits > limits.max_cost_microunits:
                return self._finish(trace_store, SmartAgentResult(run_id, "failed", steps=tuple(steps), error_code="AGENT_COST_LIMIT", cost_microunits=cost_microunits, model_calls=model_calls))
            core = gateway.knowledge_gateway
            if core is None:
                return self._finish(trace_store, SmartAgentResult(run_id, "failed", steps=tuple(steps), error_code="SMART_RETRIEVAL_UNAVAILABLE", model_calls=model_calls))
            query_plan = core.plan(question)
            coverage = core.evidence.evaluate_chunks(evidence.chunks, query_plan)
            if not coverage.accepted:
                code = coverage.reason.value if coverage.reason is not None else "NO_CANDIDATES"
                return self._finish(trace_store, SmartAgentResult(run_id, "failed", steps=tuple(steps), error_code=code, model_calls=model_calls))
            snapshots = evidence.freeze()
            guard()
            answer, citations, error_code = self._normalize_answer(answer, snapshots)
            if error_code is None:
                error_code = core.evidence.validate_answer(answer, snapshots, query_plan)
            if error_code is not None:
                answer, citations, snapshots = "", (), ()
            else:
                snapshots = tuple(snapshot for snapshot in snapshots if snapshot.label in citations)
            status = "completed" if error_code is None else "failed"
            return self._finish(
                trace_store,
                SmartAgentResult(
                    run_id,
                    status,
                    answer,
                    tuple(steps),
                    citations,
                    snapshots,
                    error_code,
                    cost_microunits,
                    model_calls,
                ),
            )
        except _AgentAbort as exc:
            record_model("error")
            return self._finish(trace_store, SmartAgentResult(run_id, "cancelled" if exc.code == "CANCELLED" else "failed", steps=tuple(steps), error_code=exc.code, model_calls=model_calls))
        except Exception as exc:
            record_model("error")
            code = "AGENT_STEP_LIMIT" if type(exc).__name__ in {"ToolCallLimitExceededError", "GraphRecursionError"} else type(exc).__name__
            return self._finish(trace_store, SmartAgentResult(run_id, "failed", steps=tuple(steps), error_code=code, model_calls=model_calls))

    def _build_tools(self, context: _ToolContext) -> list[Any]:
        from langchain_core.tools import StructuredTool

        def list_documents(limit: int = 50) -> str:
            return self._invoke_tool(context, "list_documents", {"limit": limit})

        def search_knowledge(query: str) -> str:
            return self._invoke_tool(context, "search_knowledge", {"query": query})

        def read_document(document_id: str) -> str:
            return self._invoke_tool(context, "read_document", {"document_id": document_id})

        def query_knowledge_graph(entity_name: str, depth: int = 1) -> str:
            return self._invoke_tool(context, "query_knowledge_graph", {"entity_name": entity_name, "depth": depth})

        tools = [
            StructuredTool.from_function(
                list_documents,
                name="list_documents",
                description="List documents in the server-authorized knowledge scope.",
                args_schema=ListDocumentsArgs,
            ),
            StructuredTool.from_function(
                search_knowledge,
                name="search_knowledge",
                description="Search the server-authorized knowledge scope and return evidence chunks.",
                args_schema=SearchKnowledgeArgs,
            ),
            StructuredTool.from_function(
                read_document,
                name="read_document",
                description="Read the active ready document already authorized by the server scope.",
                args_schema=ReadDocumentArgs,
            ),
        ]
        if context.graph_enabled and context.gateway.graph_query is not None:
            tools.append(
                StructuredTool.from_function(
                    query_knowledge_graph,
                    name="query_knowledge_graph",
                    description="Query the graph inside the server-authorized scope.",
                    args_schema=QueryKnowledgeGraphArgs,
                )
            )
        return tools

    def _invoke_tool(self, context: _ToolContext, tool_name: str, args: dict[str, Any]) -> str:
        context.guard()
        result = context.gateway.invoke(tool_name, args, context.scope)
        context.record(tool_name, args, result.data)
        context.guard()
        import json

        return json.dumps(result.data, ensure_ascii=False, default=str)

    @staticmethod
    def _last_answer(result: Any) -> str:
        messages = result.get("messages", []) if isinstance(result, dict) else []
        for message in reversed(messages):
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip() and getattr(message, "type", "") == "ai":
                return content.strip()
        return ""

    @staticmethod
    def _normalize_answer(answer: str, snapshots: tuple[EvidenceSnapshot, ...]) -> tuple[str, tuple[str, ...], str | None]:
        if not snapshots:
            return "", (), "NO_CANDIDATES"
        by_chunk = {snapshot.chunk_id: snapshot.label for snapshot in snapshots if snapshot.chunk_id}
        labels = {snapshot.label for snapshot in snapshots}
        invalid = False

        def replace_reference(match: re.Match[str]) -> str:
            nonlocal invalid
            reference = match.group(1).strip()
            if reference in by_chunk:
                return f"[{by_chunk[reference]}]"
            if reference.startswith("E") and reference[1:].isdigit():
                if reference not in labels:
                    invalid = True
                return f"[{reference}]"
            return match.group(0)

        normalized = re.sub(r"\[([^\[\]]+)\]", replace_reference, answer)
        citations = tuple(dict.fromkeys(re.findall(r"\bE\d+\b", normalized)))
        if invalid or any(label not in labels for label in citations):
            return "", (), "INVALID_CITATION"
        if not citations and not any(phrase in normalized for phrase in ("资料不足", "无法回答", "没有足够证据")):
            return "", (), "UNSUPPORTED_ANSWER"
        return normalized, citations, None

    @staticmethod
    def _summarize(data: Any) -> str:
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return f"items={len(data['items'])}"
        if isinstance(data, dict):
            return "keys=" + ",".join(sorted(str(key) for key in data))
        return type(data).__name__

    @staticmethod
    def _cost_microunits(result: Any) -> int:
        total = 0
        for message in result.get("messages", []) if isinstance(result, dict) else []:
            metadata = getattr(message, "response_metadata", {}) or {}
            try:
                total += int(metadata.get("cost_microunits", 0) or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _model_name(self) -> str | None:
        value = getattr(self.model, "model", None) or getattr(self.model, "model_name", None)
        return str(value) if value else None

    @staticmethod
    def _usage_details(result: Any) -> dict[str, int]:
        input_tokens = 0
        output_tokens = 0
        for message in result.get("messages", []) if isinstance(result, dict) else []:
            usage = getattr(message, "usage_metadata", {}) or {}
            metadata = getattr(message, "response_metadata", {}) or {}
            input_tokens += int(usage.get("input_tokens") or metadata.get("prompt_eval_count") or 0)
            output_tokens += int(usage.get("output_tokens") or metadata.get("eval_count") or 0)
        details: dict[str, int] = {}
        if input_tokens:
            details["input_tokens"] = input_tokens
        if output_tokens:
            details["output_tokens"] = output_tokens
        return details

    @staticmethod
    def _is_cancelled(trace_store: Any | None, run_id: str) -> bool:
        checker = getattr(trace_store, "is_cancelled", None)
        return bool(checker(run_id)) if checker is not None else False

    @staticmethod
    def _finish(trace_store: Any | None, result: SmartAgentResult) -> SmartAgentResult:
        if trace_store is not None:
            trace_store.complete_run(result.run_id, result.status, result.error_code, result.cost_microunits)
        return result


__all__ = [
    "LangChainAgentAdapter",
    "ListDocumentsArgs",
    "QueryKnowledgeGraphArgs",
    "ReadDocumentArgs",
    "SearchKnowledgeArgs",
    "SmartAgentUnavailable",
]
