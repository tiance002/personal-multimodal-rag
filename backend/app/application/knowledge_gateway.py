from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from contextlib import nullcontext
from typing import Any

from backend.app.application.answer_validation import AnswerValidator
from backend.app.application.citations import CitationService
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.model_policy import ModelPolicy
from backend.app.application.query_router import EvidencePlan, EvidenceTarget, QueryRouter
from backend.app.application.quality import QualityDecision, QualityGate
from backend.app.application.retrieval import HybridRetriever, RetrievalItem, RetrievalResult
from backend.app.domain.models import EvidenceSnapshot
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery, normalize_query


@dataclass(frozen=True)
class QueryPlan:
    """The server-owned plan shared by Quick and Smart retrieval."""

    question: str
    evidence_plan: EvidencePlan
    retrieval_plan: NormalizedQuery
    degradation_code: str | None = None
    model_calls: int = 0


@dataclass(frozen=True)
class EvidenceBundle:
    """One validated retrieval/evidence boundary before answer generation."""

    plan: QueryPlan
    retrieval: RetrievalResult
    selected: tuple[RetrievalItem, ...]
    decision: QualityDecision
    context: str = ""
    labels: tuple[str, ...] = ()
    snapshots: tuple[EvidenceSnapshot, ...] = ()


@dataclass(frozen=True)
class Trace:
    degradation_code: str | None = None
    reason_codes: tuple[str, ...] = ()
    model_calls: int = 0


@dataclass(frozen=True)
class AnswerResult:
    run_id: str
    answer: str
    citations: tuple[str, ...]
    query_plan: QueryPlan
    trace: Trace
    error_code: str | None = None
    cost_microunits: int = 0
    evidence: tuple[EvidenceSnapshot, ...] = ()


class EvidenceService:
    """Single business owner for planning, coverage, context and validation."""

    def __init__(
        self,
        *,
        query_router: QueryRouter | None = None,
        quality_gate: QualityGate | None = None,
        context_builder: ContextBuilder | None = None,
        answer_validator: AnswerValidator | None = None,
    ) -> None:
        self.query_router = query_router or QueryRouter()
        self.quality_gate = quality_gate or QualityGate()
        self.context_builder = context_builder or ContextBuilder()
        self.answer_validator = answer_validator or AnswerValidator()

    def plan(self, question: str) -> QueryPlan:
        return QueryPlan(question, self.query_router.plan(question), normalize_query(question))

    def with_retrieval_plan(
        self,
        plan: QueryPlan,
        retrieval_plan: NormalizedQuery,
        *,
        degradation_code: str | None = None,
        model_calls: int = 0,
    ) -> QueryPlan:
        return replace(
            plan,
            retrieval_plan=retrieval_plan,
            degradation_code=degradation_code,
            model_calls=model_calls,
        )

    def evaluate(self, retrieval: RetrievalResult, plan: QueryPlan) -> QualityDecision:
        return self.quality_gate.evaluate(retrieval, plan.evidence_plan)

    def evaluate_chunks(
        self,
        chunks: Sequence[Any],
        plan: QueryPlan,
    ) -> QualityDecision:
        return self.quality_gate.evaluate_chunks(list(chunks), plan.evidence_plan)

    def bundle(self, plan: QueryPlan, retrieval: RetrievalResult) -> EvidenceBundle:
        selected = tuple(self.context_builder.select(retrieval.items))
        selected_result = RetrievalResult(
            retrieval.query_plan,
            list(selected),
            retrieval.sources,
            retrieval.reason_codes,
        )
        return EvidenceBundle(plan, retrieval, selected, self.evaluate(selected_result, plan))

    def with_context(
        self,
        bundle: EvidenceBundle,
        run_id: str,
        citations: CitationService,
    ) -> EvidenceBundle:
        context, labels = self.context_builder.build(run_id, bundle.selected, citations)
        snapshots = tuple(citations.snapshots[(run_id, label)] for label in labels)
        return replace(bundle, context=context, labels=tuple(labels), snapshots=snapshots)

    def validate_answer(
        self,
        answer: str,
        snapshots: Sequence[EvidenceSnapshot],
        plan: QueryPlan,
    ) -> str | None:
        return self.answer_validator.validate(answer, snapshots, plan.evidence_plan)

    def partial_answer(
        self,
        *,
        run_id: str,
        plan: QueryPlan,
        selected: Sequence[RetrievalItem],
        missing_targets: tuple[EvidenceTarget, ...],
        citations: CitationService,
    ) -> AnswerResult | None:
        covered = [target for target in plan.evidence_plan.targets if target not in missing_targets]
        if not covered:
            return None
        labels_by_chunk: dict[str, str] = {}
        lines: list[str] = []
        for target in covered:
            match = next(
                (
                    (item, fact)
                    for item in selected
                    if (fact := self.quality_gate.supporting_fact(item.chunk.content, target))
                ),
                None,
            )
            if match is None:
                continue
            item, fact = match
            label = labels_by_chunk.get(item.chunk.chunk_id)
            if label is None:
                label = citations.freeze(run_id, item.chunk).label
                labels_by_chunk[item.chunk.chunk_id] = label
            lines.append(f"{target.subject}：{fact} [{label}]")
        if not lines:
            return None
        absent = "、".join(target.subject for target in missing_targets)
        lines.append(f"{absent}：资料中没有足够证据，无法回答这一部分。")
        if plan.evidence_plan.kind == "comparison":
            lines.append("因此无法计算差额。")
        snapshots = tuple(citations.snapshots[(run_id, label)] for label in labels_by_chunk.values())
        return AnswerResult(
            run_id=run_id,
            answer="\n".join(lines),
            citations=tuple(labels_by_chunk.values()),
            query_plan=plan,
            trace=Trace(plan.degradation_code, ("PARTIAL_EVIDENCE",), plan.model_calls),
            evidence=snapshots,
        )


