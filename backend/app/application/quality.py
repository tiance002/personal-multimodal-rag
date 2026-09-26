from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.application.retrieval import RetrievalResult


class QualityReason(StrEnum):
    NO_CANDIDATES = "NO_CANDIDATES"
    LOW_COVERAGE = "LOW_COVERAGE"
    NO_EVIDENCE_AFTER_RETRY = "NO_EVIDENCE_AFTER_RETRY"


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    reason: QualityReason | None = None


class QualityGate:
    def evaluate(self, result: RetrievalResult) -> QualityDecision:
        if not result.items:
            return QualityDecision(False, QualityReason.NO_CANDIDATES)
        return QualityDecision(True)
