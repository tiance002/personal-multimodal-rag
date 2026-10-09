from __future__ import annotations

from backend.app.ports.session_attempts import request_identity
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable

import re
import uuid
import time
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.runnables import RunnableLambda

from backend.app.application.budget import BudgetDenied, BudgetGate, BudgetReservation
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_gateway import AnswerResult, EvidenceBundle, KnowledgeGateway, QueryPlan, Trace
from backend.app.application.retrieval import RetrievalItem
from backend.app.domain.scope import Scope
from backend.app.application.execution_routing import ExecutionRouter, ExecutionRoute
from backend.app.application.run_metrics import current_metrics
from backend.app.application.answer_hardening import detect_intents, generation_budget, MARKER, normalize_marker_spacing
from backend.app.application.follow_up import FollowUpResolution, interpretation_data
from backend.app.application.question_checklist import explicit_question_checklist
from backend.app.application.table_header_hint import header_hint, REASON as TABLE_HEADER_REASON
from backend.app.application.rule_router_v1 import RouterPolicy, GenerationEnvelope, GenerationRole, GenerationSettlementFailure, decide, digest, escalation_reason
from backend.app.ports.providers import TruncatedAnswer


@dataclass(frozen=True)
class QuickSettings:
    cloud_enabled: bool = False
    prefer_cloud: bool = False
    cloud_fallback_enabled: bool = False
    local_query_enabled: bool = False
    local_query_timeout_seconds: float = 4.0
    answer_timeout_seconds: float = 30.0
    cloud_cost_estimate_microunits: int = 1
    cloud_provider: str = "cloud"
    cloud_model: str = "configured"
    explicit_question_checklist_enabled: bool = False
    router_policy: RouterPolicy | None = None


