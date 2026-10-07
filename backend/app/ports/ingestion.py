from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable
import shutil
from tempfile import TemporaryDirectory

from backend.app.domain.models import NormalizedDocument, StoredObject
from backend.app.domain.parsers import ParserError
from backend.app.domain.version_source import VersionSource


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


def parse_version_source(parsers: DocumentParser, storage: BlobStore, source: VersionSource) -> NormalizedDocument:
    """Bind parser input to immutable source metadata, including its filename.

    A private named copy preserves unchanged parser filename semantics without
    exposing the CAS original to writes. Unknown legacy metadata fails closed.
    """
    if not source.metadata_known:
        raise ParserError("LEGACY_VERSION_METADATA_UNKNOWN")
    name = source.file_name
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ParserError("INVALID_SOURCE_FILE_NAME")
    with TemporaryDirectory(prefix="rag-version-") as directory:
        path = Path(directory) / name
        shutil.copyfile(storage.path_for(source.storage_key), path)
        return parsers.parse(path, source.media_type, source.document_id, source.version_id)
