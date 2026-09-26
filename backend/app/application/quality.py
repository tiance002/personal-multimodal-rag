from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from backend.app.application.query_router import EvidencePlan, EvidenceTarget
from backend.app.application.retrieval import RetrievalResult
from backend.app.domain.models import ChunkRecord


class QualityReason(StrEnum):
    """Reason codes produced by the deterministic evidence gate."""

    NO_CANDIDATES = "NO_CANDIDATES"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    reason: QualityReason | None = None
    missing_targets: tuple[EvidenceTarget, ...] = ()


class QualityGate:
    def evaluate(self, result: RetrievalResult, plan: EvidencePlan | None = None) -> QualityDecision:
        if not result.items:
            return QualityDecision(False, QualityReason.NO_CANDIDATES)
        return self.evaluate_chunks([item.chunk for item in result.items], plan)

    def evaluate_chunks(self, chunks: list[ChunkRecord] | tuple[ChunkRecord, ...], plan: EvidencePlan | None = None) -> QualityDecision:
        if not chunks:
            return QualityDecision(False, QualityReason.NO_CANDIDATES)
        if plan is not None and plan.kind == "relation" and plan.relation_subjects:
            left, right = plan.relation_subjects
            relationship = re.compile(r"关系|关联|依赖|属于|连接|连接到|组成|包含")
            if not any(
                left in clause and right in clause and relationship.search(clause)
                for chunk in chunks for clause in re.split(r"[。；，,！？\n]", chunk.content)
            ):
                return QualityDecision(False, QualityReason.INSUFFICIENT_EVIDENCE)
        if plan is not None and plan.targets:
            missing = tuple(
                target for target in plan.targets
                if not any(self.supporting_fact(chunk.content, target) for chunk in chunks)
            )
            if missing:
                return QualityDecision(False, QualityReason.INSUFFICIENT_EVIDENCE, missing)
        return QualityDecision(True)

    @staticmethod
    def supporting_fact(content: str, target: EvidenceTarget) -> str | None:
        subject = re.escape(target.subject)
        attribute = re.escape(target.attribute)
        pattern = re.compile(subject + r"[^。；，,！？\n]{0,8}" + attribute + r"[^。；，,！？\n]{0,20}\d", re.IGNORECASE)
        for clause in re.split(r"[。；，,！？\n]", content):
            if pattern.search(clause):
                return clause.strip()
        return None
