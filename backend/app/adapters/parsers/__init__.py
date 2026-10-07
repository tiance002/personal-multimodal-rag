from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

import fitz
from PIL import Image

from backend.app.domain.models import DocumentAsset, DocumentBlock, DocumentSection, NormalizedDocument, SourceLocator
from backend.app.domain.table_evidence import table_row_texts
from backend.app.adapters.parsers.pdf_tables import page_tables
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
    def __init__(self, tessdata: Path | None = None, *, collect_caption_geometry: bool = False) -> None:
        self.tessdata = Path(tessdata or os.getenv("RAG_TESSDATA_DIR", "var/tessdata"))
        self.collect_caption_geometry = collect_caption_geometry

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
        tables = []
        blocks = []
        warnings = []
        cursor = 0

        def append(text, page_number, *, table=None, asset_id=None):
            nonlocal cursor
            if table is not None:
                text = "".join(content for _, content in table_row_texts(table))
                table = table.model_copy(update={"start":cursor,"end":cursor+len(text)})
                tables.append(table)
            if not text:
                return
            start = cursor
            pages.append(text)
            cursor += len(text)
            block_id = f"pdf-block-{len(blocks)}"
            blocks.append(DocumentBlock(block_id=block_id, kind="table" if table else "text",
                table_id=table.table_id if table else None, start=start, end=cursor))
            sections.append(DocumentSection(section_id=block_id,heading=f"Page {page_number}",level=1,
                start=start,end=cursor,page_start=page_number,page_end=page_number,
                content_type="table" if table else "image_ocr" if asset_id else "text",asset_id=asset_id))

        try:
            pdf = fitz.open(stream=raw,filetype="pdf")
        except Exception as exc:
            raise ParserError("PDF_OPEN_FAILED") from exc
        with pdf:
            if pdf.needs_pass:
                raise ParserError("PDF_PASSWORD_REQUIRED")
            for page_number, page in enumerate(pdf, start=1):
                raw_text = page.get_text("text")
                pieces, table_evidence, table_warnings = page_tables(page,page_number,raw_text)
                warnings.extend(table_warnings)
                text = raw_text
                source_id: str | None = None
                embedded_images: list[tuple[str, bytes, str]] = []
                if not raw_text.strip():
                    raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
                    source_id = str(uuid.uuid4())
                    assets.append(DocumentAsset(
                        asset_id=source_id, asset_type="scanned_page", page_no=page_number,
                        source_bytes=raster,
                        source_locator={"page": page_number, "sha256": hashlib.sha256(raster).hexdigest(),
                                        "bbox": tuple(page.rect), "table_structure_status":"unsupported",
                                        **({"coordinate_basis":"pdf-points-top-left-unrotated"}
                                           if self.collect_caption_geometry else {})},
                    ))
                    text, error = self._ocr(page)
                    pieces = [(text,None)]
                    if error:
                        warnings.append(f"{error}:page={page_number}")
                    assets.append(DocumentAsset(
                        asset_id=str(uuid.uuid4()), asset_type="ocr_text", page_no=page_number,
                        text_content=text or None, derived_from_asset_id=source_id,
                        source_locator={"page": page_number, "source_asset_id": source_id,
                                        "table_structure_status":"unsupported"},
                        status="failed" if error else "ready", error_code=error,
                    ))
                else:
                    for image in page.get_images(full=True):
                        xref = image[0]
                        extracted = pdf.extract_image(xref)
                        image_bytes = extracted["image"]
                        regions = page.get_image_rects(xref) if self.collect_caption_geometry else [None]
                        for region in regions or [None]:
                            image_id = str(uuid.uuid4())
                            geometry = ({"bbox": tuple(region) if region is not None else None,
                                "coordinate_basis": "pdf-points-top-left-unrotated" if region is not None else "UNKNOWN"}
                                if self.collect_caption_geometry else {})
                            assets.append(DocumentAsset(
                                asset_id=image_id, asset_type="source_image", page_no=page_number,
                                source_bytes=image_bytes,
                                source_locator={"page": page_number, "xref": xref,
                                    "sha256": hashlib.sha256(image_bytes).hexdigest(),
                                    "table_structure_status":"unsupported", "semantic_status":"NOT_RUN", **geometry},
                            ))
                            warnings.append(f"PDF_IMAGE_TABLE_SEMANTICS_UNSUPPORTED:page={page_number}")
                            embedded_images.append((image_id, image_bytes, extracted["ext"]))
                start = cursor
                for piece, table in pieces:
                    append(piece,page_number,table=table,asset_id=source_id)
                locators.append(SourceLocator(kind="pdf",page=page_number,start=start,end=cursor,
                    quote="".join(pages)[start:cursor],raw_text=raw_text,raw_evidence=table_evidence,
                    asset_id=source_id))
                for image_id, image_bytes, extension in embedded_images:
                    try:
                        with fitz.open(stream=image_bytes, filetype=extension) as image_document:
                            image_text, error = self._ocr(image_document[0])
                    except Exception:
                        image_text, error = "", "OCR_UNAVAILABLE"
                    if error:
                        warnings.append(f"{error}:page={page_number}:embedded-image")
                    assets.append(DocumentAsset(
                        asset_id=str(uuid.uuid4()), asset_type="ocr_text", page_no=page_number,
                        text_content=image_text or None, derived_from_asset_id=image_id,
                        source_locator={"page": page_number, "source_asset_id": image_id},
                        status="failed" if error else "ready", error_code=error,
                    ))
                    if image_text:
                        image_start = cursor
                        append("\n"+image_text,page_number,asset_id=image_id)
                        locators.append(SourceLocator(kind="pdf",page=page_number,start=image_start,end=cursor,
                            quote="\n"+image_text,asset_id=image_id))
        content = "".join(pages)
        status = "partial" if warnings else "complete"
        locators = [l.model_copy(update={"parse_status":status,"parse_warnings":tuple(warnings)}) for l in locators]
        return NormalizedDocument(
            document_id=document_id,version_id=version_id,title=path.stem,media_type="application/pdf",
            markdown_content=content,sections=sections,tables=tables,blocks=blocks,assets=assets,
            source_locators=locators,content_sha256=_sha256(content),
            parser_version=f"pdf/v3-native-table-pymupdf-{fitz.VersionBind}",
            parse_status=status,parse_warnings=tuple(warnings),
        )


