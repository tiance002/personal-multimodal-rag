from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.application.retrieval import RetrievalResult


class QualityReason(StrEnum):
    """Reason codes the V1 gate can actually produce.

    The design vocabulary also names `LOW_COVERAGE`, `SECTION_TRUNCATED`,
    `SEMANTIC_MISMATCH`, `VERSION_CONFLICT` and `INDEX_ERROR`.  Those gates are
    not implemented in V1, so they are intentionally absent here rather than
    declared-but-never-returned.  `NO_EVIDENCE_AFTER_RETRY` is gone with the
    retry pass: a rejected retrieval is terminal, not retried with the same
    inputs.
    """

    NO_CANDIDATES = "NO_CANDIDATES"


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    reason: QualityReason | None = None


class QualityGate:
    def evaluate(self, result: RetrievalResult) -> QualityDecision:
        if not result.items:
            return QualityDecision(False, QualityReason.NO_CANDIDATES)
        return QualityDecision(True)
