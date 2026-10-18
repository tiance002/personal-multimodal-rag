"""Offline WeKnora P2 ingestion port.

Parsing produces extracted Markdown and raw image assets only. OCR/VLM and database
index writes are separate injected boundaries; this module does not call providers
or persistence. Upstream source is Tencent/WeKnora at the fixed commit documented
in docs/weknora-port/P2_INGEST_PARITY.md.
"""
from __future__ import annotations

import base64
import io
import mimetypes
import re
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ParsedAsset:
    path: str
    data: bytes
    media_type: str
    source_type: str
    page_number: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseFailure:
    stage: str
    code: str
    exception_type: str
    message: str


@dataclass(frozen=True)
class ParsedDocument:
    content: str
    assets: tuple[ParsedAsset, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    failures: tuple[ParseFailure, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class DocumentRequest:
    filename: str
    data: bytes
    owner_id: str = ""
    knowledge_base_id: str = ""
    document_id: str = ""
    source_version: str = ""
    parser_engine: str = ""
    tenant_parser_overrides: Mapping[str, str] = field(default_factory=dict)
    upload_parser_overrides: Mapping[str, str] = field(default_factory=dict)
    kb_chunking_config: Mapping[str, Any] = field(default_factory=dict)
    process_chunking_override: Mapping[str, Any] = field(default_factory=dict)
    upload_chunking_override: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    content: str
    seq: int
    start: int
    end: int
    context_header: str = ""


@dataclass(frozen=True)
class ChildChunk:
    chunk: Chunk
    parent_index: int


@dataclass(frozen=True)
class ParentChildResult:
    parents: tuple[Chunk, ...]
    children: tuple[ChildChunk, ...]


@dataclass(frozen=True)
class SplitDiagnostics:
    selected_tier: str
    tier_chain: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ChunkConfig:
    chunk_size: int = 512
    chunk_overlap: int = 80
    separators: tuple[str, ...] = ("\n\n", "\n", "。")
    strategy: str = ""
    token_limit: int = 0
    languages: tuple[str, ...] = ()
    enable_parent_child: bool = False
    parent_chunk_size: int = 0
    child_chunk_size: int = 0


@dataclass(frozen=True)
class ChunkRecord:
    """Storage-neutral chunk row; the database layer assigns its own primary key."""

    owner_id: str
    knowledge_base_id: str
    document_id: str
    source_version: str
    source_id: str
    role: str
    content: str
    seq: int
    start: int
    end: int
    parent_source_id: str | None = None
    context_header: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    index_ready: bool = False


@dataclass(frozen=True)
class IngestResult:
    parsed: ParsedDocument
    chunks: tuple[ChunkRecord, ...]
    config: ChunkConfig
    parser_engine: str


class OCRBackend(Protocol):
    """Optional later-stage OCR seam. Parsing itself leaves upstream image refs intact."""

    def recognize(self, image: bytes, media_type: str, *, source_type: str) -> str: ...


Parser = Callable[[DocumentRequest], ParsedDocument]


@dataclass(frozen=True)
class _Engine:
    name: str
    extensions: frozenset[str]
    parse: Parser


_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"})
_IMAGE_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_IMAGE_FUNCTION = re.compile(r"^=?(_xlfn\.)?(DISPIMG|IMAGE)\(", re.IGNORECASE)
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _failure(request: DocumentRequest, engine: str, stage: str, code: str, exc: BaseException) -> ParsedDocument:
    return ParsedDocument(
        content="",
        metadata={"filename": request.filename, "file_type": _extension(request.filename),
                  "parser_engine": engine, "input_bytes": len(request.data), "status": "failed",
                  "owner_id": request.owner_id, "knowledge_base_id": request.knowledge_base_id,
                  "document_id": request.document_id, "source_version": request.source_version},
        failures=(ParseFailure(stage, code, type(exc).__name__, str(exc)),),
    )


def _extension(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _base_metadata(request: DocumentRequest, engine: str, **extra: Any) -> dict[str, Any]:
    return {"filename": request.filename, "file_type": _extension(request.filename),
            "parser_engine": engine, "input_bytes": len(request.data), "status": "parsed",
            "owner_id": request.owner_id, "knowledge_base_id": request.knowledge_base_id,
            "document_id": request.document_id, "source_version": request.source_version, **extra}


def _decode_simple_text(raw: bytes) -> str:
    """SimpleFormatReader string(bytes), observed through Go JSON/rune iteration.

    Valid UTF-8 including BOM and literal U+FFFD is preserved. Go's invalid
    UTF-8 iterator emits one replacement rune per offending byte.
    """
    try:return raw.decode('utf-8')
    except UnicodeDecodeError:pass
    output=[];position=0
    while position<len(raw):
        first=raw[position]
        size=1 if first<128 else 2 if 0xC2<=first<=0xDF else 3 if 0xE0<=first<=0xEF else 4 if 0xF0<=first<=0xF4 else 0
        if size and position+size<=len(raw):
            try:
                value=raw[position:position+size].decode('utf-8')
                output.append(value);position+=size;continue
            except UnicodeDecodeError:pass
        output.append('\ufffd');position+=1
    return ''.join(output)


def _parse_plain(request: DocumentRequest) -> ParsedDocument:
    # Fixed builtin_converter.go TXT branch accepts raw content, not an
    # invented U+FFFD-based failure signal. Original bytes remain immutable.
    return ParsedDocument(_decode_simple_text(request.data),metadata=_base_metadata(request,'plain'))


def _parse_markdown(request: DocumentRequest) -> ParsedDocument:
    text = _decode_simple_text(request.data)
    from .upstream_markdown_helpers import extract_markdown_data_images, format_markdown_tables
    failures: list[ParseFailure] = []
    rewritten = format_markdown_tables(text)
    rewritten, image_outputs = extract_markdown_data_images(rewritten)
    assets = tuple(ParsedAsset(path, raw, media, "markdown_data_uri") for path, raw, media in image_outputs)

    return ParsedDocument(rewritten, assets, _base_metadata(request, "markdown", embedded_image_count=len(assets)), tuple(failures))


def _parse_markitdown(request: DocumentRequest, *, file_type: str | None = None) -> ParsedDocument:
    from markitdown import MarkItDown
    from ._office_runtime_adapter import CONFIG
    from .upstream_concurrency import parser_worker_limit
    from .upstream_markdown_helpers import extract_markdown_data_images, format_markdown_tables
    from .upstream_ppt_convert import normalize_ppt_bytes
    from .upstream_pptx_media import attach_pptx_media_to_markdown, markdown_needs_pptx_media_attach

    extension = _extension(request.filename) if file_type is None else file_type
    if extension and not extension.startswith("."):
        extension = "." + extension
    normalized_type = extension.lstrip(".").lower()
    data = request.data
    pptx_bytes = None
    if normalized_type in ("ppt", "pptx"):
        data, extension = normalize_ppt_bytes(data, extension)
        pptx_bytes = data
    elif normalized_type == "docx":
        from .upstream_docx_merge import fill_vertical_merged_cells_docx
        data = fill_vertical_merged_cells_docx(data)
    converter = MarkItDown()
    with parser_worker_limit("markitdown", CONFIG.markitdown_max_workers):
        try:
            result = converter.convert(io.BytesIO(data), file_extension=extension, keep_data_uris=True)
        except Exception:
            result = converter.convert(io.BytesIO(data), file_extension=extension, keep_data_uris=False)
    content = getattr(result, "text_content", "") or ""
    attached = {}
    if pptx_bytes is not None and markdown_needs_pptx_media_attach(content):
        content, attached = attach_pptx_media_to_markdown(content, pptx_bytes)
    content = format_markdown_tables(content)
    content, image_outputs = extract_markdown_data_images(content)
    assets = tuple(ParsedAsset(path, base64.b64decode(encoded, validate=True), "image/png", "pptx_media")
                   for path, encoded in attached.items()) + tuple(
        ParsedAsset(path, raw, media, "markitdown_data_uri") for path, raw, media in image_outputs)
    return ParsedDocument(content, assets,
                          _base_metadata(request, "markitdown", embedded_image_count=len(assets)))


def _parse_doc(request: DocumentRequest) -> ParsedDocument:
    from .upstream_doc_parser import DocParser
    source = DocParser(file_name=request.filename).parse_into_text(request.data)
    assets = tuple(ParsedAsset(path, base64.b64decode(encoded, validate=True),
                               mimetypes.guess_type(path)[0] or "image/png", "doc_embedded_image")
                   for path, encoded in source.images.items())
    return ParsedDocument(source.content, assets,
                          _base_metadata(request, "doc", embedded_image_count=len(assets)))


def _parse_image(request: DocumentRequest) -> ParsedDocument:
    if not request.data:
        raise ValueError("image input is empty")
    ext = _extension(request.filename) or ".png"
    media = _IMAGE_MEDIA.get(ext, "application/octet-stream")
    path = f"images/{_filename_base(request.filename)}"
    asset = ParsedAsset(path, request.data, media, "standalone_image")
    content = f"![{PurePosixPath(path).name}]({path})"
    return ParsedDocument(content, (asset,), _base_metadata(request, "image", image_source_type="standalone_image", embedded_image_count=1))


def _filename_base(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name


def _parse_pdf(request: DocumentRequest) -> ParsedDocument:
    from .upstream_pdf_parser import PDFParser
    overrides = _effective_parser_overrides(request)
    upstream = PDFParser(file_name=request.filename,
        pdf_force_scanned=overrides.get("pdf_force_scanned")).parse_into_text(request.data)
    assets: list[ParsedAsset] = []
    for index, (path, encoded) in enumerate(upstream.images.items(), start=1):
        raw = base64.b64decode(encoded, validate=True)
        basename = PurePosixPath(path).name
        scanned_match = re.search(r"_page_(\d+)\.jpg$", basename)
        embedded_match = re.search(r"_p(\d+)_img(\d+)\.jpg$", basename)
        vector_match = re.search(r"_p(\d+)_fig(\d+)\.jpg$", basename)
        page_match = scanned_match or embedded_match or vector_match
        page_no = int(page_match.group(1)) if page_match else None
        source_type = ("scanned_pdf" if scanned_match else "vector_pdf_figure" if vector_match
                       else "embedded_pdf_image")
        assets.append(ParsedAsset(path, raw, mimetypes.guess_type(path)[0] or "image/jpeg", source_type,
                                  page_no, {"upstream_image_order": index}))
    source_metadata = dict(upstream.metadata)
    scanned_count = int(source_metadata.get("scanned_page_count", 0))
    text_count = int(source_metadata.get("text_page_count", 0))
    page_count = scanned_count + text_count
    assets_by_page: dict[int, list[ParsedAsset]] = {}
    scanned_pages: set[int] = set()
    for asset in assets:
        if asset.page_number is None:
            continue
        assets_by_page.setdefault(asset.page_number, []).append(asset)
        if asset.source_type == "scanned_pdf":
            scanned_pages.add(asset.page_number)
    pages = [
        {"page_number": page_no,
         "route": "scanned_image" if page_no in scanned_pages else "text_layer",
         "asset_paths": [asset.path for asset in assets_by_page.get(page_no, [])]}
        for page_no in range(1, page_count + 1)
    ]
    metadata = _base_metadata(
        request, "pdf", page_count=page_count, scanned_page_count=scanned_count, text_page_count=text_count,
        embedded_image_count=int(source_metadata.get("embedded_image_count", 0)),
        vector_figure_count=int(source_metadata.get("vector_figure_count", 0)),
        image_source_type=source_metadata.get("image_source_type", "pdf_text_layer"),
        ocr_status="NOT_RUN",
        source_metadata=source_metadata,
        pages=pages,
    )
    return ParsedDocument(upstream.content, tuple(assets), metadata)


def _table_to_gfm_markdown(table: Any) -> str:
    """Adapt upstream docx_parser.table_to_gfm_markdown for a python-docx Table."""
    rows = [[re.sub(r"\s+", " ", cell.text or "").strip().replace("|", r"\|") for cell in row.cells]
            for row in table.rows]
    rows = [row for row in rows if row]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    if width == 0:
        return ""
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join("---" for _ in padded[0]) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


def _decode_docx_image_like_upstream(raw: bytes) -> bytes | None:
    """Mirror upstream `_extract_image_in_process` and `endecode.decode_image`.

    Upstream converts to RGBA, skips images below 50px in either dimension,
    scales to the 1920px long-edge cap, then base64-encodes the PIL image. With
    the converted image's unset format, `decode_image` serializes PNG.
    """
    from PIL import Image  # type: ignore[import-not-found]
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = opened.convert("RGBA")
        if image.width < 50 or image.height < 50:
            image.close()
            return None
        if image.width > 1920 or image.height > 1920:
            scale = min(1920 / image.width, 1920 / image.height)
            resized = image.resize((int(image.width * scale), int(image.height * scale)))
            image.close()
            image = resized
        output = io.BytesIO()
        image.save(output, format="PNG")
        image.close()
        return output.getvalue()
    except Exception:
        return None


def _parse_docx_builtin(request: DocumentRequest) -> ParsedDocument:
    """Execute the fixed-source DocxParser full page/process-pool fallback."""
    from .upstream_docx_parser import DocxParser

    source = DocxParser(file_name=request.filename).parse_into_text(request.data)
    assets = tuple(
        ParsedAsset(path, base64.b64decode(encoded, validate=True), mimetypes.guess_type(path)[0] or "image/png",
                    "docx_embedded_image")
        for path, encoded in source.images.items()
    )
    return ParsedDocument(source.content, assets,
                          _base_metadata(request, "docx", embedded_image_count=len(assets),
                                         docx_parser_route="docx_source_fallback"))


def _parse_docx(request: DocumentRequest) -> ParsedDocument:
    """Port upstream ``Docx2Parser(FirstParser(MarkitdownParser, DocxParser))``.

    MarkItDown is tried only when installed. Missing package or conversion failure
    falls through to the built-in python-docx parser, matching FirstParser rather
    than failing a supported DOCX upload.
    """
    try:
        from markitdown import MarkItDown  # type: ignore[import-not-found]  # optional parser engine
        from .upstream_docx_merge import fill_vertical_merged_cells_docx
        from .upstream_markdown_helpers import extract_markdown_data_images, format_markdown_tables

        converted_input = fill_vertical_merged_cells_docx(request.data)
        converter = MarkItDown()
        try:
            converted = converter.convert(io.BytesIO(converted_input), file_extension=".docx", keep_data_uris=True)
        except Exception:
            converted = converter.convert(io.BytesIO(converted_input), file_extension=".docx", keep_data_uris=False)
        content = format_markdown_tables(getattr(converted, "text_content", "") or "")
        content, image_outputs = extract_markdown_data_images(content)
        assets = tuple(ParsedAsset(path, raw, media, "docx_markdown_data_uri")
                       for path, raw, media in image_outputs)
        if not content.strip() and not assets:
            raise ValueError("MarkItDown produced an empty DOCX result")
        parsed = ParsedDocument(content, assets,
                                _base_metadata(request, "docx", embedded_image_count=len(assets),
                                               docx_parser_route="markitdown_primary"))
    except Exception:
        parsed = _parse_docx_builtin(request)
        metadata = dict(parsed.metadata)
        metadata["docx_parser_route"] = "docx_fallback"
        return replace(parsed, metadata=metadata)
    metadata = dict(parsed.metadata)
    metadata["parser_engine"] = "docx"
    metadata["docx_parser_route"] = "markitdown_primary"
    return replace(parsed, metadata=metadata)


def _cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_excel(request: DocumentRequest) -> ParsedDocument:
    from .upstream_excel_parser import ExcelParser
    first_row_header = _truthy(_effective_parser_overrides(request).get("xlsx_first_row_as_header", False))
    parser = ExcelParser(file_name=request.filename, file_type=_extension(request.filename).lstrip("."),
                         xlsx_first_row_as_header=first_row_header)
    parsed = parser.parse_into_text(request.data)
    rows = [{"seq": c.seq, "start": c.start, "end": c.end} for c in parsed.chunks]
    return ParsedDocument(parsed.content, metadata=_base_metadata(request, "excel",
        sheet_count=parser._parsed_sheet_count, row_count=len(parsed.chunks), row_sources=parser._native_row_sources, source_row_chunks=rows,
        xlsx_first_row_as_header=first_row_header, excel_source="fixed_upstream_pandas"))


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _effective_parser_overrides(request: DocumentRequest) -> dict[str, str]:
    merged = dict(request.tenant_parser_overrides)
    merged.update(request.upload_parser_overrides)
    return merged


class ParserRegistry:
    """WeKnora-style requested engine → extension default → builtin dispatch."""

    def __init__(self) -> None:
        self._engines: dict[str, _Engine] = {}
        self._defaults: dict[str, str] = {}
        self.scanned_pdf_engine: str = ""
        self._register_defaults()

    def register(self, name: str, extensions: Sequence[str], parser: Parser, *, default: bool = False) -> None:
        normalized = frozenset(ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions)
        self._engines[name] = _Engine(name, normalized, parser)
        if default:
            for ext in normalized:
                self._defaults[ext] = name

    def _register_defaults(self) -> None:
        self.register("pdf", (".pdf",), _parse_pdf, default=True)
        self.register("docx", (".docx",), _parse_docx, default=True)
        self.register("doc", (".doc",), _parse_doc, default=True)
        self.register("excel", (".xlsx", ".xlsm", ".xls"), _parse_excel, default=True)
        self.register("image", tuple(_IMAGE_EXTENSIONS), _parse_image, default=True)
        self.register("markdown", (".md", ".markdown"), _parse_markdown, default=True)
        self.register("plain", (".txt", ".text"), _parse_plain, default=True)
        self.register("markitdown", (".md", ".markdown", ".pdf", ".docx", ".doc", ".ppt", ".pptx",
                                      ".xlsx", ".xls", ".csv"), _parse_markitdown)
        for ext in (".ppt", ".pptx", ".csv"):
            self._defaults[ext] = "markitdown"
        self.register("builtin", tuple(self._defaults), self._dispatch_builtin)

    def _dispatch_builtin(self, request: DocumentRequest) -> ParsedDocument:
        ext = _extension(request.filename)
        engine = self._defaults.get(ext)
        if not engine:
            raise ValueError(f"unsupported file type: {ext or '(no extension)'}")
        return self._engines[engine].parse(request)

    def resolve(self, requested_engine: str, filename: str) -> str:
        ext = _extension(filename)
        requested = self._engines.get(requested_engine)
        if requested and ext in requested.extensions:
            return requested_engine
        if requested_engine == "mineru-basic":
            # Explicit scan-only local opt-in must never become the default PDF
            # parser when unavailable or used for an unsupported file type.
            raise ValueError("MINERU_EXPLICIT_PARSER_UNAVAILABLE")
        default = self._defaults.get(ext)
        if default:
            return default
        builtin = self._engines.get("builtin")
        if builtin and ext in builtin.extensions:
            return "builtin"
        raise ValueError(f"no parser engine supports extension {ext or '(no extension)'}")

    def parse(self, request: DocumentRequest) -> tuple[str, ParsedDocument]:
        ext = _extension(request.filename)
        if ext == ".docx" and request.data.startswith(_OLE_MAGIC):
            raise ValueError("legacy OLE document renamed as .docx requires the DOC reader")
        override_engine = _effective_parser_overrides(request).get(ext.lstrip("."))
        requested = request.parser_engine or override_engine or ""
        if not requested and ext == '.pdf' and self.scanned_pdf_engine:
            # Native (including existing OCR-layer) PDFs keep the native path.
            # Only a readable, wholly image-based PDF selects local Basic OCR.
            try:
                import fitz
                with fitz.open(stream=request.data, filetype='pdf') as document:
                    if not document.needs_pass and len(document) and not any(page.get_text().strip() for page in document):
                        requested = self.scanned_pdf_engine
            except Exception:
                pass  # Let the existing native parser report invalid PDFs.
        engine_name = self.resolve(requested, request.filename)
        engine = self._engines[engine_name]
        try:
            parsed = engine.parse(request)
        except Exception as exc:
            code = "PARSER_DEPENDENCY_MISSING" if isinstance(exc, ImportError) else "PARSER_FAILED"
            stage = "dependency" if isinstance(exc, ImportError) else "parse"
            parsed = _failure(request, engine_name, stage, code, exc)
        return engine_name, parsed


def parse_document(request: DocumentRequest, registry: ParserRegistry | None = None) -> tuple[str, ParsedDocument]:
    reg = registry or ParserRegistry()
    try:
        return reg.parse(request)
    except Exception as exc:
        return "", _failure(request, "", "dispatch", "PARSER_DISPATCH_FAILED", exc)


def resolve_chunk_config(
    kb_config: Mapping[str, Any], process_override: Mapping[str, Any] | None = None,
    upload_override: Mapping[str, Any] | None = None,
) -> ChunkConfig:
    """Apply KB snapshot, then present process/upload fields; explicit bools win."""
    values = dict(kb_config)
    for override in (process_override or {}, upload_override or {}):
        for key, value in override.items():
            if key == "enable_parent_child":
                if value is not None:
                    values[key] = _truthy(value)
            elif key in {"chunk_size", "chunk_overlap", "parent_chunk_size", "child_chunk_size", "token_limit"}:
                if isinstance(value, (int, float)) and value != 0:
                    values[key] = int(value)
            elif key in {"strategy"}:
                if value:
                    values[key] = str(value)
            elif key in {"separators", "languages"}:
                if value:
                    values[key] = tuple(str(v) for v in value)
    return ChunkConfig(
        chunk_size=int(values.get("chunk_size", 0) or 0),
        chunk_overlap=int(values.get("chunk_overlap", 0) or 0),
        separators=tuple(values.get("separators", ()) or ()),
        strategy=str(values.get("strategy", "") or ""),
        token_limit=int(values.get("token_limit", 0) or 0),
        languages=tuple(values.get("languages", ()) or ()),
        enable_parent_child=bool(values.get("enable_parent_child", False)),
        parent_chunk_size=int(values.get("parent_chunk_size", 0) or 0),
        child_chunk_size=int(values.get("child_chunk_size", 0) or 0),
    )


def ingest_document(request: DocumentRequest, registry: ParserRegistry | None = None) -> IngestResult:
    engine, parsed = parse_document(request, registry)
    config = resolve_chunk_config(request.kb_chunking_config, request.process_chunking_override, request.upload_chunking_override)
    if parsed.failures or not parsed.content:
        return IngestResult(parsed, (), config, engine)
    if "mineru_sections" in parsed.metadata and parsed.metadata["mineru_sections"] != []:
        from .sections import ingest_section_rows
        rows = ingest_section_rows(parsed, request, config)
        return IngestResult(parsed, _annotate_parser_locators(parsed, rows), config, engine)
    if config.enable_parent_child:
        parent_child = split_parent_child(parsed.content, config)
        rows: list[ChunkRecord] = []
        parent_ids = [f"{request.document_id}:parent:{parent.seq}" for parent in parent_child.parents]
        for parent in parent_child.parents:
            rows.append(_chunk_record(request, parent, "parent", None))
        for child in parent_child.children:
            parent_id = parent_ids[child.parent_index] if child.parent_index >= 0 else None
            rows.append(_chunk_record(request, child.chunk, "child", parent_id))
        return IngestResult(parsed, _annotate_parser_locators(parsed, rows), config, engine)
    rows = tuple(_chunk_record(request, chunk, "child", None) for chunk in split(parsed.content, config))
    return IngestResult(parsed, _annotate_parser_locators(parsed, rows), config, engine)


def _annotate_parser_locators(parsed: ParsedDocument, rows: Sequence[ChunkRecord]) -> tuple[ChunkRecord, ...]:
    if "mineru_source" not in parsed.metadata:
        return tuple(rows)
    from .mineru import chunk_locators
    return tuple(replace(row, metadata={**row.metadata, **chunk_locators(parsed, row.start, row.end)})
                 for row in rows)


def _chunk_record(request: DocumentRequest, chunk: Chunk, role: str, parent_id: str | None) -> ChunkRecord:
    return ChunkRecord(request.owner_id, request.knowledge_base_id, request.document_id, request.source_version,
                       f"{request.document_id}:{role}:{chunk.seq}", role, chunk.content, chunk.seq, chunk.start,
                       chunk.end, parent_id, chunk.context_header,
                       {"source_filename": request.filename}, False)


# --- Strategy-aware splitter. Rune offsets are Python Unicode code-point indices. ---
_PROTECTED_PATTERNS = tuple(re.compile(pattern, flags) for pattern, flags in (
    (r"\$\$.*?\$\$", re.DOTALL),
    (r"!\[[^\]\n]{0,200}\]\([^)\n]{1,500}\)", 0),
    (r"\[[^\]\n]{1,200}\]\([^)\n]{1,500}\)", 0),
    (r"(?m)[ ]*(?:\|[^|\n]*)+\|[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|[\r\n]+", 0),
    (r"(?m)[ ]*(?:\|[^|\n]*)+\|[\r\n]+", 0),
    (r"```(?:\w+)?[\r\n].*?```", re.DOTALL),
    (r"`[^`\r\n]+`", 0),
))
_MD_HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*#*\s*$")
_HEURISTIC_PATTERNS = (
    re.compile(r"(?m)^[ \t]*(?:\d+(?:\.\d+){1,3}\.?|(?:\d+|[IVX]{1,5})\.)[ \t]+\S.{0,200}$"),
    re.compile(r"(?m)^[ \t]*([A-ZÄÖÜ][A-ZÄÖÜ \-]{3,80}):?\s*$"),
    re.compile(r"(?m)^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$"),
    re.compile(r"(?m)^[ \t]*(?:Seite|Page|页码?)\s+\d+(?:\s*(?:von|of|/)\s*\d+)?[ \t]*$", re.I),
    re.compile(r"(?m)^[ \t]*(?:Kapitel|Abschnitt|Teil)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$"),
    re.compile(r"(?m)^[ \t]*(?:Chapter|Section|Part)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$"),
    re.compile(r"(?m)^[ \t]*第[ \t]*[一二三四五六七八九十百千零〇0-9]+[ \t]*(?:章|节|節|部分|篇)[ \t]?.{0,200}$"),
)


def _chars_for_token_limit(token_limit: int, language: str) -> int:
    ratios = {"en": 4.0, "de": 4.5, "zh": 1.7, "mixed": 3.0}
    return max(0, int(token_limit * ratios.get(language, ratios["mixed"]) * 0.9))


def _normalized(config: ChunkConfig) -> ChunkConfig:
    return replace(config,
                   chunk_size=config.chunk_size if config.chunk_size > 0 else 512,
                   chunk_overlap=config.chunk_overlap if config.chunk_overlap > 0 else 80,
                   separators=config.separators or ("\n\n", "\n", "。"))


def _ensure_defaults(config: ChunkConfig) -> ChunkConfig:
    cfg = _normalized(config)
    if cfg.token_limit > 0:
        lang = cfg.languages[0] if cfg.languages else "mixed"
        budget = _chars_for_token_limit(cfg.token_limit, lang)
        if budget > 0:
            cfg = replace(cfg, chunk_size=min(cfg.chunk_size, budget))
    return cfg


def _split_by_separators(text: str, separators: Sequence[str], chunk_size: int) -> list[str]:
    if text == "" or not separators or (chunk_size > 0 and len(text) <= chunk_size):
        return [text]
    for i, sep in enumerate(separators):
        if not sep:
            continue
        pieces = re.split(f"({re.escape(sep)})", text)
        if len(pieces) <= 1:
            continue
        remaining = separators[i + 1:]
        out: list[str] = []
        for piece in pieces:
            if piece:
                if chunk_size > 0 and len(piece) > chunk_size and remaining:
                    out.extend(_split_by_separators(piece, remaining, chunk_size))
                else:
                    out.append(piece)
        return out
    return [text]


@dataclass
class _Unit:
    text: str
    start: int
    end: int


def _protected_spans(text: str) -> list[tuple[int, int]]:
    matches = [(m.start(), m.end()) for pat in _PROTECTED_PATTERNS for m in pat.finditer(text) if m.end() > m.start()]
    matches.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    output: list[tuple[int, int]] = []
    last_end = 0
    for start, end in matches:
        if start >= last_end:
            output.append((start, end))
            last_end = end
    return output


def _build_units(text: str, protected: Sequence[tuple[int, int]], separators: Sequence[str], chunk_size: int) -> list[_Unit]:
    units: list[_Unit] = []
    byte_pos = 0  # Python indexes are Unicode rune offsets, but byte_pos is text index here.
    for start, end in protected:
        if start > byte_pos:
            pre = text[byte_pos:start]
            cursor = byte_pos
            for part in _split_by_separators(pre, separators, chunk_size):
                units.append(_Unit(part, cursor, cursor + len(part)))
                cursor += len(part)
        content = text[start:end]
        if len(content) > 7500:
            offset = 0
            while offset < len(content):
                cut = min(offset + 7500, len(content))
                if cut < len(content):
                    for i in range(cut - 1, max(offset, cut - 200), -1):
                        if content[i] in "\n ":
                            cut = i + 1
                            break
                units.append(_Unit(content[offset:cut], start + offset, start + cut))
                offset = cut
        else:
            units.append(_Unit(content, start, end))
        byte_pos = end
    if byte_pos < len(text):
        cursor = byte_pos
        for part in _split_by_separators(text[byte_pos:], separators, chunk_size):
            units.append(_Unit(part, cursor, cursor + len(part)))
            cursor += len(part)
    return units


def _split_text(text: str, config: ChunkConfig) -> list[Chunk]:
    if not text:
        return []
    cfg = config
    size = cfg.chunk_size if cfg.chunk_size > 0 else 512
    overlap = max(0, cfg.chunk_overlap)
    units = _build_units(text, _protected_spans(text), cfg.separators, size)
    return _merge_units(units, size, overlap)


def _merge_units(units: Sequence[_Unit], size: int, overlap: int) -> list[Chunk]:
    if not units:
        return []
    output: list[Chunk] = []
    current: list[_Unit] = []
    current_len = 0
    header = _TableHeaderTracker()
    for unit in units:
        unit_len = len(unit.text)
        if unit_len > 7500:
            if current:
                output.append(_build_chunk(current, len(output)))
                current, current_len = [], 0
            for off in range(0, unit_len, 7500):
                end = min(unit_len, off + 7500)
                output.append(Chunk(unit.text[off:end], len(output), unit.start + off, unit.start + end))
            continue
        header.update(unit.text)
        if header.ended_this_unit and current:
            output.append(_build_chunk(current, len(output)))
            current, current_len = [], 0
        active_header = header.value
        header_len = len(active_header)
        if header_len > size:
            active_header, header_len = "", 0
        if current_len + unit_len + header_len > size and current:
            output.append(_build_chunk(current, len(output)))
            current, current_len = _compute_overlap(current, overlap, size, unit_len)
            if active_header and header_len + unit_len <= size:
                while current and current_len + unit_len + header_len > size:
                    current_len -= len(current[0].text)
                    current = current[1:]
                overlap_text = "".join(item.text for item in current)
                if not _header_already_present(active_header, overlap_text, unit.text) and not _header_column_mismatch(active_header, unit.text):
                    start_pos = current[0].start if current else unit.start
                    current.insert(0, _Unit(active_header, start_pos, start_pos))
                    current_len += header_len
        if current_len + unit_len > 7500 and current:
            output.append(_build_chunk(current, len(output)))
            current, current_len = [], 0
        current.append(unit)
        current_len += unit_len
    if current:
        output.append(_build_chunk(current, len(output)))
    return output


_TABLE_HEADER_START = re.compile(r"(?is)^\s*(?:\|[^|\n]*)+[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|?[\r\n]+$")
_TABLE_ROW = re.compile(r"(?m)^\s*(?:\|[^|\n]*)+\|\s*$")


class _TableHeaderTracker:
    def __init__(self) -> None:
        self.value = ""
        self.ended = False
        self.ended_this_unit = False
        self.pending_table_break = False

    def update(self, unit: str) -> None:
        self.ended_this_unit = False
        if self.pending_table_break:
            self.pending_table_break = False
            if self.value and _first_table_columns(unit):
                self.value = ""
                self.ended = True
                self.ended_this_unit = True
            elif self.value:
                self.value = ""
                self.ended = True
        if self.value and (not unit.strip() or (unit.strip() and not unit.lstrip().startswith("|"))):
            self.value = ""
            self.ended = True
        if self.value:
            if unit.rstrip(" \t\r").endswith(("\n\n", "\r\n\r\n")):
                self.pending_table_break = True
            else:
                header_cols, row_cols = _header_columns(self.value), _first_table_columns(unit)
                if row_cols and header_cols and row_cols != header_cols:
                    self.value = ""
                    self.ended = True
                    self.ended_this_unit = True
        if not self.value and not self.ended:
            match = _TABLE_HEADER_START.fullmatch(unit)
            if match:
                self.value = match.group(0)
        if not self.value:
            self.ended = False


def _table_columns(line: str) -> int:
    line = line.strip()
    if not line.startswith("|"):
        return 0
    parts = line.split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return len(parts)


def _first_table_columns(text: str) -> int:
    for line in text.split("\n"):
        line = line.strip()
        if line and _TABLE_ROW.fullmatch(line):
            return _table_columns(line)
    return 0


def _header_columns(header: str) -> int:
    for line in header.split("\n"):
        line = line.strip()
        if line and "---" not in line and (cols := _table_columns(line)):
            return cols
    return 0


def _header_column_mismatch(headers: str, next_unit: str) -> bool:
    header_cols, row_cols = _header_columns(headers), _first_table_columns(next_unit)
    return header_cols > 0 and row_cols > 0 and header_cols != row_cols


def _header_already_present(headers: str, overlap_text: str, unit_text: str) -> bool:
    if headers in overlap_text or headers in unit_text:
        return True
    for line in headers.split("\n"):
        line = line.strip()
        if not line or "---" in line or not any(char not in "| \t" for char in line):
            continue
        return line in overlap_text or line in unit_text
    return False


def _build_chunk(units: Sequence[_Unit], seq: int) -> Chunk:
    return Chunk("".join(unit.text for unit in units), seq, units[0].start, units[-1].end)


def _compute_overlap(current: Sequence[_Unit], overlap: int, size: int, next_len: int) -> tuple[list[_Unit], int]:
    """Port upstream splitter.go's semanticOverlapWindow/computeOverlap path.

    The suffix can start inside a splitter unit. Keeping source positions for
    that slice is required for arbitrary separator choices and multilingual
    text; replacing it with one synthetic suffix only matched a narrow fixture
    set and was not source-equivalent.
    """
    max_overlap = min(overlap, size - next_len)
    if max_overlap <= 0:
        return [], 0
    window = _semantic_overlap_window(current, max_overlap + 4)
    text = "".join(unit.text for unit in window)
    if not text:
        return [], 0
    boundary_end = _find_semantic_overlap_boundary(text, max(0, len(text) - max_overlap))
    if boundary_end is None:
        return [], 0
    retained = _trim_units_prefix(window, boundary_end)
    retained_len = sum(len(unit.text) for unit in retained)
    if retained_len <= 0 or retained_len > max_overlap or not "".join(unit.text for unit in retained).strip():
        return [], 0
    return retained, retained_len


def _semantic_overlap_window(current: Sequence[_Unit], maximum: int) -> list[_Unit]:
    """Return a source-backed suffix, slicing the first retained unit as needed."""
    if maximum <= 0 or not current:
        return []
    remaining = maximum
    reversed_units: list[_Unit] = []
    for unit in reversed(current):
        length = len(unit.text)
        if length == 0:
            continue
        if unit.start == unit.end or unit.end - unit.start != length:
            break  # generated context headers are a source-offset boundary
        if length <= remaining:
            reversed_units.append(unit)
            remaining -= length
            continue
        offset = length - remaining
        reversed_units.append(_Unit(unit.text[offset:], unit.start + offset, unit.end))
        remaining = 0
    return list(reversed(reversed_units))


def _find_semantic_overlap_boundary(text: str, min_end: int) -> int | None:
    """Find the earliest eligible boundary at the best source priority."""
    if not text or min_end > len(text):
        return None
    protected = _protected_spans(text)

    def inside_protected(position: int) -> bool:
        for start, end in protected:
            if position < start:
                return False
            if start <= position < end:
                return True
        return False

    def consider(start: int, end: int, priority: int) -> None:
        nonlocal best
        if start < 0 or end <= start or end < min_end or end > len(text):
            return
        if inside_protected(start) or not text[end:].strip():
            return
        candidate = (priority, start, end)
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    best: tuple[int, int, int] | None = None
    paragraph = [False] * len(text)
    index = 0
    while index < len(text):
        if text.startswith("\r\n\r\n", index):
            consider(index, index + 4, 1)
            paragraph[index:index + 4] = [True] * 4
            index += 4
            continue
        if text.startswith("\n\n", index):
            consider(index, index + 2, 1)
            paragraph[index:index + 2] = [True] * 2
            index += 2
            continue
        index += 1

    index = 0
    while index < len(text):
        if paragraph[index]:
            index += 1
            continue
        if text.startswith("\r\n", index):
            if index + 1 < len(paragraph) and not paragraph[index + 1]:
                consider(index, index + 2, 2)
            index += 2
            continue
        if text[index] == "\n":
            consider(index, index + 1, 2)
        index += 1

    for index, char in enumerate(text):
        if char in "。？！":
            consider(index, index + 1, 3)
        elif char in ".?!" and index + 1 < len(text) and text[index + 1] == " ":
            consider(index, index + 2, 3)
    return best[2] if best is not None else None


def _trim_units_prefix(units: Sequence[_Unit], prefix_length: int) -> list[_Unit]:
    if prefix_length <= 0:
        return list(units)
    remaining = prefix_length
    output: list[_Unit] = []
    for unit in units:
        unit_len = len(unit.text)
        if remaining >= unit_len:
            remaining -= unit_len
            continue
        if remaining > 0:
            output.append(_Unit(unit.text[remaining:], unit.start + remaining, unit.end))
            remaining = 0
        else:
            output.append(unit)
    return output


def _profile(text: str) -> dict[str, Any]:
    lines = text.split("\n")
    heading_counts = {i: 0 for i in range(1, 7)}
    in_fence = False
    heuristic = 0
    chapter = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        h = _MD_HEADING.match(line)
        if h:
            heading_counts[len(h.group(1))] += 1
            continue
        if any(_HEURISTIC_PATTERNS[index].search(line) for index in (0, 1, 2, 4, 5, 6)):
            heuristic += 1
        if any(_HEURISTIC_PATTERNS[index].search(line) for index in (4, 5, 6)):
            chapter += 1
    heading_total = sum(heading_counts.values())
    dominant = next((i for i in range(1, 7) if heading_counts[i] >= 3), 0)
    if not dominant:
        dominant = next((i for i in range(6, 0, -1) if heading_counts[i]), 0)
    chain: list[str] = []
    if heading_total >= 3 and heading_total / max(1, len(lines)) > 0.005 and dominant:
        chain.append("heading")
    if heuristic >= 5 or "\f" in text or chapter:
        chain.append("heuristic")
    chain.append("legacy")
    return {"headings": heading_counts, "heading_total": heading_total, "dominant": dominant, "chain": chain}


def _resolve_chain(text: str, strategy: str) -> list[str]:
    strategy = strategy.lower()
    if strategy == "heading":
        return ["heading", "legacy"]
    if strategy == "heuristic":
        return ["heuristic", "legacy"]
    if strategy in {"recursive", "legacy", ""}:
        return ["legacy"]
    return _profile(text)["chain"]


def _heading_split(text: str, cfg: ChunkConfig) -> list[Chunk]:
    lines = list(re.finditer(r"(?m)^.*(?:\n|$)", text))
    profile = _profile(text)
    level = profile["dominant"]
    if not level:
        return _split_text(text, cfg)
    boundaries: list[tuple[int, str]] = [(0, "")]
    in_fence = False
    for match in lines:
        line = match.group(0).rstrip("\r\n")
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _MD_HEADING.match(line)
        if heading and 1 <= len(heading.group(1)) <= level:
            if match.start() > 0:
                boundaries.append((match.start(), line))
            else:
                boundaries[0] = (0, line)
    if len(boundaries) <= 1:
        return _split_text(text, cfg)
    boundaries.sort()
    out: list[Chunk] = []
    hierarchy: list[str] = ["", "", "", "", "", ""]
    for i, (start, title) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        body = text[start:end]
        if not body:
            continue
        if title:
            current_heading = _MD_HEADING.match(title)
            if current_heading:
                title_level = len(current_heading.group(1))
                hierarchy[title_level - 1] = current_heading.group(2).strip()
                for deeper in range(title_level, 6):
                    hierarchy[deeper] = ""
        header = "\n".join(f"{'#' * (idx + 1)} {value}" for idx, value in enumerate(hierarchy) if value)
        section_hierarchy = hierarchy.copy()
        for subheading in _markdown_headings(body, min_level=level + 1):
            _observe_heading(section_hierarchy, subheading[1])
        if len(header) + 2 + len(body) <= cfg.chunk_size:
            out.append(Chunk(body, len(out), start, end, header))
        else:
            for sub in _split_text(body, cfg):
                sub_header = _breadcrumb_at(body, level, hierarchy, sub.start, header)
                out.append(Chunk(sub.content, len(out), start + sub.start, start + sub.end, sub_header))
        hierarchy = section_hierarchy
    return _coalesce_tiny_chunks(out, cfg.chunk_size)


def _observe_heading(stack: list[str], heading: str) -> None:
    match = _MD_HEADING.match(heading)
    if not match:
        return
    level = len(match.group(1))
    stack[level - 1] = match.group(2).strip()
    for idx in range(level, 6):
        stack[idx] = ""


def _markdown_headings(text: str, *, min_level: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    pos = 0
    in_fence = False
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            match = _MD_HEADING.match(line)
            if match and len(match.group(1)) >= min_level:
                found.append((pos, line))
        pos += len(line) + (index < len(lines) - 1)
    return found


def _breadcrumb(stack: Sequence[str]) -> str:
    return "\n".join(f"{'#' * (idx + 1)} {value}" for idx, value in enumerate(stack) if value)


def _breadcrumb_at(section: str, primary_level: int, seed: Sequence[str], offset: int, fallback: str) -> str:
    stack = list(seed)
    selected = _breadcrumb(stack)
    for start, line in _markdown_headings(section, min_level=primary_level + 1):
        if start > offset:
            break
        _observe_heading(stack, line)
        selected = _breadcrumb(stack)
    return selected or fallback


def _common_heading_prefix(left: str, right: str) -> str:
    if not left or not right:
        return ""
    a, b = left.split("\n"), right.split("\n")
    common: list[str] = []
    for x, y in zip(a, b):
        if x != y:
            break
        common.append(x)
    return "\n".join(common)


def _coalesce_tiny_chunks(chunks: Sequence[Chunk], chunk_size: int) -> list[Chunk]:
    if len(chunks) <= 1 or chunk_size <= 0:
        return list(chunks)
    target = max(chunk_size // 2, 200)
    output: list[Chunk] = []
    current = chunks[0]
    for next_chunk in chunks[1:]:
        current_len, next_len = len(current.content), len(next_chunk.content)
        prefix = _common_heading_prefix(current.context_header, next_chunk.context_header)
        if prefix and current.end == next_chunk.start and current_len < target and current_len + next_len <= chunk_size:
            current = replace(current, content=current.content + next_chunk.content,
                              end=next_chunk.end, context_header=prefix)
        else:
            output.append(current)
            current = next_chunk
    output.append(current)
    return [replace(chunk, seq=i) for i, chunk in enumerate(output)]


def _heuristic_split(text: str, cfg: ChunkConfig) -> list[Chunk]:
    if not text:
        return []
    if len(text) <= cfg.chunk_size:
        return _split_text(text, cfg)
    bounds: set[int] = {m.start() for m in re.finditer(r"\f", text)}
    lines = text.split("\n")
    pos = 0
    in_fence = False
    chapter_patterns = _HEURISTIC_PATTERNS[-3:]
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            if any(pattern.match(line) for pattern in chapter_patterns):
                bounds.add(pos)
            elif _HEURISTIC_PATTERNS[0].match(line) or _HEURISTIC_PATTERNS[1].match(line) or _HEURISTIC_PATTERNS[2].match(line) or _HEURISTIC_PATTERNS[3].match(line):
                bounds.add(pos)
        pos += len(line) + (1 if i < len(lines) - 1 else 0)
    # Three or more newlines: boundary after the blank block.
    bounds.update(len(text[:match.end()]) for match in re.finditer(r"\n{3,}", text))
    if not bounds:
        return _split_text(text, cfg)
    ordered = sorted(bounds)
    protected = _protected_spans(text)
    ordered = [bound for bound in ordered if not any(start < bound < end for start, end in protected)]
    ordered.append(len(text))
    if ordered[0] != 0:
        ordered.insert(0, 0)
    out: list[Chunk] = []
    chunk_start = ordered[0]
    cur_end = chunk_start
    seq = 0
    min_chunk_size = max(cfg.chunk_size // 4, 50)
    for next_end in ordered[1:]:
        block_len = next_end - cur_end
        if block_len > cfg.chunk_size:
            if cur_end - chunk_start > 0:
                out = _append_chunk(out, text, chunk_start, cur_end, seq)
                seq = len(out)
                chunk_start = cur_end
            out = _append_oversize(out, text, cur_end, next_end, cfg, seq)
            seq = len(out)
            cur_end = next_end
            chunk_start = next_end
            continue
        accumulated = next_end - chunk_start
        if accumulated > cfg.chunk_size and cur_end - chunk_start >= min_chunk_size:
            out = _append_chunk(out, text, chunk_start, cur_end, seq)
            seq = len(out)
            chunk_start = _apply_overlap_aligned(text, cur_end, cfg.chunk_overlap, ordered)
        cur_end = next_end
    if cur_end > chunk_start:
        out = _append_chunk(out, text, chunk_start, cur_end, seq)
    return out


def _append_chunk(out: list[Chunk], text: str, start: int, end: int, seq: int) -> list[Chunk]:
    if end <= start:
        return out
    raw = text[start:end]
    if not raw.strip():
        return out
    out.append(Chunk(raw, seq, start, end))
    return out


def _append_oversize(out: list[Chunk], text: str, start: int, end: int, cfg: ChunkConfig, seq: int) -> list[Chunk]:
    for sub in _split_text(text[start:end], cfg):
        out.append(Chunk(sub.content, seq, start + sub.start, start + sub.end))
        seq += 1
    return out


def _apply_overlap_aligned(text: str, cur_end: int, overlap: int, bounds: Sequence[int]) -> int:
    if overlap <= 0:
        return cur_end
    target = max(0, cur_end - overlap)
    window_start = max(0, cur_end - 2 * overlap)
    candidates = [b for b in bounds if window_start <= b < cur_end]
    if candidates:
        return max(candidates)
    for i in range(min(target, len(text) - 1), window_start, -1):
        if text[i] == "\n":
            return i + 1
    return target


def _validate(chunks: Sequence[Chunk], total_chars: int, chunk_size: int) -> tuple[bool, str]:
    if not chunks:
        return False, "no chunks produced"
    lengths = [len(chunk.content) for chunk in chunks]
    if len(chunks) == 1 and total_chars > 2 * chunk_size:
        return False, "single chunk for large document"
    tiny = sum(length < 50 for length in lengths[:-1])
    if tiny > len(chunks) // 4 and tiny > 2:
        return False, "too many tiny chunks"
    if max(lengths) < chunk_size // 4 and total_chars > chunk_size:
        return False, "all chunks far below target size"
    if max(lengths) > 2 * chunk_size and chunk_size > 0:
        return False, "chunk exceeds 2x target size"
    return True, ""


def split(text: str, config: ChunkConfig | None = None, *, with_diagnostics: bool = False) -> tuple[Chunk, ...] | tuple[tuple[Chunk, ...], SplitDiagnostics]:
    cfg = _ensure_defaults(config or ChunkConfig())
    if not text:
        empty_diag = SplitDiagnostics("legacy", (), ())
        return ((), empty_diag) if with_diagnostics else ()
    chain = _resolve_chain(text, cfg.strategy)
    attempts = {"heading": _heading_split, "heuristic": _heuristic_split, "legacy": _split_text}
    rejected: list[tuple[str, str]] = []
    last: list[Chunk] | None = None
    for tier in chain:
        candidate = attempts[tier](text, cfg)
        ok, reason = _validate(candidate, len(text), cfg.chunk_size)
        if ok:
            result = tuple(candidate)
            diagnostics = SplitDiagnostics(tier, tuple(chain), tuple(rejected))
            return (result, diagnostics) if with_diagnostics else result
        rejected.append((tier, reason))
        if tier == "legacy" and tier == chain[-1]:
            last = candidate
    result = tuple(last if last is not None else _split_text(text, cfg))
    diagnostics = SplitDiagnostics("legacy", tuple(chain), tuple(rejected))
    return (result, diagnostics) if with_diagnostics else result


def _derive_parent_child(base: ChunkConfig) -> tuple[ChunkConfig, ChunkConfig]:
    parent_size = base.parent_chunk_size if base.parent_chunk_size > 0 else 4096
    child_size = base.child_chunk_size if base.child_chunk_size > 0 else 384
    parent = replace(base, chunk_size=parent_size, token_limit=0, enable_parent_child=False)
    child = replace(base, chunk_size=child_size, chunk_overlap=child_size // 5, enable_parent_child=False)
    return parent, child


def split_parent_child(text: str, config: ChunkConfig) -> ParentChildResult:
    parent_cfg, child_cfg = _derive_parent_child(_normalized(config))
    parents, _diag = split(text, parent_cfg, with_diagnostics=True)
    stored_parents: list[Chunk] = []
    children: list[ChildChunk] = []
    child_seq = 0
    for parent in parents:
        subs = split(parent.content, child_cfg)
        parent_index = -1
        if len(subs) > 1 or (len(subs) == 1 and subs[0].content != parent.content):
            parent_index = len(stored_parents)
            stored_parents.append(parent)
        for sub in subs:
            child = replace(sub, seq=child_seq, start=sub.start + parent.start, end=sub.end + parent.start,
                            context_header=_merge_breadcrumbs(parent.context_header, sub.context_header))
            children.append(ChildChunk(child, parent_index))
            child_seq += 1
    return ParentChildResult(tuple(stored_parents), tuple(children))


def _merge_breadcrumbs(parent: str, child: str) -> str:
    if not parent:
        return child
    if not child:
        return parent
    parent_lines, child_lines = parent.split("\n"), child.split("\n")
    if parent_lines and child_lines and parent_lines[-1].strip() == child_lines[0].strip():
        child_lines = child_lines[1:]
    return parent if not child_lines else parent + "\n" + "\n".join(child_lines)
