from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.application.retrieval import RetrievalItem, RetrievalResult


@dataclass(frozen=True)
class EvidenceQualitySignals:
    retrieval_scores: tuple[float, ...]
    top1_topk_gap: float | None
    vector_keyword_agreement: float | None
    number_of_supporting_chunks: int | None
    number_of_source_documents: int
    context_diversity: float
    conflicting_evidence: bool | None = None
    retrieval_confidence: float | None = None


@dataclass(frozen=True)
class EvidenceQuality:
    level: str
    signals: EvidenceQualitySignals
    reasons: tuple[str, ...]
    basis: str = "RETRIEVAL_HEURISTIC_NOT_ANSWER_CORRECTNESS"


class EvidenceQualityAssessor:
    def assess(self, retrieval: RetrievalResult, selected: Sequence[RetrievalItem], *,
               supporting_chunks: int | None = None) -> EvidenceQuality:
        vector = retrieval.candidate_rankings.get("vector", ())[:5]
        keyword = retrieval.candidate_rankings.get("keyword", ())[:5]
        vector_ids, keyword_ids = {hit.chunk_id for hit in vector}, {hit.chunk_id for hit in keyword}
        agreement = len(vector_ids & keyword_ids) / len(vector_ids | keyword_ids) if vector and keyword else None
        # A gap is meaningful only within the original vector score scale.
        scores = tuple(float(hit.raw_score) for hit in vector if hit.raw_score is not None)
        gap = scores[0] - scores[-1] if len(scores) > 1 else None
        documents = len({item.chunk.document_id for item in selected})
        signals = EvidenceQualitySignals(scores, gap, agreement, supporting_chunks,
                                         documents, documents / len(selected) if selected else 0.0)
        level = "LOW" if not selected else "MEDIUM"
        reasons = ["NO_EVIDENCE" if not selected else "RETRIEVED_EVIDENCE", "CONFLICT_NOT_EVALUATED", "CONFIDENCE_NOT_CALIBRATED"]
        if selected and supporting_chunks is not None and supporting_chunks > 0:
            level = "HIGH"
            reasons.append("DETERMINISTIC_TARGET_SUPPORT")
        return EvidenceQuality(level, signals, tuple(reasons))
