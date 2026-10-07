from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from statistics import median

from backend.app.domain.models import ChunkDraft, DocumentSection, NormalizedDocument, SourceLocator


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


from backend.app.domain.table_evidence import table_row_texts, table_row_cells, table_row_range


CHUNKER_VERSION = "adaptive/v1"
CAPTION_EVIDENCE_PREFIX = "【模型生成的图像描述，非原文；请对照原图】\n"


def _caption_chunk(document: NormalizedDocument, section: DocumentSection,
                   index: int, max_chars: int) -> ChunkDraft:
    """Admit an explicitly supplied derivative, never generate caption content."""
    assets = {asset.asset_id: asset for asset in document.assets}
    if len(assets) != len(document.assets):
        raise ValueError("CAPTION_ASSET_ID_CONFLICT")
    caption = assets.get(section.asset_id)
    if (caption is None or caption.asset_type != "caption" or caption.status != "ready"
            or caption.error_code is not None or not caption.text_content):
        raise ValueError("CAPTION_DERIVATIVE_REQUIRED")
    source = assets.get(caption.derived_from_asset_id)
    if (source is None or source.asset_type not in {"source_image", "scanned_page"}
            or source.status != "ready"):
        raise ValueError("CAPTION_SOURCE_REQUIRED")
    evidence = caption.source_locator
    digest = evidence.get("source_image_sha256")
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest != source.source_locator.get("sha256")
            or source.source_bytes is not None and hashlib.sha256(source.source_bytes).hexdigest() != digest):
        raise ValueError("CAPTION_SOURCE_HASH_MISMATCH")
    if (evidence.get("schema_version") != "local-caption/v1"
            or evidence.get("evidence_kind") != "model_generated_caption"
            or evidence.get("validation_status") != "UNVERIFIED"
            or not isinstance(evidence.get("input_image_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence["input_image_sha256"])
            or any(not isinstance(evidence.get(key), str) or not evidence[key]
                   for key in ("model_requested", "model_reported", "model_digest", "prompt_version"))):
        raise ValueError("CAPTION_PROVENANCE_REQUIRED")
    content = document.markdown_content[section.start:section.end]
    if (not 0 <= section.start < section.end <= len(document.markdown_content)
            or content != CAPTION_EVIDENCE_PREFIX + caption.text_content):
        raise ValueError("CAPTION_SOURCE_SPAN_MISMATCH")
    if len(content) > max_chars:
        raise ValueError("CAPTION_SECTION_TOO_LARGE")
    matches = [locator for locator in document.source_locators
               if (locator.start, locator.end, locator.asset_id) == (section.start, section.end, caption.asset_id)]
    if len(matches) != 1:
        raise ValueError("CAPTION_LOCATOR_REQUIRED")
    locator = matches[0]
    expected_kind = "pdf" if document.media_type == "application/pdf" else "image" if document.media_type.startswith("image/") else None
    if (expected_kind is None or locator.kind != expected_kind or locator.cells
            or locator.table_id is not None or locator.cell_range is not None
            or locator.table_bbox is not None or locator.header_rows or locator.merged_ranges):
        raise ValueError("CAPTION_NOT_TABLE_EVIDENCE")
    if (locator.quote != content or locator.raw_evidence != evidence
            or locator.page != caption.page_no or caption.page_no != source.page_no
            or evidence.get("page") != source.page_no
            or section.page_start != source.page_no or section.page_end != source.page_no):
        raise ValueError("CAPTION_LOCATOR_MISMATCH")
    bbox = evidence.get("bbox")
    source_bbox = source.source_locator.get("bbox")
    source_basis = source.source_locator.get("coordinate_basis")
    if bbox is None:
        if (source_bbox is not None or locator.bbox is not None
                or evidence.get("coordinate_basis") != "UNKNOWN" or source_basis != "UNKNOWN"):
            raise ValueError("CAPTION_BBOX_MISMATCH")
    else:
        basis = "pdf-points-top-left-unrotated" if expected_kind == "pdf" else "image-pixels-top-left"
        if (not isinstance(bbox, (tuple, list)) or len(bbox) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in bbox)
                or not bbox[0] < bbox[2] or not bbox[1] < bbox[3]
                or not isinstance(source_bbox, (tuple, list)) or len(source_bbox) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in source_bbox)
                or not source_bbox[0] < source_bbox[2] or not source_bbox[1] < source_bbox[3]
                or tuple(source_bbox) != tuple(bbox) or source_basis != basis
                or locator.bbox != tuple(bbox) or evidence.get("coordinate_basis") != basis):
            raise ValueError("CAPTION_BBOX_MISMATCH")
    return ChunkDraft(chunk_index=index, content=content, start=section.start, end=section.end,
        heading_path=section.heading_path, chunk_type="image_caption", content_sha256=_sha256(content),
        source_locator=locator.model_copy(update={"conversion_lineage": document.conversion_lineage,
            "parse_status": document.parse_status, "parse_warnings": document.parse_warnings}))


