from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceLocator(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["text", "markdown", "pdf", "image"]
    page: int | None = Field(default=None, ge=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    bbox: tuple[float, float, float, float] | None = None
    quote: str | None = None


class DocumentSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str
    heading: str
    heading_path: tuple[str, ...] = ()
    level: int = Field(ge=0, le=9)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)


class DocumentAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: str
    asset_type: Literal["source_image", "scanned_page", "ocr_text", "caption"]
    storage_key: str | None = None
    text_content: str | None = None
    derived_from_asset_id: str | None = None
    page_no: int | None = Field(default=None, ge=1)
    source_locator: dict[str, object] = Field(default_factory=dict)


class NormalizedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    title: str
    media_type: str
    markdown_content: str
    sections: list[DocumentSection] = Field(default_factory=list)
    assets: list[DocumentAsset] = Field(default_factory=list)
    source_locators: list[SourceLocator] = Field(default_factory=list)
    content_sha256: str
    parser_version: str


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
