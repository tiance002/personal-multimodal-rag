from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from backend.app.application.budget import BudgetDenied, BudgetGate, BudgetReservation
from backend.app.application.answer_validation import AnswerValidator
from backend.app.application.citations import CitationService
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.model_policy import ModelPolicy
from backend.app.application.quality import QualityGate
from backend.app.application.query_router import QueryRouter
from backend.app.application.retrieval import HybridRetriever, RetrievalItem, RetrievalResult
from backend.app.domain.scope import Scope


@dataclass(frozen=True)
class RagSettings:
    cloud_enabled: bool = False
    prefer_cloud: bool = False
    local_query_enabled: bool = False
    local_query_timeout_seconds: float = 4.0
    answer_timeout_seconds: float = 30.0
    cloud_cost_estimate_microunits: int = 1
    cloud_provider: str = "cloud"
    cloud_model: str = "configured"


@dataclass(frozen=True)
class Trace:
    """Degradation, evidence decisions and local chat-call count for one run."""

    degradation_code: str | None = None
    reason_codes: tuple[str, ...] = ()
    model_calls: int = 0


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
    """Fixed Quick-mode answer pipeline owned by the project's RAG Core."""

    def __init__(
        self,
        retriever: HybridRetriever,
        citation_service: CitationService,
        *,
        local_query_gateway: Any | None = None,
        answer_gateway: Any | None = None,
        cloud_allowed_by_kb: dict[str, bool] | None = None,
        budget_gate: BudgetGate | None = None,
        on_retrieval: Callable[[str, Sequence[RetrievalItem]], None] | None = None,
    ) -> None:
        self.retriever = retriever
        self.citations = citation_service
        self.local_query_gateway = local_query_gateway
        self.answer_gateway = answer_gateway
        self.cloud_allowed_by_kb = cloud_allowed_by_kb or {}
        self.budget_gate = budget_gate
        self.on_retrieval = on_retrieval
        self.model_policy = ModelPolicy()
        self.quality_gate = QualityGate()
        self.answer_validator = AnswerValidator()
        self.query_router = QueryRouter()
        self.context_builder = ContextBuilder()

    def answer_query(self, question: str, scope: Scope, settings: RagSettings | None = None, *, run_id: str | None = None) -> AnswerResult:
        settings = settings or RagSettings()
        run_id = run_id or str(uuid.uuid4())
        reservation: BudgetReservation | None = None
        model_calls = 0

        if settings.prefer_cloud and settings.cloud_enabled:
            if any(not self.cloud_allowed_by_kb.get(kb_id, False) for kb_id in scope.knowledge_base_ids):
                return self._failure(run_id, question, "CLOUD_EGRESS_DISABLED")

        evidence_plan = self.query_router.plan(question)
        query_plan, degradation = self.model_policy.build_query_plan(
            question, None, enabled=False, timeout_seconds=settings.local_query_timeout_seconds,
        )
        retrieval = self.retriever.retrieve(scope, question, query_plan=query_plan)
        if not retrieval.items and settings.local_query_enabled and self.local_query_gateway is not None:
            model_calls += 1
            expanded, degradation = self.model_policy.build_query_plan(
                question, self.local_query_gateway, enabled=True, timeout_seconds=settings.local_query_timeout_seconds,
            )
            if expanded.terms != query_plan.terms:
                query_plan = expanded
                retrieval = self.retriever.retrieve(scope, question, query_plan=query_plan)

        decision = self.quality_gate.evaluate(retrieval, evidence_plan)
        if decision.missing_targets:
            targeted_query = " ".join(target.search_query for target in decision.missing_targets)
            targeted = self.retriever.retrieve(scope, targeted_query)
            seen = {item.chunk.chunk_id for item in retrieval.items}
            combined = list(retrieval.items)
            for item in targeted.items:
                if item.chunk.chunk_id not in seen:
                    combined.append(item)
                    seen.add(item.chunk.chunk_id)
            retrieval = RetrievalResult(query_plan, combined, tuple(sorted(set(retrieval.sources + targeted.sources))))
            decision = self.quality_gate.evaluate(retrieval, evidence_plan)
        if self.on_retrieval is not None:
            self.on_retrieval(run_id, retrieval.items)

        selected = self.context_builder.select(retrieval.items)
        if not selected and retrieval.items:
            return AnswerResult(run_id, "", (), query_plan, Trace(degradation, ("SECTION_TRUNCATED",), model_calls), "SECTION_TRUNCATED")
        decision = self.quality_gate.evaluate(
            RetrievalResult(query_plan, selected, retrieval.sources), evidence_plan,
        )
        if not decision.accepted:
            if evidence_plan.targets and selected and decision.missing_targets:
                partial = self._partial_answer(run_id, query_plan, degradation, evidence_plan, selected, decision.missing_targets, model_calls)
                if partial is not None:
                    return partial
            reason = decision.reason.value if decision.reason is not None else "NO_CANDIDATES"
            return AnswerResult(run_id, "", (), query_plan, Trace(degradation, (reason,), model_calls), reason)

        context, labels = self.context_builder.build(run_id, selected, self.citations)
        if not labels:
            return AnswerResult(run_id, "", (), query_plan, Trace(degradation, ("SECTION_TRUNCATED",), model_calls), "SECTION_TRUNCATED")
        answer_degradation = degradation
        evidence_only = self.answer_gateway is None

        if settings.prefer_cloud and settings.cloud_enabled and self.answer_gateway is not None:
            if self.budget_gate is None:
                return AnswerResult(run_id, "", (), query_plan, Trace(degradation, ("BUDGET_GATE_UNAVAILABLE",), model_calls), "BUDGET_GATE_UNAVAILABLE")
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
                return AnswerResult(run_id, "", (), query_plan, Trace(degradation, (code,), model_calls), code)

        if self.answer_gateway is None:
            answer = "基于检索到的证据：\n" + context
        else:
            try:
                if not (settings.prefer_cloud and settings.cloud_enabled):
                    model_calls += 1
                answer = self.answer_gateway.answer(
                    "Answer only from the supplied evidence. Cite every factual statement with its exact "
                    f"[E#] label; say when a requested fact is missing.\nQuestion: {question}\nEvidence:\n{context}",
                    settings.answer_timeout_seconds,
                )
                if reservation is not None:
                    self.budget_gate.settle(reservation.reservation_id, settings.cloud_cost_estimate_microunits)
            except Exception:
                if reservation is not None:
                    self.budget_gate.mark_unknown(reservation.reservation_id)
                answer = "本地回答模型暂不可用，以下为可回读证据：\n" + context
                evidence_only = True
                answer_degradation = ";".join(code for code in (degradation, "MODEL_UNAVAILABLE") if code) or "MODEL_UNAVAILABLE"

        cited_labels = tuple(dict.fromkeys(re.findall(r"\bE\d+\b", answer)))
        if any(label not in labels for label in cited_labels):
            return AnswerResult(run_id, "", (), query_plan, Trace(answer_degradation, ("INVALID_CITATION",), model_calls), "INVALID_CITATION")
        snapshots = tuple(self.citations.snapshots[(run_id, label)] for label in labels)
        validation_error = None if evidence_only else self.answer_validator.validate(answer, snapshots, evidence_plan)
        if validation_error is not None:
            return AnswerResult(run_id, "", (), query_plan, Trace(answer_degradation, (validation_error,), model_calls), validation_error)
        cost = settings.cloud_cost_estimate_microunits if reservation is not None else 0
        return AnswerResult(run_id, answer, cited_labels, query_plan, Trace(answer_degradation, (), model_calls), None, cost)

    def _failure(self, run_id: str, question: str, code: str) -> AnswerResult:
        return AnswerResult(run_id, "", (), question, Trace(reason_codes=(code,)), code)

    def _partial_answer(
        self,
        run_id: str,
        query_plan: Any,
        degradation: str | None,
        evidence_plan: Any,
        selected: Sequence[RetrievalItem],
        missing_targets: tuple[Any, ...],
        model_calls: int,
    ) -> AnswerResult | None:
        covered = [target for target in evidence_plan.targets if target not in missing_targets]
        if not covered:
            return None
        labels_by_chunk: dict[str, str] = {}
        lines: list[str] = []
        for target in covered:
            match = next(
                ((item, fact) for item in selected if (fact := self.quality_gate.supporting_fact(item.chunk.content, target))),
                None,
            )
            if match is None:
                continue
            item, fact = match
            label = labels_by_chunk.get(item.chunk.chunk_id)
            if label is None:
                label = self.citations.freeze(run_id, item.chunk).label
                labels_by_chunk[item.chunk.chunk_id] = label
            lines.append(f"{target.subject}：{fact} [{label}]")
        if not lines:
            return None
        absent = "、".join(target.subject for target in missing_targets)
        lines.append(f"{absent}：资料中没有足够证据，无法回答这一部分。")
        if evidence_plan.kind == "comparison":
            lines.append("因此无法计算差额。")
        return AnswerResult(
            run_id,
            "\n".join(lines),
            tuple(dict.fromkeys(labels_by_chunk.values())),
            query_plan,
            Trace(degradation, ("PARTIAL_EVIDENCE",), model_calls),
        )
