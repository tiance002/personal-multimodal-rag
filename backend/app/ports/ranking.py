from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from dataclasses import dataclass

from backend.app.domain.models import ChunkRecord, RankedHit


@dataclass(frozen=True)
class RerankResult:
    """Scores correspond to hits; original RankedHit provenance stays intact."""
    hits: tuple[RankedHit, ...]
    relevance_scores: tuple[float, ...]
    latency_ms: float
    usage_actual: dict | None


class CandidateRanker(Protocol):
    """Optional second stage. Return only supplied, authorized chunk IDs.

    Future network adapters require the same egress/budget gates as generation;
    the V1 composition root does not wire a network or heavyweight adapter.
    """

    def rank(self, question: str, hits: Sequence[RankedHit], chunks: Mapping[str, ChunkRecord]) -> Sequence[RankedHit]: ...


class CandidateDiversitySelector(Protocol):
    """Optional MMR/diversity stage after relevance ranking."""

    def select(self, question: str, hits: Sequence[RankedHit], chunks: Mapping[str, ChunkRecord]) -> Sequence[RankedHit]: ...
