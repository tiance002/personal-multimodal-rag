from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
import hashlib
import json
import time
from typing import Any

from backend.app.application.agent_ports import SmartAgentPort
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.scope import Scope
from backend.app.domain.errors import FinalAnswerCommitError
from backend.app.ports.persistence import RunEventStore
from backend.app.application.run_metrics import collect_metrics, current_metrics
from backend.app.application.follow_up import resolve_question, clarification, validate_resolution_response, bounded_method_reference
from backend.app.ports.model_usage import ModelCall, call_stage
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.application.table_header_hint import valid_hint
from backend.app.application.run_lifecycle import current_execution
from backend.app.ports.run_lifecycle import LifecycleDenied


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
        follow_up_enabled: bool = False,
        follow_up_provider: Any | None = None,
        observability: Any | None = None,
        quick_settings: QuickSettings | None = None,
        lifecycle: Any | None = None,
        memory_service: Any | None = None,
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
        # Deliberately disabled until separately authorized real semantic validation.
        # No config/env/runtime API switch is introduced by this implementation.
        self.follow_up_enabled = follow_up_enabled
        self.follow_up_provider = follow_up_provider
        self.observability = observability
        self.quick_settings = quick_settings or QuickSettings()
        self.lifecycle = lifecycle
        self.memory_service = memory_service
        self.quick_chain = quick_chain or LangChainQuickChain(
            knowledge_gateway,
            answer_gateway=answer_gateway,
            budget_gate=budget_gate,
        )

    def answer(self, conversation: dict[str, Any], content: str, mode: str = "quick",
               *, request_id: str | None = None) -> AnswerOutcome:
        with collect_metrics("pending") as metrics:
            if self.lifecycle is None:
                outcome = self._answer(conversation, content, mode, request_id=request_id)
            else:
                import uuid
                identity = request_id or str(uuid.uuid4())
                try:
                    claim = self.lifecycle.repository.claim_run(str(conversation['id']),
                        list(conversation['knowledge_base_scope']),list(conversation['document_scope'] or []),content,mode,identity)
                    if not claim.created:
                        saved = self.lifecycle.repository.read_run(claim.run_id,str(conversation['id']),
                            list(conversation['knowledge_base_scope']),list(conversation['document_scope'] or []))
                        if claim.state in {'COMPLETED','FAILED','CANCELLED'}:
                            self.lifecycle.cleanup_terminal(claim.run_id)
                            outcome = AnswerOutcome(claim.run_id,saved['answer'],saved['citations'],saved['error_code'],{'replayed':True})
                        else:
                            outcome = AnswerOutcome(claim.run_id,'',(),'RUN_'+claim.state,{'replayed':True})
                    else:
                        with self.lifecycle.executing(claim,str(conversation['id'])):
                            outcome = self._answer(conversation, content, mode, request_id=identity)
                except LifecycleDenied as exc:
                    outcome = AnswerOutcome(identity,'',(),str(exc),{})
            metrics.query_id = outcome.run_id
            row = metrics.snapshot(citations=outcome.citations, error=outcome.error_code)
            return replace(outcome, trace={**outcome.trace, "metrics": row})

    def _answer(self, conversation: dict[str, Any], content: str, mode: str = "quick",
                *, request_id: str | None = None) -> AnswerOutcome:
        kb_scope = list(conversation["knowledge_base_scope"])
        document_scope = list(conversation["document_scope"] or [])
        conversation_id = str(conversation["id"])

        policy = self.quick_settings.router_policy or getattr(self.quick_chain, "router_policy", None)
        if request_id is None and self.quick_settings.cloud_enabled and (
                (policy is not None and policy.mode != "OFF") or
                self.quick_settings.prefer_cloud or self.quick_settings.cloud_fallback_enabled):
            return AnswerOutcome("", "", (), "IDEMPOTENCY_REQUIRED", {})
        context = current_execution.get()
        if context is not None:
            self.lifecycle.assert_current()
            run_id = context[0].run_id
        elif request_id is not None:
            claim = getattr(self.runs, "create_run_once", None)
            if claim is None:
                return AnswerOutcome(request_id, "", (), "IDEMPOTENCY_UNAVAILABLE", {})
            try:
                run_id, created = claim(conversation_id, kb_scope, document_scope, content, request_id)
            except ValueError:
                return AnswerOutcome(request_id, "", (), "REQUEST_ID_CONFLICT", {})
            if not created:
                return AnswerOutcome(run_id, "", (), "DUPLICATE_REQUEST", {})
        else:
            run_id = self.runs.create_run(conversation_id, kb_scope, document_scope, content)
        if (metrics := current_metrics()) is not None:
            metrics.query_id = run_id
        self.runs.append_event(run_id, "run.created", {"run_id": run_id})
        self.runs.append_message(conversation_id, "user", content, run_id=run_id)
        self.runs.append_event(run_id, "retrieval.started", {"scope": kb_scope, "document_scope": document_scope})

        cloud_allowed_by_kb = {
            kb_id: bool((self.runs.get_knowledge_base(kb_id) or {}).get("cloud_allowed", False))
            for kb_id in kb_scope
        }
        scope = Scope.from_ids(kb_scope, document_scope)
        if self._is_cancelled(run_id):
            return self._cancelled_outcome(run_id, mode)
        # Prefer provenance-aware history; legacy stores still provide completed q0.
        context_reader = getattr(self.runs, "last_completed_question_context", None) if not self.follow_up_enabled else None
        source_run_id = "UNKNOWN"
        if context_reader is not None:
            previous_context = context_reader(conversation_id, kb_scope, document_scope)
            previous = previous_context["q0"] if previous_context else None
            if previous_context and previous_context.get("run_id") is not None:
                source_run_id = str(previous_context["run_id"])
        else:
            previous_reader = getattr(self.runs, "last_completed_question", None) if not self.follow_up_enabled else None
            previous = previous_reader(conversation_id, kb_scope, document_scope) if previous_reader is not None else None
        resolution = resolve_question(content, previous)
        resolver_trace = {"resolver_enabled": self.follow_up_enabled, "resolver_status": "disabled",
                          "resolver_call_count": 0, "history_count": 0}
        if not self.follow_up_enabled and resolution.clarification_required:
            # Restore the existing bounded follow-up capability without enabling
            # the general model resolver or putting historical questions in q0.
            reader = getattr(self.runs, "completed_history_context", None)
            if reader is not None:
                try:
                    context = reader(conversation_id, kb_scope, document_scope, current_run_id=run_id, limit=1)
                    turns = context.get("turns", ())
                    if (len(turns) == 1 and context.get("knowledge_base_scope") == kb_scope
                            and context.get("document_scope") == document_scope and turns[0].get("turn_id") == "H1"):
                        bounded = bounded_method_reference(content, turns[0].get("q0"), turns[0].get("run_id"))
                        if bounded is not None:
                            resolution = bounded
                            source_run_id = bounded.source_run_ids[0]
                            resolver_trace.update(resolver_status="bounded_method_reference", history_count=1)
                except (ValueError, TypeError, KeyError, AttributeError):
                    # Unavailable/malformed history never prevents safe clarification.
                    pass
        if self.follow_up_enabled:
            resolution, resolver_trace = self._resolve_history(content, conversation_id, kb_scope, document_scope, run_id)
            source_run_id = resolution.source_run_ids[0] if len(resolution.source_run_ids) == 1 else "UNKNOWN"
        execution_question = resolution.question if resolution.used else content
        follow_up_used = bool(resolution.source_run_ids)
        resolution_args = ({"retrieval_query": execution_question, "validated_follow_up": resolution}
                           if resolution.used else {})
        resolution_trace = {
            "query_original": content,
            "query_resolved": execution_question,
            "resolution_reason": resolution.reason,
            "source_completed_run_id": source_run_id,
            "resolution_scope": {"knowledge_base_ids": kb_scope, "document_ids": document_scope},
            "follow_up_context_used": follow_up_used,
            "history_resolution_performed": bool(resolver_trace["resolver_call_count"]),
            "automatic_ambiguity_resolution": ("LOCAL_RESOLVER_SEMANTICS_NOT_VERIFIED" if resolver_trace["resolver_call_count"] else "BOUNDED_METHOD_REFERENCE" if resolution.reason == "BOUNDED_METHOD_REFERENCE" else "NOT_PERFORMED"),
            "clarification_required": resolution.clarification_required,
            "resolver_decision": resolution.decision,
            "source_completed_run_ids": list(resolution.source_run_ids),
            **resolver_trace,
        }
        if (metrics := current_metrics()) is not None:
            metrics.hardening["follow_up"] = resolution_trace

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
            privacy_evidence_only = self.quick_chain._privacy_configuration(content)
            answer_type, finish_reason = "generated", None
            commit_plan = self.knowledge_gateway.plan(execution_question).evidence_plan
            if resolution.clarification_required:
                # Existing insufficient-evidence terminal path, with a non-factual
                # clarification. Do not let retrieval similarity pick the object.
                answer = ("请明确对象并重述完整问题；本次上下文解析不可用或指代不明确，未检索或生成事实答案。"
                          if self.follow_up_enabled else "请明确当前问题所指的对象或补全问题；未执行历史解析，无法自动消除指代歧义。")
                citations, error_code, snapshots = (), "NO_CANDIDATES", ()
                trace = {"requested_mode": mode, "execution_mode": "clarification"}
            elif mode == "smart" and not privacy_evidence_only:
                answer, citations, error_code, trace, smart_evidence, agent_terminal = self._run_smart(
                    conversation_id,
                    content,
                    scope,
                    run_id,
                    **resolution_args,
                )
                snapshots: tuple[Any, ...] = smart_evidence
            else:
                result = self.quick_chain.invoke(
                    content,
                    scope,
                    settings=replace(self.quick_settings,
                        local_query_enabled=self.local_query_enabled and not privacy_evidence_only and not resolver_trace["resolver_call_count"],
                    ),
                    run_id=run_id,
                    cloud_allowed_by_kb=cloud_allowed_by_kb,
                    local_query_gateway=self.local_query_gateway,
                    on_retrieval=self.runs.persist_retrieval_hits,
                    is_cancelled=lambda: self._is_cancelled(run_id),
                    **({'conversation_id': conversation_id} if getattr(self.quick_chain, 'context_manager', None) is not None else {}),
                    **resolution_args,
                )
                answer, citations, error_code = result.answer, result.citations, result.error_code
                trace = dict(result.trace.__dict__)
                snapshots = result.evidence
                answer_type, finish_reason = result.answer_type, result.finish_reason
                commit_plan = result.query_plan.evidence_plan
                if privacy_evidence_only:
                    trace.update(requested_mode=mode, execution_mode="evidence_only")

            trace.update(resolution_trace)
            trace["execution_model_calls"] = trace.get("model_calls", 0)
            trace["model_calls"] = trace["execution_model_calls"] + resolver_trace["resolver_call_count"]
            if error_code == "CANCELLED" or self._is_cancelled(run_id):
                if langfuse_run is not None:
                    langfuse_run.finish(answer="", citations=(), error_code="CANCELLED", run_trace=trace)
                return self._cancelled_outcome(run_id, mode, trace)

            if error_code is None:
                error_code = self.knowledge_gateway.evidence.commit_check.check(
                    answer, snapshots, commit_plan, answer_type=answer_type,
                    finish_reason=finish_reason or trace.get("finish_reason"), citations=citations)
                trace["final_commit_check"] = "PASS" if error_code is None else error_code
                if error_code:
                    answer, citations, snapshots = "", (), ()
                    if agent_terminal is not None:
                        agent_terminal = ("failed", error_code, agent_terminal[2])

            trace["follow_up_context_used"] = follow_up_used
            if (metrics := current_metrics()) is not None:
                metadata = metrics.snapshot(citations=citations, error=error_code)
                if (trace.get("execution_mode") == "clarification"
                        and trace.get("clarification_required") is True
                        and error_code == "NO_CANDIDATES" and not citations
                        and isinstance(answer, str) and answer.strip()):
                    # Presentation only: the read side also requires committed failed
                    # status and matching current scope. Never an assistant fact.
                    metadata["presentation"] = {"kind": "clarification", "text": answer,
                                                "clarification_required": True}
                hint = trace.get("evidence_hint")
                if (error_code == "INSUFFICIENT_EVIDENCE" and not citations and not snapshots
                        and valid_hint(hint) and answer == hint["text"]):
                    # Diagnostic on the exact failed user/run, never an assistant fact.
                    metadata["presentation"] = hint
                self.runs.append_event(run_id, "run.metrics", metadata)
            finalizer = getattr(self.runs, "finalize_answer", None)
            if finalizer is not None:
                commit_args = dict(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    answer=answer,
                    citations=tuple(citations),
                    snapshots=snapshots,
                    error_code=error_code,
                    mode=mode,
                    agent_terminal=agent_terminal,
                    commit_check=lambda: self.knowledge_gateway.evidence.commit_check.check(
                        answer, snapshots, commit_plan, answer_type=answer_type,
                        finish_reason=finish_reason or trace.get("finish_reason"), citations=citations),
                )
                try:
                    committed = finalizer(**commit_args)
                except FinalAnswerCommitError as exc:
                    # The success TX rolled back. Commit only a failed terminal;
                    # cancellation can still win the second terminal lock.
                    error_code = str(exc)
                    answer, citations, snapshots = "", (), ()
                    trace["final_commit_check"] = error_code
                    if agent_terminal is not None:
                        agent_terminal = ("failed", error_code, agent_terminal[2])
                    commit_args.update(answer="", citations=(), snapshots=(), error_code=error_code,
                                       agent_terminal=agent_terminal, commit_check=None)
                    committed = finalizer(**commit_args)
                if not committed:
                    if langfuse_run is not None:
                        langfuse_run.finish(answer="", citations=(), error_code="CANCELLED", run_trace=trace)
                    return self._cancelled_outcome(run_id, mode, trace)
                outcome = AnswerOutcome(run_id, answer, tuple(citations), error_code, trace)
                if error_code is None and self.memory_service is not None:
                    try:
                        trace['memory_extraction_job'] = self.memory_service.after_completed(conversation_id,scope)
                    except Exception:
                        # The answer was authoritatively committed; scheduling
                        # failure must not relabel or regenerate that answer.
                        trace['memory_schedule_error'] = 'MEMORY_SCHEDULE_FAILED'
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

    def _resolve_history(self, content: str, conversation_id: str, kb_scope: list[str],
                         document_scope: list[str], run_id: str):
        metadata = {"resolver_enabled": True, "resolver_status": "no_history",
                    "resolver_call_count": 0, "resolver_attempt_count": 0, "history_count": 0}
        reader = getattr(self.runs, "completed_history_context", None)
        if reader is None:
            metadata["resolver_status"] = "unavailable"
            return clarification(content, "FOLLOW_UP_HISTORY_UNAVAILABLE"), metadata
        try:
            context = reader(conversation_id, kb_scope, document_scope, current_run_id=run_id, limit=3)
            if not isinstance(context, dict):
                raise ValueError()
            turns = tuple(context.get("turns", ()))
            if len(turns) > 3:
                raise ValueError()
            if turns and (context.get("knowledge_base_scope") != kb_scope or context.get("document_scope") != document_scope):
                raise ValueError()
            clean = []
            for index, item in enumerate(turns, 1):
                if (not isinstance(item, dict) or item.get("turn_id") != f"H{index}"
                        or not isinstance(item.get("run_id"), str) or not item["run_id"]
                        or not isinstance(item.get("q0"), str) or not 0 < len(item["q0"]) <= 512):
                    raise ValueError()
                clean.append({key: item[key] for key in ("turn_id", "run_id", "q0")})
            turns = tuple(clean)
        except Exception:
            metadata["resolver_status"] = "unavailable"
            return clarification(content, "FOLLOW_UP_HISTORY_UNAVAILABLE"), metadata
        metadata.update(history_count=len(turns), history_barrier=context.get("blocked_reason"),
                        history_omitted_count=context.get("omitted_count", 0),
                        history_snapshot_digest=hashlib.sha256(json.dumps(turns, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
        if not turns:
            return resolve_question(content, None), metadata
        provider = self.follow_up_provider
        if (provider is None or getattr(provider, "provider_kind", None) != "local"
                or not callable(getattr(provider, "resolve_history_json", None))):
            metadata["resolver_status"] = "unavailable"
            return clarification(content, "FOLLOW_UP_PROVIDER_UNAVAILABLE"), metadata
        if len(json.dumps({"q0": content, "history": turns}, ensure_ascii=False).encode("utf-8")) > 6000:
            metadata["resolver_status"] = "input_limit"
            return clarification(content, "FOLLOW_UP_INPUT_LIMIT"), metadata
        if self._is_cancelled(run_id):
            return clarification(content, "CANCELLED"), metadata
        metrics = current_metrics()
        usage_start = len(metrics.usage.calls) if metrics is not None else 0
        started = time.perf_counter()
        status = "error"
        metadata["resolver_call_count"] = 1
        metadata["resolver_attempt_count"] = 1
        try:
            with call_stage("query"):
                raw = provider.resolve_history_json(content, turns, timeout_seconds=20.0, max_output_tokens=256)
            if self._is_cancelled(run_id):
                metadata["resolver_status"] = "cancelled"
                return clarification(content, "CANCELLED"), metadata
            if time.perf_counter() - started > 20.0:
                raise TimeoutError()
            resolution = validate_resolution_response(content, turns, raw)
            status = "ok"
            metadata["resolver_status"] = resolution.decision
            return resolution, metadata
        except ProviderRequestNotSent:
            status = "not_sent"
            metadata.update(resolver_status="not_sent", resolver_call_count=0)
            return clarification(content, "FOLLOW_UP_REQUEST_NOT_SENT"), metadata
        except TimeoutError:
            metadata["resolver_status"] = "timeout"
            return clarification(content, "FOLLOW_UP_RESOLVER_TIMEOUT"), metadata
        except (ValueError, TypeError, KeyError):
            metadata["resolver_status"] = "invalid"
            return clarification(content, "FOLLOW_UP_SCHEMA_INVALID"), metadata
        except Exception:
            metadata["resolver_status"] = "unavailable"
            return clarification(content, "FOLLOW_UP_RESOLVER_FAILED"), metadata
        finally:
            latency = (time.perf_counter() - started) * 1000
            metadata["resolver_latency_ms"] = latency
            if metrics is not None and status != "not_sent" and len(metrics.usage.calls) == usage_start:
                metrics.usage.calls.append(ModelCall("query", "business", getattr(provider, "chat_model", "NOT_AVAILABLE"),
                                                    status, latency, None, None))

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
        *, retrieval_query: str | None = None,
        validated_follow_up: Any | None = None,
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
            **({"retrieval_query": retrieval_query, "validated_follow_up": validated_follow_up}
               if validated_follow_up is not None else {}),
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
            "finish_reason": getattr(agent_result, "finish_reason", None),
        }
        hint = getattr(agent_result, "evidence_hint", None)
        if (agent_result.status == "failed" and agent_result.error_code == "INSUFFICIENT_EVIDENCE"
                and not agent_result.citations and not agent_result.evidence
                and valid_hint(hint) and agent_result.answer == hint["text"]):
            trace["evidence_hint"] = hint
            trace["reason_codes"] = ["INSUFFICIENT_EVIDENCE", hint["reason_code"]]
        return (
            agent_result.answer,
            tuple(agent_result.citations),
            agent_result.error_code,
            trace,
            tuple(agent_result.evidence),
            deferred_trace.terminal if deferred_trace is not None else None,
        )


__all__ = ["AnswerOutcome", "AnswerService"]
