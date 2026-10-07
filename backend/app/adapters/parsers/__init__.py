from __future__ import annotations

import hashlib
import os
import re
import sys
import uuid
from pathlib import Path

import fitz
from PIL import Image

from backend.app.domain.models import DocumentAsset, DocumentBlock, DocumentSection, NormalizedDocument, SourceLocator, TableRowProof
from backend.app.domain.table_evidence import table_row_texts, table_row_cells, table_row_range
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
                try:
                    raw_text = page.get_text("text")
                except Exception:
                    warnings.append(f"PARTIAL_PARSE:page={page_number}:text")
                    raw_text = ''
                try:
                    image_area = sum(abs((i['bbox'][2]-i['bbox'][0])*(i['bbox'][3]-i['bbox'][1])) for i in page.get_image_info())
                    ratio = image_area / page.rect.get_area() if page.rect.get_area() > 0 else 0
                except Exception:
                    warnings.append(f"PARTIAL_PARSE:page={page_number}:classification")
                    ratio = 0
                scanned = classify_pdf_page(ratio, len(raw_text.strip())) == 'scanned'
                try:
                    pieces, table_evidence, table_warnings = (
                        ([(raw_text,None)],{'table_status':'unsupported','table_candidates':[]},
                         [f'PDF_TABLE_STRUCTURE_UNSUPPORTED:page={page_number}'])
                        if scanned else page_tables(page,page_number,raw_text))
                except Exception:
                    pieces, table_evidence, table_warnings = [(raw_text,None)],{},[f"PARTIAL_PARSE:page={page_number}:layout"]
                warnings.extend(table_warnings)
                text = raw_text
                source_id: str | None = None
                embedded_images: list[tuple[str, bytes, str]] = []
                if scanned:
                    try:
                        raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
                    except Exception:
                        warnings.append(f"PARTIAL_PARSE:page={page_number}:render")
                        continue
                    source_id = str(uuid.uuid4())
                    assets.append(DocumentAsset(
                        asset_id=source_id, asset_type="scanned_page", page_no=page_number,
                        source_bytes=raster,
                        source_locator={"page": page_number, "sha256": hashlib.sha256(raster).hexdigest(),
                                        "bbox": tuple(page.rect), "table_structure_status":"unsupported",
                                        **({"coordinate_basis":"pdf-points-top-left-unrotated"}
                                           if self.collect_caption_geometry else {})},
                    ))
                    try:
                        text, error = self._ocr(page)
                    except Exception:
                        text, error = '', 'OCR_UNAVAILABLE'
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
                    try:
                        page_images = page.get_images(full=True)
                    except Exception:
                        warnings.append(f"PARTIAL_PARSE:page={page_number}:embedded-images")
                        page_images = []
                    for image in page_images:
                        xref = image[0]
                        try:
                            extracted = pdf.extract_image(xref)
                            image_bytes = extracted['image']
                            extension = extracted['ext']
                            if not isinstance(image_bytes, bytes) or not image_bytes or not isinstance(extension, str):
                                raise ValueError('embedded image unavailable')
                        except Exception:
                            warnings.append(f"PARTIAL_PARSE:page={page_number}:embedded-image")
                            continue
                        try:
                            regions = page.get_image_rects(xref) if self.collect_caption_geometry else [None]
                        except Exception:
                            warnings.append(f"PARTIAL_PARSE:page={page_number}:image-geometry")
                            regions = [None]
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
                            embedded_images.append((image_id, image_bytes, extension))
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
            parser_version=f"pdf/v4-weknora-page-router-pymupdf-{fitz.VersionBind}",
            parser_engine="pymupdf/weknora-page-router-v1",source_mapping_available=True,
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
            source_bytes=raw,
            source_locator={"sha256": hashlib.sha256(raw).hexdigest(),
                **({"bbox": (0, 0, *image_size), "coordinate_basis": "image-pixels-top-left"}
                   if self.collect_caption_geometry else {})},
        )
        try:
            with fitz.open(stream=raw, filetype=path.suffix.lstrip(".")) as image_document:
                content, error = self.pdf_parser._ocr(image_document[0])
        except Exception:
            content, error = '', 'OCR_UNAVAILABLE'
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
            parser_engine="pymupdf/tesseract",source_mapping_available=True,
            parse_status="partial" if error else "complete",
            parse_warnings=(error,) if error else (),
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
from backend.app.adapters.parsers.xls import XlsParser


