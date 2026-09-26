from __future__ import annotations

from collections.abc import Iterable, Mapping, Protocol, Sequence
from typing import Any

from backend.app.domain.models import ChunkDraft, EvidenceSnapshot


class KnowledgeRepository(Protocol):
    def list_active_chunks(self, knowledge_base_ids: Sequence[str], document_ids: Sequence[str] | None = None) -> Iterable[Mapping[str, Any]]: ...

    def get_chunk(self, chunk_id: str) -> Mapping[str, Any] | None: ...

    def write_chunks(self, version_id: str, chunks: Sequence[ChunkDraft]) -> None: ...


class EvidenceRepository(Protocol):
    def resolve_evidence(self, snapshot: EvidenceSnapshot) -> Mapping[str, Any] | None: ...


__all__ = ["EvidenceRepository", "KnowledgeRepository"]
