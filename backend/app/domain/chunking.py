from __future__ import annotations

import hashlib

from backend.app.domain.models import ChunkDraft, NormalizedDocument, SourceLocator


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_document(
    document: NormalizedDocument,
    max_chars: int = 1200,
    overlap: int = 120,
) -> list[ChunkDraft]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be between 0 and max_chars")

    sections = document.sections or [
        type("DefaultSection", (), {
            "start": 0,
            "end": len(document.markdown_content),
            "heading_path": (),
        })()
    ]
    chunks: list[ChunkDraft] = []
    for section in sections:
        section_start = max(0, min(section.start, len(document.markdown_content)))
        section_end = max(section_start, min(section.end, len(document.markdown_content)))
        cursor = section_start
        while cursor < section_end:
            end = min(cursor + max_chars, section_end)
            content = document.markdown_content[cursor:end]
            chunks.append(
                ChunkDraft(
                    chunk_index=len(chunks),
                    content=content,
                    start=cursor,
                    end=end,
                    heading_path=tuple(section.heading_path),
                    content_sha256=_sha256(content),
                    source_locator=SourceLocator(
                        kind="markdown" if document.media_type == "text/markdown" else "text",
                        start=cursor,
                        end=end,
                        quote=content,
                    ),
                )
            )
            if end == section_end:
                break
            cursor = max(cursor + 1, end - overlap)
    return chunks