class LangChainQuickChain:
    """Fixed Quick pipeline composed as a LangChain Runnable sequence.

    LangChain provides the execution composition only.  Query planning,
    retrieval, evidence coverage, citation freezing and answer validation remain
    in the project-owned KnowledgeGateway/RAG Core.
    """

    def __init__(
        self,
        knowledge_gateway: KnowledgeGateway,
        *,
        answer_gateway: Any | None = None,
        cloud_answer_gateway: Any | None = None,
        budget_gate: BudgetGate | None = None,
        router_policy: RouterPolicy | None = None,
        generation_roles: dict[str, GenerationRole] | None = None,
        input_token_estimator: Callable[[str], tuple[int, str]] | None = None,
        context_manager: Any | None = None,
    ) -> None:
        self.knowledge_gateway = knowledge_gateway
        self.answer_gateway = answer_gateway
        self.cloud_answer_gateway = cloud_answer_gateway
        self.budget_gate = budget_gate
        self.execution_router = ExecutionRouter()
        self.router_policy = router_policy or RouterPolicy()
        self.generation_roles = dict(generation_roles or {})
        self.input_token_estimator = input_token_estimator
        self.context_manager = context_manager
        self.runnable = (
            RunnableLambda(self._prepare)
            | RunnableLambda(self._generate)
            | RunnableLambda(self._validate)
        )

    def invoke(
        self,
        question: str,
        scope: Scope,
        *,
        settings: QuickSettings | None = None,
        run_id: str | None = None,
        cloud_allowed_by_kb: dict[str, bool] | None = None,
        local_query_gateway: Any | None = None,
        on_retrieval: Callable[[str, Sequence[RetrievalItem]], None] | None = None,
        retrieval_query: str | None = None,
        validated_follow_up: FollowUpResolution | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        conversation_id: str | None = None,
    ) -> AnswerResult:
        payload = {
            "question": question,
            "retrieval_query": question if retrieval_query is None else retrieval_query,
            "validated_follow_up": validated_follow_up,
            "scope": scope,
            "settings": settings or QuickSettings(),
            "run_id": run_id or str(uuid.uuid4()),
            "cloud_allowed_by_kb": cloud_allowed_by_kb or {},
            "local_query_gateway": local_query_gateway if local_query_gateway is not None else self.answer_gateway,
            "on_retrieval": on_retrieval,
            "is_cancelled": is_cancelled or (lambda: False),
            "caller_run_id": run_id is not None,
            "conversation_id": conversation_id,
        }
        return self.runnable.invoke(payload)

    def _prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload["question"])
        scope: Scope = payload["scope"]
        settings: QuickSettings = payload["settings"]
        run_id = str(payload["run_id"])
        cloud_allowed_by_kb: dict[str, bool] = payload["cloud_allowed_by_kb"]
        policy = settings.router_policy or self.router_policy
        dynamic = policy.mode != "OFF" and not self._privacy_configuration(question)
        payload["router_policy"] = policy
        payload["router_enabled"] = dynamic
        if dynamic:
            if not payload["caller_run_id"]:
                return self._terminal(payload, self._error_result(run_id, self.knowledge_gateway.plan(question), "IDEMPOTENCY_REQUIRED"))
            if not settings.cloud_enabled or not scope.knowledge_base_ids or not all(cloud_allowed_by_kb.get(k, False) for k in scope.knowledge_base_ids):
                return self._terminal(payload, self._error_result(run_id, self.knowledge_gateway.plan(question), "CLOUD_EGRESS_DISABLED"))
            if policy.mode.endswith("ONLY") and not all(r.simulated for r in self.generation_roles.values()):
                return self._terminal(payload, self._error_result(run_id, self.knowledge_gateway.plan(question), "OFFLINE_ROUTER_MODE_ONLY"))
            from backend.app.application.run_lifecycle import current_execution, RunLifecycle
            try:
                RunLifecycle.assert_current()
            except Exception:
                return self._terminal(payload,self._error_result(run_id,self.knowledge_gateway.plan(question),'RUN_LIFECYCLE_UNAVAILABLE'))
        cloud_gateway = self.cloud_answer_gateway or (self.answer_gateway
            if self.answer_gateway is not None and getattr(self.answer_gateway, "provider_kind", None) != "local" else None)
        execution_route = self.execution_router.choose(
            prefer_cloud=settings.prefer_cloud and not self._privacy_configuration(question), cloud_enabled=settings.cloud_enabled,
            cloud_allowed=bool(scope.knowledge_base_ids) and all(cloud_allowed_by_kb.get(kb_id, False) for kb_id in scope.knowledge_base_ids),
            cloud_provider_available=cloud_gateway is not None,
        )
        if dynamic:
            execution_route = ExecutionRoute("CLOUD", "RULE_ROUTER")
        payload["execution_route"] = execution_route
        payload["cloud_gateway"] = cloud_gateway
        answer_gateway = cloud_gateway if execution_route.path == "CLOUD" else self.answer_gateway
        payload["answer_gateway"] = answer_gateway
        if execution_route.path != "CLOUD" and getattr(answer_gateway, "provider_kind", None) == "cloud":
            return self._terminal(payload, self._error_result(run_id, self.knowledge_gateway.plan(question), "CLOUD_EGRESS_DISABLED"))
        if execution_route.error_code is not None:
            return self._terminal(payload, self._error_result(run_id, self.knowledge_gateway.plan(question), execution_route.error_code))
        if execution_route.path == "CLOUD":
            if any(not cloud_allowed_by_kb.get(kb_id, False) for kb_id in scope.knowledge_base_ids):
                plan = self.knowledge_gateway.plan(question)
                return self._terminal(
                    payload,
                    AnswerResult(
                        run_id,
                        "",
                        (),
                        plan,
                        Trace(reason_codes=("CLOUD_EGRESS_DISABLED",)),
                        "CLOUD_EGRESS_DISABLED",
                    ),
                )

        citation_service = CitationService(InMemoryCitationStore())
        plan, retrieval = self.knowledge_gateway.retrieve_question(
            scope,
            str(payload["retrieval_query"]),
            local_query_gateway=payload["local_query_gateway"],
            local_query_enabled=settings.local_query_enabled,
            local_query_timeout_seconds=settings.local_query_timeout_seconds,
        )
        observer = payload.get("on_retrieval")
        if observer is not None:
            observer(run_id, retrieval.items)

        bundle = self.knowledge_gateway.evidence.bundle(plan, retrieval)
        if not bundle.selected:
            code = "SECTION_TRUNCATED" if retrieval.items else "NO_CANDIDATES"
            return self._terminal(payload, self._error_result(run_id, plan, code))

        if not bundle.decision.accepted:
            if bundle.decision.missing_targets:
                partial = self.knowledge_gateway.evidence.partial_answer(
                    run_id=run_id,
                    plan=plan,
                    selected=bundle.selected,
                    missing_targets=bundle.decision.missing_targets,
                    citations=citation_service,
                )
                if partial is not None:
                    return self._terminal(payload, partial)
            reason = bundle.decision.reason.value if bundle.decision.reason is not None else "NO_CANDIDATES"
            hint = (header_hint([item.chunk for item in bundle.selected], bundle.decision.missing_targets)
                    if reason == "INSUFFICIENT_EVIDENCE" else None)
            if hint is not None:
                return self._terminal(payload, AnswerResult(run_id, hint["text"], (), plan,
                    Trace(plan.degradation_code, (reason, TABLE_HEADER_REASON), plan.model_calls, hint), reason))
            return self._terminal(payload, self._error_result(run_id, plan, reason))

        bundle = self.knowledge_gateway.evidence.with_context(bundle, run_id, citation_service)
        if not bundle.labels:
            return self._terminal(payload, self._error_result(run_id, plan, "SECTION_TRUNCATED"))

        reservation: BudgetReservation | None = None
        if execution_route.path == "CLOUD" and not dynamic:
            try:
                reservation = self._reserve_cloud(payload)
            except BudgetDenied as exc:
                code = str(exc) or "MONTHLY_BUDGET_EXCEEDED"
                return self._terminal(payload, self._error_result(run_id, plan, code))

        return {
            **payload,
            "plan": plan,
            "bundle": bundle,
            "citation_service": citation_service,
            "reservation": reservation,
            "model_calls": plan.model_calls,
            "answer_degradation": ";".join(code for code in (plan.degradation_code, "PRIVACY_CONFIG_EVIDENCE_ONLY" if self._privacy_configuration(question) else None) if code) or None,
            "evidence_only": (answer_gateway is None and not dynamic) or self._privacy_configuration(question),
        }

    def _generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "result" in payload:
            return payload
        question = str(payload["question"])
        settings: QuickSettings = payload["settings"]
        run_id = str(payload["run_id"])
        plan: QueryPlan = payload["plan"]
        bundle: EvidenceBundle = payload["bundle"]
        reservation: BudgetReservation | None = payload["reservation"]
        model_calls = int(payload["model_calls"])
        answer_degradation = payload.get("answer_degradation")
        evidence_only = bool(payload["evidence_only"])
        answer_gateway = payload["answer_gateway"]

        if evidence_only:
            prefix = "涉及外发与隐私配置，以下仅提供资料原文；实际运行配置需单独核查：\n" if self._privacy_configuration(question) else "基于检索到的证据：\n"
            answer = prefix + bundle.context
        else:
            generation_started = time.perf_counter()
            metrics = current_metrics()
            usage_start = len(metrics.usage.calls) if metrics is not None else 0
            path = payload["execution_route"].path
            provider = getattr(answer_gateway, "provider_name", "NOT_AVAILABLE")
            model = getattr(answer_gateway, "chat_model", "NOT_AVAILABLE")
            generation_status = "error"
            if metrics is not None and not payload.get("router_enabled"):
                metrics.hardening.setdefault("generation_routes", []).append({
                    "path": path, "reason": payload["execution_route"].reason, "provider": provider,
                    "cost_basis": "configured_reservation_estimate_not_verified_charge" if reservation else "LOCAL_COMPUTE_NOT_MEASURED",
                    "reserved_microunits": reservation.reserved_microunits if reservation else None})
            try:
                intents = detect_intents(question)
                budget = generation_budget(intents, len(bundle.context))
                if metrics is not None:
                    metrics.hardening.update({"generation_complexity": budget.complexity, "max_output_tokens": budget.max_tokens})
                prompt = (
                    "Answer only from the supplied evidence. "
                    "Answer in the language of the user question by default; follow any explicit user request for a different output language instead. Do not switch answer language to match the evidence. Keep original quotations, proper names, numeric units and citation labels unchanged where necessary. "
                    "Cite every supported factual statement with "
                    "its exact evidence label. Allowed citation markers: "
                    + " ".join(f"[{label}]" for label in bundle.labels)
                    + ". Use only these markers, separately, never ranges. For each requested item, "
                    "answer supported sub-points with citations; mark only genuinely missing sub-points "
                    "as unknown without a citation. Be concise: cover requested points, "
                    "omit unrelated background and do not repeat source excerpts."
                    f"\nQuestion: {question}{intents.checklist()}"
                    + (explicit_question_checklist(question, len(intents.intents))
                       if settings.explicit_question_checklist_enabled else "")
                    + interpretation_data(payload.get("validated_follow_up"))
                    + f"\nEvidence:\n{bundle.context}"
                )
                if self.context_manager is not None:
                    if payload.get('conversation_id') is None:
                        raise ProviderRequestNotSent('CONTEXT_CONVERSATION_REQUIRED')
                    roles = tuple(self.generation_roles) if payload.get('router_enabled') else (self.context_manager.gateway_role(answer_gateway),)
                    if payload.get('router_enabled'):
                        from backend.app.ports.context_budget import ContextDenied
                        for role, binding in self.generation_roles.items():
                            window = self.context_manager.window(role)
                            if (window.provider, window.model) != (binding.provider, binding.model):
                                raise ContextDenied('MODEL_CONTEXT_IDENTITY_MISMATCH')
                    prompt = self.context_manager.quick_prompt(prompt, conversation=payload['conversation_id'],
                        scope=payload['scope'], run=run_id, roles=roles, output_tokens=budget.max_tokens, query=question)
                if payload.get("router_enabled"):
                    return self._generate_router(payload, prompt, budget.max_tokens)
                if self.context_manager is not None and self.context_manager.memory_retriever is not None:
                    self.context_manager.memory_retriever.validate_frozen(prompt,payload['scope'])
                model_calls += 1
                budget_answer = getattr(answer_gateway, "answer_with_budget", None)
                if getattr(answer_gateway, "provider_name", None) == "deepseek":
                    product_answer = getattr(answer_gateway, "answer_with_product_scope", None)
                    scope_args = {"run_id": run_id, "scope": payload["scope"], "question": question} if product_answer else {}
                    answer = (product_answer or budget_answer)(prompt, settings.answer_timeout_seconds, budget.max_tokens,
                                           cloud_authorized=path == "CLOUD",
                                           request_id=request_identity(run_id, "quick.answer", 1), **scope_args)
                else:
                    answer = budget_answer(prompt, settings.answer_timeout_seconds, budget.max_tokens) if budget_answer else answer_gateway.answer(prompt, settings.answer_timeout_seconds)
                generation_status = "ok"
            except Exception as exc:
                if reservation is not None:
                    if isinstance(exc, ProviderRequestNotSent):
                        self.budget_gate.release(reservation.reservation_id)
                    else:
                        self.budget_gate.mark_unknown(reservation.reservation_id)
                answer = getattr(exc, "candidate", "")
                from backend.app.application.context_window import ContextDenied
                if isinstance(exc, ContextDenied):
                    return self._terminal(payload, self._error_result(run_id, plan, str(exc), model_calls=model_calls))
                payload["generation_error"] = "MODEL_OUTPUT_TRUNCATED" if str(exc) == "MODEL_OUTPUT_TRUNCATED" else "MODEL_UNAVAILABLE"
                answer_degradation = ";".join(
                    code for code in (answer_degradation, "MODEL_OUTPUT_TRUNCATED" if str(exc) == "MODEL_OUTPUT_TRUNCATED" else "MODEL_UNAVAILABLE") if code
                ) or "MODEL_UNAVAILABLE"
            else:
                if reservation is not None:
                    try:
                        self.budget_gate.settle(
                            reservation.reservation_id,
                            settings.cloud_cost_estimate_microunits,
                        )

                    except BudgetDenied as exc:
                        self.budget_gate.mark_unknown(reservation.reservation_id)
                        return self._terminal(
                            payload,
                            self._error_result(
                                run_id,
                                plan,
                                str(exc) or "BUDGET_SETTLEMENT_FAILED",
                                answer_degradation,
                                model_calls,
                            ),
                        )
                    except Exception:
                        self.budget_gate.mark_unknown(reservation.reservation_id)
                        return self._terminal(
                            payload,
                            self._error_result(
                                run_id,
                                plan,
                                "BUDGET_SETTLEMENT_FAILED",
                                answer_degradation,
                                model_calls,
                            ),
                        )

            finally:
                if metrics is not None and not payload.get("router_enabled"):
                    metrics.record_generation(path=path, provider=provider, model=model, usage_start=usage_start,
                                              latency_ms=(time.perf_counter() - generation_started) * 1000,
                                              status=generation_status)

            if generation_status == "error" and path == "LOCAL" and settings.cloud_fallback_enabled:
                return self._fallback_cloud(payload, model_calls, answer_degradation)

        return {
            **payload,
            "answer": answer,
            "model_calls": model_calls,
            "answer_degradation": answer_degradation,
            "evidence_only": evidence_only,
        }

    def _generate_router(self, payload: dict[str, Any], prompt: str, max_tokens: int) -> dict[str, Any]:
        """Same fixed Quick chain, at most two generation attempts, one finalizer."""
        settings = payload["settings"]
        bundle = payload["bundle"]
        policy = payload["router_policy"]
        run_id = payload["run_id"]
        resolution = payload.get("validated_follow_up")
        trusted_query = (resolution.question if resolution and resolution.used and resolution.query_original == payload["question"]
                         else payload["question"])
        estimated_tokens, estimator = None, "UNKNOWN"
        if self.input_token_estimator is not None:
            try:
                estimated_tokens, estimator = self.input_token_estimator(prompt)
                if type(estimated_tokens) is not int or estimated_tokens <= 0 or not isinstance(estimator, str) or estimator == "UNKNOWN":
                    raise ValueError("TOKEN_ESTIMATE_INVALID")
            except Exception:
                estimated_tokens, estimator = None, "UNKNOWN"
        envelope = GenerationEnvelope(payload["question"], trusted_query, prompt, digest(bundle.context), tuple(bundle.labels),
            tuple((s.version_id, s.chunk_id, s.quote_sha256, digest(json.dumps(s.locator, sort_keys=True))) for s in bundle.snapshots),
            max_tokens, settings.answer_timeout_seconds, estimated_input_tokens=estimated_tokens)
        decision = decide(trusted_query, prompt, policy, estimated_tokens=estimated_tokens, estimator=estimator)
        metrics = current_metrics()
        row = decision.public(policy) | {"context_hash": envelope.context_hash, "prompt_hash": digest(prompt),
            "envelope_identity": envelope.identity, "selected_source_ids": [s.chunk_id for s in bundle.snapshots],
            "selected_parent_count": len({i.chunk.parent_id for i in bundle.selected if i.chunk.parent_id}),
            "attempts": [], "escalation_reason": None, "provider_failure_reason": None,
            "net_saving": "NOT_AVAILABLE", "total_latency_ms": None}
        row["legacy_cost_field_basis"] = "COMPATIBILITY_FIELD_NOT_BILLING"
        if metrics is not None:
            metrics.rule_router = row
        started = time.perf_counter()
        role, answer, error = decision.role, "", None
        calls = int(payload["model_calls"])
        for ordinal in (1, 2):
            if payload["is_cancelled"]():
                return self._terminal(payload, self._error_result(run_id, payload["plan"], "CANCELLED", model_calls=calls))
            attempt_id = request_identity(run_id, "quick.router." + role, ordinal)
            binding = self.generation_roles.get(role)
            if binding is None:
                return self._terminal(payload, self._error_result(run_id, payload["plan"], "P5_GENERATION_ROLE_UNAVAILABLE", model_calls=calls))
            if binding.role != role:
                return self._terminal(payload, self._error_result(run_id, payload["plan"], "ROUTER_ROLE_BINDING_MISMATCH", model_calls=calls))
            if self.context_manager is not None and self.context_manager.memory_retriever is not None:
                try:self.context_manager.memory_retriever.validate_frozen(prompt,payload['scope'])
                except ProviderRequestNotSent as exc:
                    return self._terminal(payload,self._error_result(run_id,payload['plan'],str(exc),model_calls=calls))
            from backend.app.application.run_lifecycle import current_execution, RunLifecycle
            from backend.app.ports.run_lifecycle import LifecycleDenied, current_attempt
            durable = current_execution.get()
            if durable is None:
                return self._terminal(payload,self._error_result(run_id,payload['plan'],'RUN_LIFECYCLE_UNAVAILABLE',model_calls=calls))
            else:
                try:
                    claim, lifecycle, lost, session = RunLifecycle.assert_current()
                    # No re-entry/send when Redis ownership was lost, even before heartbeat.
                    if not lifecycle.streams.renew_live_run(session,run_id,claim.owner):
                        raise LifecycleDenied('RUN_LEASE_LOST')
                    lifecycle.repository.claim_attempt(run_id=run_id,owner=claim.owner,attempt_id=attempt_id,
                        role=role,ordinal=ordinal,envelope_hash=envelope.identity,provider=binding.provider,model=binding.model)
                except Exception:
                    return self._terminal(payload,self._error_result(run_id,payload['plan'],'ATTEMPT_ADMISSION_DENIED',model_calls=calls))
            usage_start = len(metrics.usage.calls) if metrics is not None else 0
            call_started = time.perf_counter()
            provider_failed = False
            not_sent = False
            generation_status, send_status, validation = "FAILED", "UNKNOWN", "NOT_RUN"
            finish_reason, settlement_operation = "UNKNOWN", "UNKNOWN"
            primary_outcome = None
            answer, error, error_class = "", None, None
            try:
                token = current_attempt.set(attempt_id)
                try:
                    answer = binding.invoke(envelope, request_id=attempt_id, run_id=run_id, scope=payload["scope"])
                finally:
                    current_attempt.reset(token)
                generation_status, send_status = "COMPLETED", "RESPONSE_RECEIVED"
                finish_reason, settlement_operation = "stop", "COMPLETED"
                candidate = normalize_marker_spacing(answer)
                error = self.knowledge_gateway.evidence.hardening.check_candidate(candidate, bundle.snapshots, payload["plan"],
                    self.knowledge_gateway.evidence.validate_answer(candidate, bundle.snapshots, payload["plan"]))
                validation = error or "PASS"
            except ProviderRequestNotSent as exc:
                code = str(exc)
                safe_codes = {"P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY", "ROUTER_MODEL_IDENTITY_MISMATCH", "MODEL_USAGE_GUARD_REQUIRED", "DEEPSEEK_GATED_ROLE_REQUIRED", "MODEL_BUDGET_DENIED"}
                not_sent, error, error_class = True, code if code in safe_codes else "MODEL_REQUEST_NOT_SENT", "ProviderRequestNotSent"
                generation_status, send_status, settlement_operation = "NOT_SENT", "NOT_SENT", "NOT_REQUIRED_OR_RELEASED"
            except TruncatedAnswer as exc:
                answer, error, error_class = exc.candidate, "MODEL_OUTPUT_TRUNCATED", "TruncatedAnswer"
                generation_status, send_status = "TRUNCATED", "RESPONSE_RECEIVED"
                finish_reason, settlement_operation = "length", "COMPLETED"
            except GenerationSettlementFailure as exc:
                not_sent = exc.not_sent
                provider_failed, error, error_class = True, "MODEL_UNAVAILABLE", "SettlementFailure"
                send_status = "NOT_SENT" if not_sent else "UNKNOWN"
                settlement_operation, primary_outcome = "FAILED", exc.primary_outcome
            except Exception as exc:
                if generation_status == "COMPLETED":
                    error, error_class, validation = "CANDIDATE_VALIDATION_INTERNAL_ERROR", "CandidateValidationFailure", "VALIDATION_ERROR"
                else:
                    provider_failed, error, error_class = True, "MODEL_UNAVAILABLE", "ProviderFailure"
                    if isinstance(exc, ProviderUnavailable) and str(exc) == "MODEL_USAGE_SETTLEMENT_FAILED":
                        settlement_operation = "FAILED"
            elapsed = (time.perf_counter() - call_started) * 1000
            if not not_sent:
                calls += 1
                if metrics is not None:
                    metrics.record_generation(path="CLOUD", provider=binding.provider,
                        model=binding.model, usage_start=usage_start, latency_ms=elapsed,
                        status="truncated" if generation_status == "TRUNCATED" else "error" if provider_failed else "ok")
            usages = metrics.usage.calls[usage_start:] if metrics is not None else []
            attempt = {"attempt_id": attempt_id, "role": role, "provider": binding.provider,
                "model": binding.model, "execution_kind": "SIMULATED" if binding.simulated else "BLOCKED_REAL",
                "envelope_identity": envelope.identity, "latency_ms": elapsed, "error_class": error_class,
                "finish_reason": finish_reason, "generation_status": generation_status,
                "send_status": send_status, "settlement_operation": settlement_operation,
                "primary_outcome_before_settlement_failure": primary_outcome,
                "actual_tokens_or_UNKNOWN": [{"input": u.input_tokens, "output": u.output_tokens} for u in usages] or "UNKNOWN",
                "settlement_status": "NOT_SENT" if not_sent and settlement_operation != "FAILED" else "UNKNOWN", "cost_provenance": "NO_BILLING_RECEIPT",
                "actual_cost": "UNKNOWN", "result_validation": validation}
            row["attempts"].append(attempt)
            if durable is not None:
                state = 'NOT_SENT' if not_sent and settlement_operation != 'FAILED' else 'COMPLETED' if send_status=='RESPONSE_RECEIVED' and settlement_operation=='COMPLETED' else 'UNKNOWN'
                try:
                    lifecycle.repository.finish_attempt(run_id,claim.owner,attempt_id,state,attempt)
                except Exception:
                    return self._terminal(payload,self._error_result(run_id,payload['plan'],'ATTEMPT_PERSISTENCE_FAILED',model_calls=calls))
            row["result_validation"] = error or "PASS"
            row["provider_failure_reason"] = error_class if provider_failed or not_sent else None
            if payload["is_cancelled"]():
                return self._terminal(payload, self._error_result(run_id, payload["plan"], "CANCELLED", model_calls=calls))
            if validation == "VALIDATION_ERROR":
                # Fail closed before finalize can rewrite the error or release
                # an evidence fallback. No candidate or internal error is exposed.
                row["total_latency_ms"] = (time.perf_counter() - started) * 1000
                return self._terminal(payload, self._error_result(run_id, payload["plan"], error, model_calls=calls))
            reason = escalation_reason(policy, role=role, error=error, answer=answer, provider_failed=provider_failed)
            if ordinal == 1 and reason and not not_sent and not payload["is_cancelled"]():
                row["escalation_reason"] = reason
                role = "chat_expensive"
                continue
            break
        row["total_latency_ms"] = (time.perf_counter() - started) * 1000
        if not_sent:
            return self._terminal(payload, self._error_result(run_id, payload["plan"], error, model_calls=calls))
        return {**payload, "answer": answer, "generation_error": error, "model_calls": calls,
                "evidence_only": False, "answer_degradation": payload.get("answer_degradation")}

    def _reserve_cloud(self, payload: dict[str, Any]) -> BudgetReservation:
        settings = payload["settings"]
        if self.budget_gate is None:
            raise BudgetDenied("BUDGET_GATE_UNAVAILABLE")
        if settings.cloud_cost_estimate_microunits <= 0:
            raise BudgetDenied("CLOUD_COST_ESTIMATE_REQUIRED")
        return self.budget_gate.reserve(run_id=payload["run_id"], provider=settings.cloud_provider,
            model_name=settings.cloud_model, capability="chat",
            estimate_microunits=settings.cloud_cost_estimate_microunits)

    def _fallback_cloud(self, payload: dict[str, Any], model_calls: int,
                        degradation: str | None) -> dict[str, Any]:
        settings = payload["settings"]; scope = payload["scope"]
        route = self.execution_router.choose(prefer_cloud=True, cloud_enabled=settings.cloud_enabled,
            cloud_allowed=bool(scope.knowledge_base_ids) and all(payload["cloud_allowed_by_kb"].get(k, False)
                for k in scope.knowledge_base_ids), cloud_provider_available=payload["cloud_gateway"] is not None)
        code = route.error_code or ("CLOUD_EGRESS_DISABLED" if route.path != "CLOUD" else None)
        if code is not None:
            return self._terminal(payload, self._error_result(payload["run_id"], payload["plan"], code, degradation, model_calls))
        try:
            reservation = self._reserve_cloud(payload)
        except BudgetDenied as exc:
            return self._terminal(payload, self._error_result(payload["run_id"], payload["plan"], str(exc), degradation, model_calls))
        next_payload = {**payload, "answer_gateway": payload["cloud_gateway"], "reservation": reservation,
            "execution_route": ExecutionRoute("CLOUD", "LOCAL_FAILURE_CLOUD_FALLBACK"),
            "model_calls": model_calls, "answer_degradation": degradation}
        next_payload.pop("generation_error", None)
        # Exactly one local-failure fallback; CLOUD failures never recurse/retry.
        return self._generate(next_payload)

    def _validate(self, payload: dict[str, Any]) -> AnswerResult:
        if "result" in payload:
            return payload["result"]
        run_id = str(payload["run_id"])
        plan: QueryPlan = payload["plan"]
        bundle: EvidenceBundle = payload["bundle"]
        answer = str(payload["answer"])
        answer_degradation = payload.get("answer_degradation")
        model_calls = int(payload["model_calls"])
        evidence_only = bool(payload["evidence_only"])
        reasons = ()
        if evidence_only:
            self.knowledge_gateway.evidence.hardening.observe_evidence_answer(answer, bundle.snapshots,
                [item.chunk for item in bundle.selected], plan)
        if not evidence_only:
            finalized = self.knowledge_gateway.evidence.finalize_answer(run_id, answer, bundle.snapshots,
                [item.chunk for item in bundle.selected], plan, payload.get("generation_error"))
            answer = finalized.answer
            reasons = tuple(x for x in (finalized.rejection, finalized.fallback) if x)
            if finalized.error:
                return self._error_result(run_id, plan, finalized.error, answer_degradation, model_calls)
        cited_labels = tuple(dict.fromkeys(MARKER.findall(answer)))
        if any(label not in bundle.labels for label in cited_labels):
            return self._error_result(run_id, plan, "INVALID_CITATION", answer_degradation, model_calls)
        cited_snapshots = tuple(snapshot for snapshot in bundle.snapshots if snapshot.label in cited_labels)
        answer_type = "source_excerpt" if evidence_only else "fallback" if "EVIDENCE_ONLY_FALLBACK" in reasons else "generated"
        error = self.knowledge_gateway.evidence.commit_check.check(
            answer, cited_snapshots, plan.evidence_plan, answer_type=answer_type, citations=cited_labels)
        if error:
            return self._error_result(run_id, plan, error, answer_degradation, model_calls)
        cost = payload["settings"].cloud_cost_estimate_microunits if payload["reservation"] is not None else 0
        return AnswerResult(
            run_id,
            answer,
            cited_labels,
            plan,
            Trace(answer_degradation, reasons, model_calls),
            None,
            cost,
            cited_snapshots,
            answer_type,
        )

    @staticmethod
    def _privacy_configuration(question: str) -> bool:
        return bool(re.search(r"Langfuse|RAG_CLOUD|LANGFUSE_|cloud_allowed|外发|云端(?:边界|配置|开关|权限)|正文采集", question, re.I))

    @staticmethod
    def _error_result(
        run_id: str,
        plan: QueryPlan,
        code: str,
        degradation_code: str | None = None,
        model_calls: int | None = None,
    ) -> AnswerResult:
        return AnswerResult(
            run_id,
            "",
            (),
            plan,
            Trace(degradation_code, (code,), plan.model_calls if model_calls is None else model_calls),
            code,
        )

    @staticmethod
    def _terminal(payload: dict[str, Any], result: AnswerResult) -> dict[str, Any]:
        return {"result": result, **payload}


__all__ = ["LangChainQuickChain", "QuickSettings"]