def classify_pdf_page(image_area_ratio: float, text_len: int) -> str:
    """Fixed WeKnora v0.8.2 _classify_page defaults, in the same order."""
    if image_area_ratio >= 0.5:
        return 'scanned'
    if text_len < 10 and image_area_ratio >= 0.1:
        return 'scanned'
    return 'text'


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
        ".xls": "application/vnd.ms-excel",
    }
    if declared in {"", "application/octet-stream", "binary/octet-stream"}:
        return by_suffix.get(path.suffix.lower(), declared or "application/octet-stream")
    return declared


class ParserRegistry:
    def __init__(self, tessdata: Path | None = None, *, native_python: Path | None = None,
                 doc_converter=None, collect_caption_geometry: bool = False,
                 xlsx_first_row_as_header: bool = False, caption_enricher=None) -> None:
        self.tessdata = tessdata
        self.native_python = Path(native_python or os.getenv('RAG_NATIVE_TABLE_PYTHON') or sys.executable)
        self.doc_converter = doc_converter
        self.collect_caption_geometry = collect_caption_geometry or caption_enricher is not None
        self.caption_enricher = caption_enricher
        self.parsers = {
            'text/plain': TextParser('text/plain'),
            'text/markdown': TextParser('text/markdown'),
            'text/csv': TextParser('text/plain'),
            'application/msword': DocParser(python=self.native_python, converter=doc_converter),
            'text/html': HtmlParser(python=self.native_python),
            'application/xhtml+xml': HtmlParser(python=self.native_python),
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': DocxParser(python=self.native_python),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': XlsxParser(first_row_as_header=xlsx_first_row_as_header),
            'application/vnd.ms-excel': XlsParser(python=self.native_python, first_row_as_header=xlsx_first_row_as_header),
            'application/pdf': PdfParser(tessdata=tessdata, collect_caption_geometry=self.collect_caption_geometry),
            'image/*': ImageParser(tessdata=tessdata, collect_caption_geometry=self.collect_caption_geometry),
        }

    def parse(self, path: Path, media_type: str, document_id: str, version_id: str) -> NormalizedDocument:
        media_type = _canonical_media_type(path, media_type)
        parser = self.parsers.get('image/*' if media_type.startswith('image/') else media_type)
        if parser is None:
            raise ParserError('UNSUPPORTED_MEDIA_TYPE')
        document = parser.parse(path, document_id, version_id)
        proofs = []
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest() if document.tables else None
        for table in document.tables:
            for row, cells in table_row_cells(table):
                origins = {c.coordinate for c in cells}
                proofs.append(TableRowProof(document_id=document_id,version_id=version_id,
                    source_sha256=source_sha256,table_id=table.table_id,
                    row=row,cell_range=table_row_range(table,row,cells),header_rows=table.header_rows,
                    header_policy=table.header_detection,cells=tuple(c for c in table.cells
                        if c.coordinate in origins or c.row in table.header_rows),parse_status=document.parse_status,
                    conversion_lineage=table.conversion_lineage))
        document = document.model_copy(update={'table_row_proofs': proofs,
            'parser_engine': document.parser_engine if document.parser_engine != 'legacy' else type(parser).__name__,
            'source_mapping_available': bool(document.source_locators)})
        if self.caption_enricher is not None:
            document = self.caption_enricher.enrich(document)
        return document


__all__ = ["OCRUnavailable", "ParserError", "ParserRegistry"]
