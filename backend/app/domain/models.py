from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.weknora_port.ingestion.types import (
    DocumentAsset, DocumentBlock, DocumentSection, DocumentTable,
    NormalizedDocument, SourceLocator, TableCell, TableRowProof,
)


class ChunkDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    heading_path: tuple[str, ...] = ()
    chunk_type: Literal["text", "parent_text", "image_ocr", "image_caption", "table"] = "text"
    content_sha256: str
    source_locator: SourceLocator
    context_header: str = ""
    parent_index: int | None = Field(default=None, ge=0)

    @property
    def embedding_content(self) -> str:
        body = self.content.strip()
        return f"{self.context_header}\n\n{body}" if self.context_header else body


class RankedHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    rank: int = Field(ge=1)
    raw_score: float | None = None
    fused_score: float | None = None
    sources: tuple[str, ...] = ()


class EvidenceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    version_id: str
    chunk_id: str | None = None
    quote: str = Field(min_length=1)
    quote_sha256: str
    locator: dict[str, object]


@dataclass(frozen=True)
class ChunkRecord:
    """A stored chunk as read back from a retrieval repository.

    A frozen dataclass rather than a pydantic model so repositories and tests can
    construct it positionally, matching the other read-side value objects.

    `embedding` is only populated by repositories that keep vectors in process
    (the in-memory test double). The PostgreSQL repository resolves vectors in
    SQL and therefore leaves it as None.
    """

    chunk_id: str
    knowledge_base_id: str
    document_id: str
    version_id: str
    content: str
    locator: dict[str, object] = field(default_factory=dict)
    heading_path: tuple[str, ...] = ()
    is_current: bool = True
    embedding: tuple[float, ...] | None = None
    embedding_profile_id: str | None = None
    content_sha256: str | None = None
    parent_id: str | None = None
    chunk_role: str = "child"
    chunk_index: int | None = None
    chunk_type: str = "text"
    index_identity: str | None = None


@dataclass(frozen=True)
class StoredObject:
    """A content-addressed blob as written by the storage port.

    Lives in domain so the storage port and its adapters can agree on the return
    type without the application layer importing a concrete adapter.
    """

    storage_key: str
    sha256: str
    size: int
