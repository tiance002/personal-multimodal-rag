from __future__ import annotations

from collections.abc import Iterable

from backend.app.domain.errors import EvidenceIntegrityError
from backend.app.domain.evidence import freeze_evidence
from backend.app.domain.models import ChunkRecord, EvidenceSnapshot


class EvidenceAccumulator:
    """Collect, deduplicate and freeze server-verified evidence for one run."""

    def __init__(self) -> None:
        self._chunks: dict[tuple[str, str], ChunkRecord] = {}
        self._frozen: tuple[EvidenceSnapshot, ...] | None = None

    def add(self, chunks: Iterable[ChunkRecord]) -> None:
        if self._frozen is not None:
            raise EvidenceIntegrityError("evidence is already frozen")
        for chunk in chunks:
            if not chunk.chunk_id or not chunk.version_id:
                raise EvidenceIntegrityError("evidence requires chunk_id and version_id")
            if not chunk.content.strip():
                raise EvidenceIntegrityError("evidence quote must not be empty")
            if not chunk.locator:
                raise EvidenceIntegrityError("evidence requires a locator")
            key = (chunk.version_id, chunk.chunk_id)
            previous = self._chunks.get(key)
            if previous is not None and (previous.content != chunk.content or previous.locator != chunk.locator):
                raise EvidenceIntegrityError("evidence identity resolved to conflicting content")
            self._chunks[key] = chunk

    @property
    def chunks(self) -> tuple[ChunkRecord, ...]:
        return tuple(self._chunks.values())

    def label_for(self, chunk: ChunkRecord) -> str:
        key = (chunk.version_id, chunk.chunk_id)
        if key not in self._chunks:
            raise EvidenceIntegrityError("chunk was not accepted into evidence")
        return f"E{list(self._chunks).index(key) + 1}"

    def freeze(self) -> tuple[EvidenceSnapshot, ...]:
        if self._frozen is None:
            snapshots: list[EvidenceSnapshot] = []
            for index, chunk in enumerate(self._chunks.values(), start=1):
                snapshots.append(
                    freeze_evidence(
                        f"E{index}",
                        chunk.version_id,
                        chunk.chunk_id,
                        chunk.content,
                        chunk.locator,
                    )
                )
            self._frozen = tuple(snapshots)
        return self._frozen


__all__ = ["EvidenceAccumulator"]
