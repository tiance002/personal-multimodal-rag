"""Normalized source-document value types shared by parser implementations."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TableCell(BaseModel):
    model_config = ConfigDict(frozen=True)
    coordinate: str
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    value: str | int | float | bool | None = None
    value_type: str
    display: str
    number_format: str = "General"
    raw_number: str | None = None
    formula: str | None = None
    cached_value: str | int | float | bool | None = None
    cache_status: Literal["present", "missing", "not_applicable"] = "not_applicable"
    merged_anchor: str | None = None
    column_headers: tuple[str, ...] = ()
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    column_header: bool | None = None
    row_header: bool | None = None
    header_evidence: dict[str, object] = Field(default_factory=dict)
    native_locator: dict[str, object] = Field(default_factory=dict)
    bbox: tuple[float, float, float, float] | None = None


class DocumentTable(BaseModel):
    model_config = ConfigDict(frozen=True)
    table_id: str
    sheet: str | None = None
    cell_range: str
    header_rows: tuple[int, ...] = ()
    header_detection: str = "unavailable"
    row_representation: Literal["legacy", "key_value"] = "legacy"
    conversion_lineage: dict[str, str] = Field(default_factory=dict)
    source_format: str | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    raw_evidence: dict[str, object] = Field(default_factory=dict)
    caption: str | None = None
    merged_ranges: tuple[str, ...] = ()
    cells: list[TableCell] = Field(default_factory=list)
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class DocumentBlock(BaseModel):
    model_config = ConfigDict(frozen=True)
    block_id: str
    kind: Literal["text", "table"]
    table_id: str | None = None
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SourceLocator(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["text", "markdown", "pdf", "image", "table"]
    page: int | None = Field(default=None, ge=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    bbox: tuple[float, float, float, float] | None = None
    table_bbox: tuple[float, float, float, float] | None = None
    raw_text: str | None = None
    raw_evidence: dict[str, object] = Field(default_factory=dict)
    quote: str | None = None
    asset_id: str | None = None
    sheet: str | None = None
    table_id: str | None = None
    cell_range: str | None = None
    header_rows: tuple[int, ...] = ()
    merged_ranges: tuple[str, ...] = ()
    cells: tuple[TableCell, ...] = ()
    conversion_lineage: dict[str, str] = Field(default_factory=dict)
    source_format: str | None = None
    header_detection: str | None = None
    parse_status: Literal["complete", "partial"] = "complete"
    parse_warnings: tuple[str, ...] = ()


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
    content_type: Literal["text", "image_ocr", "image_caption", "table"] = "text"
    asset_id: str | None = None


class DocumentAsset(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_id: str
    asset_type: Literal["source_image", "scanned_page", "ocr_text", "caption"]
    storage_key: str | None = None
    text_content: str | None = None
    derived_from_asset_id: str | None = None
    page_no: int | None = Field(default=None, ge=1)
    source_locator: dict[str, object] = Field(default_factory=dict)
    source_bytes: bytes | None = Field(default=None, exclude=True)
    status: Literal["ready", "failed"] = "ready"
    error_code: str | None = None


class TableRowProof(BaseModel):
    """Immutable parser proof, independent of where-only source locators.

    Existing citation consumers retain their legacy JSON projection until their
    own migration; this object also survives in the version processing manifest.
    """
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    document_id: str
    version_id: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    table_id: str
    row: int = Field(ge=1)
    cell_range: str
    header_rows: tuple[int, ...] = ()
    header_policy: str
    cells: tuple[TableCell, ...]
    parse_status: Literal["complete", "partial"]
    conversion_lineage: dict[str, str] = Field(default_factory=dict)


class NormalizedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)
    document_id: str
    version_id: str
    title: str
    media_type: str
    markdown_content: str
    sections: list[DocumentSection] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    blocks: list[DocumentBlock] = Field(default_factory=list)
    assets: list[DocumentAsset] = Field(default_factory=list)
    source_locators: list[SourceLocator] = Field(default_factory=list)
    content_sha256: str
    parser_version: str
    parser_engine: str = "legacy"
    source_mapping_available: bool = False
    table_row_proofs: list[TableRowProof] = Field(default_factory=list)
    conversion_lineage: dict[str, str] = Field(default_factory=dict)
    parse_status: Literal["complete", "partial"] = "complete"
    parse_warnings: tuple[str, ...] = ()


__all__ = [
    "DocumentAsset", "DocumentBlock", "DocumentSection", "DocumentTable",
    "NormalizedDocument", "SourceLocator", "TableCell", "TableRowProof",
]
