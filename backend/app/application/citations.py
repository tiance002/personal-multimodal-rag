from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain.errors import EvidenceIntegrityError
from backend.app.domain.evidence import EvidenceResolver, freeze_evidence
from backend.app.domain.models import ChunkRecord, EvidenceSnapshot


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


def as_citation_chunk(chunk: CitationChunk | ChunkRecord) -> CitationChunk:
    if isinstance(chunk, CitationChunk):
        return chunk
    return CitationChunk(
        chunk.chunk_id,
        chunk.document_id,
        chunk.knowledge_base_id,
        chunk.version_id,
        chunk.content,
        chunk.locator,
    )


class InMemoryCitationStore:
    def __init__(self) -> None:
        self.chunks: dict[tuple[str, str], CitationChunk] = {}
        self.current_versions: dict[str, str] = {}

    def add(self, chunk: CitationChunk | ChunkRecord, *, is_current: bool = True) -> None:
        normalized = as_citation_chunk(chunk)
        self.chunks[(normalized.version_id, normalized.chunk_id)] = normalized
        if is_current:
            self.current_versions[normalized.document_id] = normalized.version_id

    def get(self, version_id: str, chunk_id: str) -> CitationChunk | None:
        return self.chunks.get((version_id, chunk_id))

    def mark_current(self, document_id: str, version_id: str) -> None:
        self.current_versions[document_id] = version_id


class CitationService:
    """Freezes answer evidence and re-reads it under hash validation.

    Resolution delegates to the domain `EvidenceResolver` so the integrity rule
    (quote hash matches, quote still readable in the frozen version) exists in
    exactly one place.
    """

    def __init__(self, store: InMemoryCitationStore) -> None:
        self.store = store
        self.snapshots: dict[tuple[str, str], EvidenceSnapshot] = {}

    def freeze(self, run_id: str, chunk: CitationChunk | ChunkRecord, label: str | None = None) -> CitationDetail:
        normalized = as_citation_chunk(chunk)
        for (run, old_label), old in self.snapshots.items():
            if run != run_id:
                continue
            same = (old.version_id, old.chunk_id) == (normalized.version_id, normalized.chunk_id)
            if label == old_label and not same:
                raise EvidenceIntegrityError("citation label identity conflict")
            if same:
                if old.quote != normalized.content or old.locator != normalized.locator:
                    raise EvidenceIntegrityError("citation evidence identity conflict")
                if label is not None and label != old_label:
                    raise EvidenceIntegrityError("citation identity already has a label")
                return self._detail(old_label, normalized)
        self.store.add(normalized, is_current=False)
        label = label or f"E{sum(1 for run, _ in self.snapshots if run == run_id) + 1}"
        snapshot = freeze_evidence(label, normalized.version_id, normalized.chunk_id, normalized.content, normalized.locator)
        self.snapshots[(run_id, label)] = snapshot
        return self._detail(label, normalized)

    def resolve(self, run_id: str, citation_id: str) -> CitationDetail:
        snapshot = self.snapshots.get((run_id, citation_id))
        if snapshot is None or snapshot.chunk_id is None:
            raise EvidenceIntegrityError("unknown citation")
        EvidenceResolver({citation_id: snapshot}, self._read_quote).resolve(citation_id)
        chunk = self.store.get(snapshot.version_id, snapshot.chunk_id)
        if chunk is None:
            raise EvidenceIntegrityError("citation source is unreadable")
        return self._detail(citation_id, chunk)

    def _read_quote(self, version_id: str, chunk_id: str | None) -> str:
        chunk = self.store.get(version_id, chunk_id or "")
        if chunk is None:
            raise EvidenceIntegrityError("citation source is unreadable")
        return chunk.content

    def _detail(self, label: str, chunk: CitationChunk) -> CitationDetail:
        current = self.store.current_versions.get(chunk.document_id)
        status = "current" if current in {None, chunk.version_id} else "superseded"
        return CitationDetail(label, label, chunk.version_id, chunk.chunk_id, chunk.content, chunk.locator, status)
