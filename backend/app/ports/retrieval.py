from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery

if TYPE_CHECKING:
    from backend.app.application.context_expansion import NeighborMetadata


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

    def vector_candidates(
        self,
        scope: Scope,
        vector: Sequence[float],
        limit: int,
        *,
        profile_id: str | None = None,
    ) -> list[RankedHit]:
        """Rank chunks by embedding similarity, best first."""

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        """Read one chunk back, regardless of version, for citation freezing."""

    def list_active_chunks(self, scope: Scope, limit: int | None = None) -> list[ChunkRecord]:
        """Read active chunks in a scope; `limit` bounds the scan when given."""


@dataclass(frozen=True)
class ContextNeighborRow:
    """Read-side context only; offset 0 is a revalidated original seed.

    This is not a RankedHit and grants no new retrieval/citation permission.
    """

    seed_id: str
    offset: int
    metadata: NeighborMetadata


@dataclass(frozen=True)
class ContextNeighborReason:
    code: str
    seed_id: str
    offset: int | None = None


@dataclass(frozen=True)
class ContextNeighborRead:
    rows: tuple[ContextNeighborRow, ...] = ()
    reasons: tuple[ContextNeighborReason, ...] = ()


@runtime_checkable
class ContextNeighborReader(Protocol):
    """Optional bounded metadata read, separate from RetrievalRepository.

    Both seeds and neighbors must be queried under server Scope, current
    version, ready/nondeleted state and fully proved section/page boundaries.
    At most ten selected seeds and three rows per seed (including offset 0).
    Unavailable/ambiguous boundaries admit no neighbor. No caller is wired yet.
    """

    def read_context_rows(
        self, scope: Scope, seeds: Sequence[ChunkRecord], *, max_seeds: int = 10,
    ) -> ContextNeighborRead: ...


__all__ = [
    "RetrievalRepository", "ContextNeighborReader", "ContextNeighborRow",
    "ContextNeighborRead", "ContextNeighborReason",
]