@dataclass(frozen=True)
class ChunkingDecision:
    strategy: str
    chunker_version: str = CHUNKER_VERSION
    reason: str = ""


def profile_document(document: NormalizedDocument, max_chars: int = 1200) -> ChunkingDecision:
    """Choose a deterministic layout strategy from parsed document structure."""
    if document.tables:
        return ChunkingDecision("table_rows", reason="structured table cells and header context available")
    if document.media_type == "application/pdf" and any(section.page_start for section in document.sections):
        return ChunkingDecision("page_recursive", reason="page boundaries available")
    if document.media_type == "text/markdown" and any(section.heading for section in document.sections):
        lengths = [section.end - section.start for section in document.sections if section.heading]
        if len(lengths) >= 4 and median(lengths) < max(1, max_chars // 4):
            return ChunkingDecision("merged_sections", reason="many short heading sections")
        return ChunkingDecision("heading_recursive", reason="heading structure available")
    if "\n\n" in document.markdown_content or "\n" in document.markdown_content:
        return ChunkingDecision("paragraph_recursive", reason="paragraph boundaries available")
    return ChunkingDecision("fixed", reason="no reliable structure")


def _recursive_end(content: str, cursor: int, hard_end: int, max_chars: int) -> int:
    upper = min(cursor + max_chars, hard_end)
    if upper == hard_end:
        return upper
    lower = cursor + max(1, max_chars // 2)
    window = content[lower:upper]
    for separator in ("\n\n", "\n", "。", ". "):
        offset = window.rfind(separator)
        if offset >= 0:
            return lower + offset + len(separator)
    return upper


def chunk_document(
    document: NormalizedDocument,
    max_chars: int = 1200,
    overlap: int = 120,
) -> list[ChunkDraft]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be between 0 and max_chars")

    # Caption admission cannot depend on whether a later block happens to fit
    # one section: merged/overlapping blocks must never erase its evidence type.
    for index, section in enumerate(document.sections):
        if section.content_type != "image_caption":
            continue
        _caption_chunk(document, section, 0, max_chars)
        if any(index != other_index and section.start < other.end and other.start < section.end
               for other_index, other in enumerate(document.sections)):
            raise ValueError("CAPTION_SECTION_OVERLAP")
        if document.blocks:
            intersecting = [block for block in document.blocks
                            if section.start < block.end and block.start < section.end]
            if (len(intersecting) != 1 or intersecting[0].kind != "text"
                    or (intersecting[0].start, intersecting[0].end) != (section.start, section.end)):
                raise ValueError("CAPTION_BLOCK_SPAN_MISMATCH")

    if document.tables:
        # Native mixed documents require a contiguous ordered block graph.
        # Existing XLSX admission retains its table-only coverage check.
        native_mixed = bool(all(t.source_format for t in document.tables))
        if native_mixed:
            covered = 0
            table_ids = []
            for block in document.blocks:
                if block.start != covered or block.end <= block.start:
                    raise ValueError("TABLE_BLOCK_COVERAGE_REQUIRED")
                covered = block.end
                if block.kind == "table":
                    table_ids.append(block.table_id)
                    table = next((t for t in document.tables if t.table_id == block.table_id), None)
                    if table is None or (table.start, table.end) != (block.start, block.end):
                        raise ValueError("TABLE_BLOCK_COVERAGE_REQUIRED")
            if covered != len(document.markdown_content) or table_ids != [t.table_id for t in document.tables]:
                raise ValueError("TABLE_BLOCK_COVERAGE_REQUIRED")
        covered = 0
        for table in ([] if native_mixed else document.tables):
            if table.start != covered or table.end < table.start:
                raise ValueError("TABLE_BLOCK_COVERAGE_REQUIRED")
            covered = table.end
        if not native_mixed and covered != len(document.markdown_content):
            raise ValueError("TABLE_BLOCK_COVERAGE_REQUIRED")
        chunks: list[ChunkDraft] = []
        for table in document.tables:
            projected_rows = dict(table_row_cells(table))
            cursor = table.start
            for row, content in table_row_texts(table):
                if len(content) > max_chars:
                    raise ValueError("TABLE_ROW_TOO_LARGE")
                end = cursor + len(content)
                if document.markdown_content[cursor:end] != content:
                    raise ValueError("TABLE_SOURCE_SPAN_MISMATCH")
                row_cells = projected_rows[row]
                origin_coordinates = {c.coordinate for c in row_cells}
                provenance = tuple(c for c in table.cells
                    if c.coordinate in origin_coordinates or c.row in table.header_rows)
                chunks.append(ChunkDraft(chunk_index=len(chunks), content=content, start=cursor, end=end,
                    heading_path=(table.sheet or table.table_id,), chunk_type="table", content_sha256=_sha256(content),
                    source_locator=SourceLocator(kind="table", start=cursor, end=end, quote=content,
                        page=table.page, table_bbox=table.bbox,
                        bbox=(min(c.bbox[0] for c in row_cells), min(c.bbox[1] for c in row_cells),
                              max(c.bbox[2] for c in row_cells), max(c.bbox[3] for c in row_cells))
                             if all(c.bbox for c in row_cells) else None,
                        sheet=table.sheet, table_id=table.table_id,
                        cell_range=table_row_range(table, row, row_cells),
                        header_rows=table.header_rows, merged_ranges=table.merged_ranges, cells=provenance,
                        source_format=table.source_format, header_detection=table.header_detection,
                        conversion_lineage=document.conversion_lineage,
                        parse_status=document.parse_status, parse_warnings=document.parse_warnings)))
                cursor = end
        if native_mixed:
            for block in document.blocks:
                if block.kind != "text": continue
                section = next((s for s in document.sections if s.start <= block.start and s.end >= block.end), None)
                if section and section.content_type == "image_caption":
                    if (block.start, block.end) != (section.start, section.end):
                        raise ValueError("CAPTION_BLOCK_SPAN_MISMATCH")
                    chunks.append(_caption_chunk(document, section, len(chunks), max_chars))
                    continue
                cursor = block.start
                while cursor < block.end:
                    end = _recursive_end(document.markdown_content, cursor, block.end, max_chars)
                    content = document.markdown_content[cursor:end]
                    chunks.append(ChunkDraft(chunk_index=len(chunks), content=content, start=cursor, end=end,
                        chunk_type="image_ocr" if section and section.content_type == "image_ocr" else "text",
                        content_sha256=_sha256(content), source_locator=SourceLocator(
                            kind="pdf" if document.media_type == "application/pdf" else "text", start=cursor,
                            page=section.page_start if section else None, asset_id=section.asset_id if section else None,
                            end=end, quote=content, source_format=document.tables[0].source_format,
                            conversion_lineage=document.conversion_lineage,
                            parse_status=document.parse_status, parse_warnings=document.parse_warnings)))
                    if end == block.end: break
                    cursor = max(cursor + 1, end - overlap)
            chunks = [c.model_copy(update={"chunk_index":i}) for i,c in enumerate(sorted(chunks, key=lambda c:c.start))]
        return chunks

    decision = profile_document(document, max_chars)
    structured = any(s.content_type == "image_caption" for s in document.sections) or decision.strategy in {"page_recursive", "heading_recursive"} or (
        len(document.sections) == 1 and decision.strategy != "merged_sections"
    )
    sections = (document.sections if structured else []) or [
        DocumentSection(
            section_id="section-0",
            heading="",
            level=0,
            start=0,
            end=len(document.markdown_content),
        )
    ]
    chunks: list[ChunkDraft] = []
    for section in sections:
        if section.content_type == "image_caption":
            chunks.append(_caption_chunk(document, section, len(chunks), max_chars))
            continue
        section_start = max(0, min(section.start, len(document.markdown_content)))
        section_end = max(section_start, min(section.end, len(document.markdown_content)))
        cursor = section_start
        while cursor < section_end:
            end = (
                min(cursor + max_chars, section_end)
                if decision.strategy == "fixed"
                else _recursive_end(document.markdown_content, cursor, section_end, max_chars)
            )
            content = document.markdown_content[cursor:end]
            chunks.append(
                ChunkDraft(
                    chunk_index=len(chunks),
                    content=content,
                    start=cursor,
                    end=end,
                    heading_path=tuple(section.heading_path),
                    content_sha256=_sha256(content),
                    chunk_type=section.content_type,
                    source_locator=SourceLocator(
                        kind=("pdf" if document.media_type == "application/pdf" else "image" if document.media_type.startswith("image/") else "markdown" if document.media_type == "text/markdown" else "text"),
                        page=section.page_start if document.media_type == "application/pdf" else None,
                        start=cursor,
                        end=end,
                        quote=content,
                        asset_id=section.asset_id,
                        conversion_lineage=document.conversion_lineage,
                        parse_status=document.parse_status, parse_warnings=document.parse_warnings,
                    ),
                )
            )
            if end == section_end:
                break
            cursor = max(cursor + 1, end - overlap)
    return chunks
