from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from backend.app.application.citations import CitationService
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.budget import BudgetDenied, BudgetGate, BudgetReservation
from backend.app.application.model_policy import ModelPolicy
from backend.app.application.quality import QualityGate
from backend.app.application.retrieval import HybridRetriever, RetrievalResult
from backend.app.domain.scope import Scope


@dataclass(frozen=True)
class RagSettings:
    cloud_enabled: bool = False
    prefer_cloud: bool = False
    local_query_enabled: bool = True
    local_query_timeout_seconds: float = 4.0
    answer_timeout_seconds: float = 30.0
    cloud_cost_estimate_microunits: int = 1
    cloud_provider: str = "cloud"
    cloud_model: str = "configured"


@dataclass(frozen=True)
class Trace:
    degradation_code: str | None = None
    retry_count: int = 0
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerResult:
    run_id: str
    answer: str
    citations: tuple[str, ...]
    query_plan: Any
    trace: Trace
    error_code: str | None = None
    cost_microunits: int = 0


class RAGOrchestrator:
    def __init__(
        self,
        retriever: HybridRetriever,
        citation_service: CitationService,
        *,
        local_query_gateway: Any | None = None,
        answer_gateway: Any | None = None,
        cloud_allowed_by_kb: dict[str, bool] | None = None,
        budget_gate: BudgetGate | None = None,
    ) -> None:
        self.retriever = retriever
        self.citations = citation_service
        self.local_query_gateway = local_query_gateway
        self.answer_gateway = answer_gateway
        self.cloud_allowed_by_kb = cloud_allowed_by_kb or {}
        self.budget_gate = budget_gate
        self.model_policy = ModelPolicy()
        self.quality_gate = QualityGate()
        self.context_builder = ContextBuilder()

    def answer_query(self, question: str, scope: Scope, settings: RagSettings | None = None, *, run_id: str | None = None) -> AnswerResult:
        settings = settings or RagSettings()
        run_id = run_id or str(uuid.uuid4())
        reservation: BudgetReservation | None = None
        if settings.prefer_cloud and settings.cloud_enabled:
            if any(not self.cloud_allowed_by_kb.get(kb_id, False) for kb_id in scope.knowledge_base_ids):
                return AnswerResult(run_id, "", (), question, Trace(reason_codes=("CLOUD_EGRESS_DISABLED",)), "CLOUD_EGRESS_DISABLED")
        query_plan, degradation = self.model_policy.build_query_plan(
            question,
            self.local_query_gateway,
            enabled=settings.local_query_enabled,
            timeout_seconds=settings.local_query_timeout_seconds,
        )
        retrieval = self.retriever.retrieve(scope, question, query_plan=query_plan)
        decision = self.quality_gate.evaluate(retrieval)
        retry_count = 0
        if not decision.accepted:
            retry_count = 1
            retrieval = self.retriever.retrieve(scope, question, query_plan=query_plan)
            decision = self.quality_gate.evaluate(retrieval)
        if not decision.accepted:
            reason = "NO_EVIDENCE_AFTER_RETRY"
            return AnswerResult(run_id, "", (), query_plan, Trace(degradation, retry_count, (reason,)), reason)
        context, labels = self.context_builder.build(run_id, retrieval.items, self.citations)
        answer_degradation = degradation
        if settings.prefer_cloud and settings.cloud_enabled and self.answer_gateway is not None:
            if self.budget_gate is None:
                return AnswerResult(run_id, "", (), query_plan, Trace(degradation, retry_count, ("BUDGET_GATE_UNAVAILABLE",)), "BUDGET_GATE_UNAVAILABLE")
            try:
                reservation = self.budget_gate.reserve(
                    run_id=run_id,
                    provider=settings.cloud_provider,
                    model_name=settings.cloud_model,
                    capability="chat",
                    estimate_microunits=settings.cloud_cost_estimate_microunits,
                )
            except BudgetDenied as exc:
                code = str(exc) or "MONTHLY_BUDGET_EXCEEDED"
                return AnswerResult(run_id, "", (), query_plan, Trace(degradation, retry_count, (code,)), code)
        if self.answer_gateway is None:
            answer = "基于检索到的证据：\n" + context
        else:
            try:
                answer = self.answer_gateway.answer(f"Question: {question}\nEvidence:\n{context}", settings.answer_timeout_seconds)
                if reservation is not None:
                    self.budget_gate.settle(reservation.reservation_id, settings.cloud_cost_estimate_microunits)
            except Exception:
                if reservation is not None:
                    self.budget_gate.mark_unknown(reservation.reservation_id)
                answer = "本地回答模型暂不可用，以下为可回读证据：\n" + context
                answer_degradation = ";".join(code for code in (degradation, "MODEL_UNAVAILABLE") if code) or "MODEL_UNAVAILABLE"
        cited_labels = tuple(dict.fromkeys(re.findall(r"\bE\d+\b", answer)))
        if any(label not in labels for label in cited_labels):
            return AnswerResult(run_id, "", tuple(labels), query_plan, Trace(answer_degradation, retry_count, ("INVALID_CITATION",)), "INVALID_CITATION")
        return AnswerResult(run_id, answer, cited_labels or tuple(labels), query_plan, Trace(answer_degradation, retry_count), None, settings.cloud_cost_estimate_microunits if reservation is not None else 0)
