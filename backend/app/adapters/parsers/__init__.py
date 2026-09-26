from __future__ import annotations

import hashlib
import os
import re
import uuid
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
        active_headings: list[tuple[int, str]] = []
        for index, (start, level, heading, line_length) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
            while active_headings and active_headings[-1][0] >= level:
                active_headings.pop()
            active_headings.append((level, heading))
            heading_path = tuple(title for _, title in active_headings)
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
    def __init__(self, tessdata: Path | None = None) -> None:
        self.tessdata = Path(tessdata or os.getenv("RAG_TESSDATA_DIR", "var/tessdata"))

    def _ocr(self, page: fitz.Page) -> tuple[str, str | None]:
        if not all((self.tessdata / f"{name}.traineddata").is_file() for name in ("eng", "chi_sim")):
            return "", "OCR_UNAVAILABLE"
        try:
            text = page.get_text(textpage=page.get_textpage_ocr(
                language="eng+chi_sim", dpi=150, full=True, tessdata=str(self.tessdata.resolve())
            )).strip()
        except Exception:
            return "", "OCR_UNAVAILABLE"
        return text, None if text else "OCR_EMPTY"

    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument:
        raw = path.read_bytes()
        if not raw.startswith(b"%PDF"):
            raise ParserError("PDF_MAGIC_MISMATCH")
        pages: list[str] = []
        locators: list[SourceLocator] = []
        sections: list[DocumentSection] = []
        assets: list[DocumentAsset] = []
        cursor = 0
        with fitz.open(path) as pdf:
            for page_number, page in enumerate(pdf, start=1):
                text = page.get_text("text").replace("\r\n", "\n").replace("\r", "\n")
                source_id: str | None = None
                embedded_images: list[tuple[str, bytes, str]] = []
                if not text.strip():
                    raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
                    source_id = str(uuid.uuid4())
                    assets.append(DocumentAsset(
                        asset_id=source_id, asset_type="scanned_page", page_no=page_number,
                        source_bytes=raster,
                        source_locator={"page": page_number, "sha256": hashlib.sha256(raster).hexdigest(),
                                        "bbox": tuple(page.rect)},
                    ))
                    text, error = self._ocr(page)
                    assets.append(DocumentAsset(
                        asset_id=str(uuid.uuid4()), asset_type="ocr_text", page_no=page_number,
                        text_content=text or None, derived_from_asset_id=source_id,
                        source_locator={"page": page_number, "source_asset_id": source_id},
                        status="failed" if error else "ready", error_code=error,
                    ))
                else:
                    for image in page.get_images(full=True):
                        xref = image[0]
                        extracted = pdf.extract_image(xref)
                        image_bytes = extracted["image"]
                        image_id = str(uuid.uuid4())
                        assets.append(DocumentAsset(
                            asset_id=image_id, asset_type="source_image", page_no=page_number,
                            source_bytes=image_bytes,
                            source_locator={"page": page_number, "xref": xref,
                                            "sha256": hashlib.sha256(image_bytes).hexdigest()},
                        ))
                        embedded_images.append((image_id, image_bytes, extracted["ext"]))
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
                        content_type="image_ocr" if source_id else "text",
                        asset_id=source_id,
                    )
                )
                locators.append(SourceLocator(kind="pdf", page=page_number, start=start, end=end, quote=text, asset_id=source_id))
                for image_id, image_bytes, extension in embedded_images:
                    try:
                        with fitz.open(stream=image_bytes, filetype=extension) as image_document:
                            image_text, error = self._ocr(image_document[0])
                    except Exception:
                        image_text, error = "", "OCR_UNAVAILABLE"
                    assets.append(DocumentAsset(
                        asset_id=str(uuid.uuid4()), asset_type="ocr_text", page_no=page_number,
                        text_content=image_text or None, derived_from_asset_id=image_id,
                        source_locator={"page": page_number, "source_asset_id": image_id},
                        status="failed" if error else "ready", error_code=error,
                    ))
                    if image_text:
                        pages.append("\n")
                        cursor += 1
                        image_start = cursor
                        pages.append(image_text)
                        cursor += len(image_text)
                        sections.append(DocumentSection(
                            section_id=f"page-{page_number}-image-{image_id}",
                            heading=f"Page {page_number} image", level=1,
                            start=image_start, end=cursor, page_start=page_number,
                            page_end=page_number, content_type="image_ocr", asset_id=image_id,
                        ))
                        locators.append(SourceLocator(
                            kind="pdf", page=page_number, start=image_start, end=cursor,
                            quote=image_text, asset_id=image_id,
                        ))
        content = "".join(pages)
        return NormalizedDocument(
            document_id=document_id,
            version_id=version_id,
            title=path.stem,
            media_type="application/pdf",
            markdown_content=content,
            sections=sections,
            assets=assets,
            source_locators=locators,
            content_sha256=_sha256(content),
            parser_version="pdf/v2-ocr",
        )


class ImageParser:
    def __init__(self, tessdata: Path | None = None) -> None:
        self.pdf_parser = PdfParser(tessdata=tessdata)

    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise ParserError("IMAGE_SIGNATURE_MISMATCH") from exc
        raw = path.read_bytes()
        source_id = str(uuid.uuid4())
        asset = DocumentAsset(
            asset_id=source_id, asset_type="source_image",
            source_locator={"sha256": hashlib.sha256(raw).hexdigest()},
        )
        with fitz.open(stream=raw, filetype=path.suffix.lstrip(".")) as image_document:
            content, error = self.pdf_parser._ocr(image_document[0])
        derived = DocumentAsset(
            asset_id=str(uuid.uuid4()), asset_type="ocr_text", text_content=content or None,
            derived_from_asset_id=source_id, status="failed" if error else "ready", error_code=error,
            source_locator={"source_asset_id": source_id},
        )
        sections = [DocumentSection(
            section_id="image-ocr", heading="", level=0, start=0, end=len(content),
            content_type="image_ocr", asset_id=source_id,
        )] if content else []
        return NormalizedDocument(
            document_id=document_id,
            version_id=version_id,
            title=path.stem,
            media_type="image/*",
            markdown_content=content,
            sections=sections,
            assets=[asset, derived],
            source_locators=[SourceLocator(kind="image", start=0, end=len(content), quote=content, asset_id=source_id)],
            content_sha256=_sha256(content),
            parser_version="image/v2-ocr",
        )


class ParserRegistry:
    def __init__(self, tessdata: Path | None = None) -> None:
        self.tessdata = tessdata

    def parse(self, path: Path, media_type: str, document_id: str, version_id: str) -> NormalizedDocument:
        if media_type in {"text/plain", "text/markdown"}:
            return TextParser(media_type).parse(path, document_id, version_id)
        if media_type == "application/pdf":
            return PdfParser(tessdata=self.tessdata).parse(path, document_id, version_id)
        if media_type.startswith("image/"):
            return ImageParser(tessdata=self.tessdata).parse(path, document_id, version_id)
        raise ParserError("UNSUPPORTED_MEDIA_TYPE")


__all__ = ["OCRUnavailable", "ParserError", "ParserRegistry"]
