from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from backend.app.domain.models import NormalizedDocument, StoredObject


@runtime_checkable
class BlobStore(Protocol):
    """Content-addressed blob store used by ingestion.

    Implemented by `adapters.storage.ContentAddressedStorage`; the application
    layer depends only on this contract so it never imports a concrete adapter.
    """

    def put_stream(
        self,
        stream: BinaryIO,
        chunk_size: int = 1024 * 1024,
        max_bytes: int | None = None,
    ) -> StoredObject: ...

    def path_for(self, storage_key: str) -> Path: ...


@runtime_checkable
class DocumentParser(Protocol):
    """Turns a stored file into a normalized document."""

    def parse(self, path: Path, media_type: str, document_id: str, version_id: str) -> NormalizedDocument: ...