class KnowledgeGateway:
    """Shared RAG Core gateway used by the Quick chain and Smart tools."""

    def __init__(
        self,
        retriever: HybridRetriever,
        *,
        evidence_service: EvidenceService | None = None,
        model_policy: ModelPolicy | None = None,
        observability: Any | None = None,
    ) -> None:
        self.retriever = retriever
        self.evidence = evidence_service or EvidenceService()
        self.model_policy = model_policy or ModelPolicy()
        self.observability = observability

    def plan(self, question: str) -> QueryPlan:
        return self.evidence.plan(question)

    def retrieve(self, scope: Scope, plan: QueryPlan) -> RetrievalResult:
        return self.retrieve_query(scope, plan.question, query_plan=plan.retrieval_plan)

    def retrieve_query(self, scope: Scope, question: str, *, query_plan: Any = None) -> RetrievalResult:
        observation = (
            self.observability.observation(
                name="knowledge-retrieval",
                as_type="retriever",
                input={"query": question},
                metadata={"knowledge_base_count": len(scope.knowledge_base_ids)},
            )
            if self.observability is not None
            else nullcontext(None)
        )
        with observation as span:
            result = self.retriever.retrieve(scope, question, query_plan=query_plan)
            if span is not None:
                span.update(
                    metadata={
                        "candidate_count": len(result.items),
                        "source_count": len(result.sources),
                        "reason_codes": list(result.reason_codes),
                    }
                )
                if getattr(self.observability, "capture_content", False):
                    span.update(
                        output={
                            "chunk_ids": [item.chunk.chunk_id for item in result.items],
                            "candidate_count": len(result.items),
                        }
                    )
            return result

    def retrieve_question(
        self,
        scope: Scope,
        question: str,
        *,
        local_query_gateway: Any | None = None,
        local_query_enabled: bool = False,
        local_query_timeout_seconds: float = 4.0,
    ) -> tuple[QueryPlan, RetrievalResult]:
        plan = self.plan(question)
        retrieval = self.retrieve(scope, plan)
        if not retrieval.items and local_query_enabled and local_query_gateway is not None:
            expanded, degradation = self.model_policy.build_query_plan(
                question,
                local_query_gateway,
                enabled=True,
                timeout_seconds=local_query_timeout_seconds,
            )
            if expanded.terms != plan.retrieval_plan.terms:
                plan = self.evidence.with_retrieval_plan(
                    plan,
                    expanded,
                    degradation_code=degradation,
                    model_calls=1,
                )
                retrieval = self.retrieve(scope, plan)
            else:
                plan = replace(plan, degradation_code=degradation, model_calls=1)

        decision = self.evidence.evaluate(retrieval, plan)
        if decision.missing_targets:
            targeted_query = " ".join(target.search_query for target in decision.missing_targets)
            targeted = self.retrieve_query(scope, targeted_query)
            retrieval = self._merge(plan, retrieval, targeted)
        return plan, retrieval

    def search(self, scope: Scope, question: str) -> RetrievalResult:
        """Search through the same planner, retriever and targeted coverage path."""

        _plan, retrieval = self.retrieve_question(scope, question)
        return retrieval

    @staticmethod
    def _merge(plan: QueryPlan, first: RetrievalResult, second: RetrievalResult) -> RetrievalResult:
        seen = {item.chunk.chunk_id for item in first.items}
        combined = list(first.items)
        for item in second.items:
            if item.chunk.chunk_id not in seen:
                combined.append(item)
                seen.add(item.chunk.chunk_id)
        return RetrievalResult(
            plan.retrieval_plan,
            combined,
            tuple(sorted(set(first.sources + second.sources))),
            tuple(sorted(set(first.reason_codes + second.reason_codes))),
        )


__all__ = [
    "AnswerResult",
    "EvidenceBundle",
    "EvidenceService",
    "KnowledgeGateway",
    "QueryPlan",
    "Trace",
]
