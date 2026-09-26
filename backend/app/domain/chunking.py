from __future__ import annotations

import hashlib
from dataclasses import dataclass
from statistics import median

from backend.app.domain.models import ChunkDraft, DocumentSection, NormalizedDocument, SourceLocator


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


CHUNKER_VERSION = "adaptive/v1"


@dataclass(frozen=True)
class ChunkingDecision:
    strategy: str
    chunker_version: str = CHUNKER_VERSION
    reason: str = ""


def profile_document(document: NormalizedDocument, max_chars: int = 1200) -> ChunkingDecision:
    """Choose a deterministic layout strategy from parsed document structure."""
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

    decision = profile_document(document, max_chars)
    structured = decision.strategy in {"page_recursive", "heading_recursive"} or (
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
                    ),
                )
            )
            if end == section_end:
                break
            cursor = max(cursor + 1, end - overlap)
    return chunks
