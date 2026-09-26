from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery


@runtime_checkable
class RetrievalRepository(Protocol):
    """Candidate generation for hybrid retrieval.

    Every implementation must apply the scope filter (knowledge base, plus the
    optional document filter) and the active-version predicate *inside* the
    query that produces candidates.  A superseded or out-of-scope chunk must
    never reach the ranking stage, and candidate generation must always be
    bounded by `limit`.
    """

    def keyword_candidates(self, scope: Scope, query: NormalizedQuery, limit: int) -> list[RankedHit]:
        """Rank chunks by deterministic term overlap, best first."""

    def vector_candidates(self, scope: Scope, vector: Sequence[float], limit: int) -> list[RankedHit]:
        """Rank chunks by embedding similarity, best first."""

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        """Read one chunk back, regardless of version, for citation freezing."""

    def list_active_chunks(self, scope: Scope, limit: int | None = None) -> list[ChunkRecord]:
        """Read active chunks in a scope; `limit` bounds the scan when given."""


__all__ = ["RetrievalRepository"]
