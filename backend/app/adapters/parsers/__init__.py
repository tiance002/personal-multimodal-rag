from __future__ import annotations

import hashlib
import re
from pathlib import Path

import fitz
from PIL import Image

from backend.app.domain.models import DocumentAsset, DocumentSection, NormalizedDocument, SourceLocator
from backend.app.domain.parsers import OCRUnavailable, ParserError


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sectioned_text(text: str, media_type: str) -> tuple[list[DocumentSection], list[SourceLocator]]:
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$") if media_type == "text/markdown" else None
    lines = text.splitlines(keepends=True)
    sections: list[DocumentSection] = []
    locators: list[SourceLocator] = []
    headings: list[tuple[int, int, str, int]] = []
    offset = 0
    for line in lines:
        content = line.rstrip("\r\n")
        if heading_pattern:
            match = heading_pattern.match(content)
            if match:
                headings.append((offset, len(match.group(1)), match.group(2).strip(), len(line)))
        offset += len(line)
    if not headings:
        sections.append(DocumentSection(section_id="section-0", heading="", start=0, end=len(text), level=0))
    else:
        if headings[0][0] > 0:
            sections.append(DocumentSection(section_id="section-0", heading="", start=0, end=headings[0][0], level=0))
        for index, (start, level, heading, line_length) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
            parent_path = [entry[2] for entry in headings[: index + 1] if entry[1] < level]
            heading_path = tuple(parent_path + [heading])
            sections.append(
                DocumentSection(
                    section_id=f"section-{len(sections)}",
                    heading=heading,
                    heading_path=heading_path,
                    level=level,
                    start=start,
                    end=end,
                )
            )
    for section in sections:
        quote = text[section.start:section.end]
        kind = "markdown" if media_type == "text/markdown" else "text"
        locators.append(SourceLocator(kind=kind, start=section.start, end=section.end, quote=quote))
    return sections, locators


class TextParser:
    media_types = {"text/plain", "text/markdown"}

    def __init__(self, media_type: str) -> None:
        self.media_type = media_type

    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument:
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ParserError("TEXT_BINARY_SIGNATURE_MISMATCH")
        try:
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as exc:
            raise ParserError("TEXT_UTF8_DECODE_FAILED") from exc
        sections, locators = _sectioned_text(text, self.media_type)
        return NormalizedDocument(
            document_id=document_id,
            version_id=version_id,
            title=path.stem,
            media_type=self.media_type,
            markdown_content=text,
            sections=sections,
            source_locators=locators,
            content_sha256=_sha256(text),
            parser_version="text/v1",
        )


class PdfParser:
    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument:
        raw = path.read_bytes()
        if not raw.startswith(b"%PDF"):
            raise ParserError("PDF_MAGIC_MISMATCH")
        pages: list[str] = []
        locators: list[SourceLocator] = []
        sections: list[DocumentSection] = []
        cursor = 0
        with fitz.open(path) as pdf:
            for page_number, page in enumerate(pdf, start=1):
                text = page.get_text("text").replace("\r\n", "\n").replace("\r", "\n")
                if pages:
                    pages.append("\n")
                    cursor += 1
                start = cursor
                pages.append(text)
                cursor += len(text)
                end = cursor
                sections.append(
                    DocumentSection(
                        section_id=f"page-{page_number}",
                        heading=f"Page {page_number}",
                        level=1,
                        start=start,
                        end=end,
                        page_start=page_number,
                        page_end=page_number,
                    )
                )
                locators.append(SourceLocator(kind="pdf", page=page_number, start=start, end=end, quote=text))
        content = "".join(pages)
        return NormalizedDocument(
            document_id=document_id,
            version_id=version_id,
            title=path.stem,
            media_type="application/pdf",
            markdown_content=content,
            sections=sections,
            source_locators=locators,
            content_sha256=_sha256(content),
            parser_version="pdf/v1",
        )


class ImageParser:
    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise ParserError("IMAGE_SIGNATURE_MISMATCH") from exc
        asset = DocumentAsset(asset_id=f"{version_id}:source-image", asset_type="source_image")
        return NormalizedDocument(
            document_id=document_id,
            version_id=version_id,
            title=path.stem,
            media_type="image/*",
            markdown_content="",
            assets=[asset],
            source_locators=[SourceLocator(kind="image")],
            content_sha256=_sha256(path.read_bytes().hex()),
            parser_version="image/v1-no-ocr",
        )


class ParserRegistry:
    def parse(self, path: Path, media_type: str, document_id: str, version_id: str) -> NormalizedDocument:
        if media_type in {"text/plain", "text/markdown"}:
            return TextParser(media_type).parse(path, document_id, version_id)
        if media_type == "application/pdf":
            return PdfParser().parse(path, document_id, version_id)
        if media_type.startswith("image/"):
            return ImageParser().parse(path, document_id, version_id)
        raise ParserError("UNSUPPORTED_MEDIA_TYPE")


__all__ = ["OCRUnavailable", "ParserError", "ParserRegistry"]