class ImageParser:
    def __init__(self, tessdata: Path | None = None, *, collect_caption_geometry: bool = False) -> None:
        self.pdf_parser = PdfParser(tessdata=tessdata)
        self.collect_caption_geometry = collect_caption_geometry

    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument:
        try:
            with Image.open(path) as image:
                image_size = image.size
                image.verify()
        except Exception as exc:
            raise ParserError("IMAGE_SIGNATURE_MISMATCH") from exc
        raw = path.read_bytes()
        source_id = str(uuid.uuid4())
        asset = DocumentAsset(
            asset_id=source_id, asset_type="source_image",
            source_locator={"sha256": hashlib.sha256(raw).hexdigest(),
                **({"bbox": (0, 0, *image_size), "coordinate_basis": "image-pixels-top-left"}
                   if self.collect_caption_geometry else {})},
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


def _text_document(
    path: Path,
    document_id: str,
    version_id: str,
    text: str,
    media_type: str,
    parser_version: str,
    empty_error: str,
    *,
    markdown_headings: bool = False,
) -> NormalizedDocument:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ParserError(empty_error)
    sections, locators = _sectioned_text(normalized, "text/markdown" if markdown_headings else "text/plain")
    return NormalizedDocument(
        document_id=document_id,
        version_id=version_id,
        title=path.stem,
        media_type=media_type,
        markdown_content=normalized,
        sections=sections,
        source_locators=locators,
        content_sha256=_sha256(normalized),
        parser_version=parser_version,
    )


# Mature HTML/DOCX parsers run only in an explicitly selected local runtime.
from backend.app.adapters.parsers.native import HtmlParser, DocxParser
from backend.app.adapters.parsers.doc import DocParser


# XLSX uses the mature, bounded inert workbook adapter.
from backend.app.adapters.parsers.xlsx import XlsxParser


def _canonical_media_type(path: Path, media_type: str) -> str:
    declared = (media_type or "").split(";", 1)[0].strip().lower()
    by_suffix = {
        ".csv": "text/csv",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if declared in {"", "application/octet-stream", "binary/octet-stream"}:
        return by_suffix.get(path.suffix.lower(), declared or "application/octet-stream")
    return declared


class ParserRegistry:
    def __init__(self, tessdata: Path | None = None, *, native_python: Path | None = None,
                 doc_converter=None, collect_caption_geometry: bool = False) -> None:
        self.tessdata = tessdata
        self.native_python = native_python
        self.doc_converter = doc_converter
        self.collect_caption_geometry = collect_caption_geometry

    def parse(self, path: Path, media_type: str, document_id: str, version_id: str) -> NormalizedDocument:
        media_type = _canonical_media_type(path, media_type)
        if media_type in {"text/plain", "text/markdown"}:
            return TextParser(media_type).parse(path, document_id, version_id)
        if media_type == "application/msword":
            return DocParser(python=self.native_python, converter=self.doc_converter).parse(path, document_id, version_id)
        if media_type == "text/csv":
            return TextParser("text/plain").parse(path, document_id, version_id)
        if media_type in {"text/html", "application/xhtml+xml"}:
            return HtmlParser(python=self.native_python).parse(path, document_id, version_id)
        if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return DocxParser(python=self.native_python).parse(path, document_id, version_id)
        if media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return XlsxParser().parse(path, document_id, version_id)
        if media_type == "application/pdf":
            return PdfParser(tessdata=self.tessdata, collect_caption_geometry=self.collect_caption_geometry).parse(path, document_id, version_id)
        if media_type.startswith("image/"):
            return ImageParser(tessdata=self.tessdata, collect_caption_geometry=self.collect_caption_geometry).parse(path, document_id, version_id)
        raise ParserError("UNSUPPORTED_MEDIA_TYPE")


__all__ = ["OCRUnavailable", "ParserError", "ParserRegistry"]
