from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

from backend.app.domain.errors import EvidenceIntegrityError
from backend.app.domain.models import EvidenceSnapshot


def _hash_quote(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def freeze_evidence(
    label: str,
    version_id: str,
    chunk_id: str | None,
    quote: str,
    locator: Mapping[str, object],
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        label=label,
        version_id=version_id,
        chunk_id=chunk_id,
        quote=quote,
        quote_sha256=_hash_quote(quote),
        locator=dict(locator),
    )


class EvidenceResolver:
    def __init__(
        self,
        snapshots: Mapping[str, EvidenceSnapshot],
        read_chunk: Callable[[str, str | None], str],
    ) -> None:
        self._snapshots = dict(snapshots)
        self._read_chunk = read_chunk

    def resolve(self, label: str) -> EvidenceSnapshot:
        snapshot = self._snapshots.get(label)
        if snapshot is None:
            raise EvidenceIntegrityError(f"unknown evidence label: {label}")
        if _hash_quote(snapshot.quote) != snapshot.quote_sha256:
            raise EvidenceIntegrityError(f"quote hash mismatch: {label}")
        source = self._read_chunk(snapshot.version_id, snapshot.chunk_id)
        if snapshot.quote not in source:
            raise EvidenceIntegrityError(f"quote is not readable in source version: {label}")
        return snapshot
