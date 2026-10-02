from __future__ import annotations

from backend.app.ports.session_attempts import request_identity
from backend.app.ports.providers import ProviderRequestNotSent

import re
import uuid
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableLambda

from backend.app.application.budget import BudgetDenied, BudgetGate, BudgetReservation
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_gateway import AnswerResult, EvidenceBundle, KnowledgeGateway, QueryPlan, Trace
from backend.app.application.retrieval import RetrievalItem
from backend.app.domain.scope import Scope
from backend.app.application.execution_routing import ExecutionRouter, ExecutionRoute
from backend.app.application.run_metrics import current_metrics
from backend.app.application.answer_hardening import detect_intents, generation_budget, MARKER
from backend.app.application.follow_up import FollowUpResolution, interpretation_data


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
    ) -> None:
        self.knowledge_gateway = knowledge_gateway
        self.answer_gateway = answer_gateway
        self.cloud_answer_gateway = cloud_answer_gateway
        self.budget_gate = budget_gate
        self.execution_router = ExecutionRouter()
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
        }
        return self.runnable.invoke(payload)

    def _prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload["question"])
        scope: Scope = payload["scope"]
        settings: QuickSettings = payload["settings"]
        run_id = str(payload["run_id"])
        cloud_allowed_by_kb: dict[str, bool] = payload["cloud_allowed_by_kb"]
        cloud_gateway = self.cloud_answer_gateway or (self.answer_gateway
            if self.answer_gateway is not None and getattr(self.answer_gateway, "provider_kind", None) != "local" else None)
        execution_route = self.execution_router.choose(
            prefer_cloud=settings.prefer_cloud and not self._privacy_configuration(question), cloud_enabled=settings.cloud_enabled,
            cloud_allowed=bool(scope.knowledge_base_ids) and all(cloud_allowed_by_kb.get(kb_id, False) for kb_id in scope.knowledge_base_ids),
            cloud_provider_available=cloud_gateway is not None,
        )
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
            return self._terminal(payload, self._error_result(run_id, plan, reason))

        bundle = self.knowledge_gateway.evidence.with_context(bundle, run_id, citation_service)
        if not bundle.labels:
            return self._terminal(payload, self._error_result(run_id, plan, "SECTION_TRUNCATED"))

        reservation: BudgetReservation | None = None
        if execution_route.path == "CLOUD":
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
            "evidence_only": answer_gateway is None or self._privacy_configuration(question),
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
            if metrics is not None:
                metrics.hardening.setdefault("generation_routes", []).append({
                    "path": path, "reason": payload["execution_route"].reason, "provider": provider,
                    "cost_basis": "configured_reservation_estimate_not_verified_charge" if reservation else "LOCAL_COMPUTE_NOT_MEASURED",
                    "reserved_microunits": reservation.reserved_microunits if reservation else None})
            try:
                model_calls += 1
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
                    + interpretation_data(payload.get("validated_follow_up"))
                    + f"\nEvidence:\n{bundle.context}"
                )
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
                if metrics is not None:
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
