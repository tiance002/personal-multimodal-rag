from __future__ import annotations

import hashlib
from dataclasses import dataclass

from backend.app.domain.errors import EvidenceIntegrityError
from backend.app.domain.models import EvidenceSnapshot
from backend.app.domain.evidence import freeze_evidence

from backend.app.application.retrieval import ChunkRecord


@dataclass(frozen=True)
class CitationChunk:
    chunk_id: str
    document_id: str
    knowledge_base_id: str
    version_id: str
    content: str
    locator: dict[str, object]


@dataclass(frozen=True)
class CitationDetail:
    citation_id: str
    label: str
    version_id: str
    chunk_id: str
    quote: str
    locator: dict[str, object]
    current_status: str


class InMemoryCitationStore:
    def __init__(self) -> None:
        self.chunks: dict[tuple[str, str], CitationChunk] = {}
        self.current_versions: dict[str, str] = {}

    def add(self, chunk: CitationChunk | ChunkRecord, *, is_current: bool = True) -> None:
        normalized = chunk if isinstance(chunk, CitationChunk) else CitationChunk(
            chunk.chunk_id,
            chunk.document_id,
            chunk.knowledge_base_id,
            chunk.version_id,
            chunk.content,
            chunk.locator,
        )
        self.chunks[(normalized.version_id, normalized.chunk_id)] = normalized
        if is_current:
            self.current_versions[normalized.document_id] = normalized.version_id

    def get(self, version_id: str, chunk_id: str) -> CitationChunk | None:
        return self.chunks.get((version_id, chunk_id))

    def mark_current(self, document_id: str, version_id: str) -> None:
        self.current_versions[document_id] = version_id


class CitationService:
    def __init__(self, store: InMemoryCitationStore) -> None:
        self.store = store
        self.snapshots: dict[tuple[str, str], EvidenceSnapshot] = {}

    def freeze(self, run_id: str, chunk: CitationChunk | ChunkRecord, label: str | None = None) -> CitationDetail:
        normalized = chunk if isinstance(chunk, CitationChunk) else CitationChunk(
            chunk.chunk_id,
            chunk.document_id,
            chunk.knowledge_base_id,
            chunk.version_id,
            chunk.content,
            chunk.locator,
        )
        self.store.add(normalized, is_current=False)
        label = label or f"E{sum(1 for run, _ in self.snapshots if run == run_id) + 1}"
        snapshot = freeze_evidence(label, normalized.version_id, normalized.chunk_id, normalized.content, normalized.locator)
        self.snapshots[(run_id, label)] = snapshot
        return self._detail(run_id, label, normalized)

    def resolve(self, run_id: str, citation_id: str) -> CitationDetail:
        snapshot = self.snapshots.get((run_id, citation_id))
        if snapshot is None or snapshot.chunk_id is None:
            raise EvidenceIntegrityError("unknown citation")
        chunk = self.store.get(snapshot.version_id, snapshot.chunk_id)
        if chunk is None or hashlib.sha256(chunk.content.encode("utf-8")).hexdigest() != snapshot.quote_sha256:
            raise EvidenceIntegrityError("citation source changed or is unreadable")
        return self._detail(run_id, citation_id, chunk)

    def _detail(self, run_id: str, label: str, chunk: CitationChunk) -> CitationDetail:
        current = self.store.current_versions.get(chunk.document_id)
        status = "current" if current in {None, chunk.version_id} else "superseded"
        return CitationDetail(label, label, chunk.version_id, chunk.chunk_id, chunk.content, chunk.locator, status)
