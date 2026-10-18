"""Explicit local MinerU Basic OCR adapter; no provider or database dependencies.

The existing NormalizedDocument is a pure conversion DTO here. It does not select
the old application's parser, chunker, repositories, or execution services.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import mimetypes
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from backend.app.domain.models import DocumentAsset, NormalizedDocument, SourceLocator

from .core import DocumentRequest, ParsedAsset, ParsedDocument, ParseFailure, ParserRegistry

ENGINE = "mineru-basic"
VERSION = "4.0.12"
SCHEMA = "docvortex.middle"
SCHEMA_VERSION = "2.0"
EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")
EVIDENCE_ROLE = "parser_evidence"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class MinerUError(ValueError):
    pass


def _fail(code: str) -> None:
    raise MinerUError(code)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_asset_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\x00" in value or value.startswith("/")):
        _fail("MINERU_ASSET_PATH_INVALID")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in value.split("/")) or path.is_absolute():
        _fail("MINERU_ASSET_PATH_INVALID")
    return value


def inspect_source(request: DocumentRequest) -> int:
    """Fail explicitly for native/mixed PDFs; this option is scan/image only."""
    ext = Path(request.filename).suffix.lower()
    if ext not in EXTENSIONS:
        _fail("MINERU_SOURCE_TYPE_UNSUPPORTED")
    if ext == ".pdf":
        import fitz
        with fitz.open(stream=request.data, filetype="pdf") as document:
            if document.needs_pass or not len(document):
                _fail("MINERU_PDF_UNSUPPORTED")
            if any(page.get_text().strip() for page in document):
                _fail("MINERU_NATIVE_PDF_UNSUPPORTED")
            return len(document)
    from PIL import Image
    with Image.open(io.BytesIO(request.data)) as image:
        if getattr(image, "n_frames", 1) != 1:
            _fail("MINERU_MULTIFRAME_IMAGE_UNSUPPORTED")
        image.verify()
    return 1


@dataclass(frozen=True)
class MinerUSettings:
    python: Path
    model_root: Path
    temp_root: Path
    timeout_seconds: int = 900

    def __post_init__(self):
        for path in (self.python, self.model_root, self.temp_root):
            if not Path(path).is_absolute():
                _fail("MINERU_ABSOLUTE_PATHS_REQUIRED")
        if self.timeout_seconds <= 0:
            _fail("MINERU_TIMEOUT_INVALID")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None):
        env = os.environ if environment is None else environment
        if env.get("WEKNORA_PORT_MINERU_ENABLED", "").lower() not in {"1", "true"}:
            return None
        names = ("PYTHON", "MODEL_ROOT", "TEMP_ROOT")
        values = [env.get("WEKNORA_PORT_MINERU_" + name, "") for name in names]
        if not all(values):
            _fail("MINERU_EXPLICIT_PATHS_REQUIRED")
        return cls(*(Path(value) for value in values),
                   timeout_seconds=int(env.get("WEKNORA_PORT_MINERU_TIMEOUT_SECONDS", "900")))


def local_environment(settings: MinerUSettings, work: Path) -> dict[str, str]:
    # Do not inherit provider credentials, proxy endpoints, Python injections or
    # arbitrary MinerU VLM/API settings from the parent service.
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT")
           if key in os.environ}
    config = work / "mineru-local.json"
    config.write_text(json.dumps({"model": {"source": "local", "base_dir": str(settings.model_root),
                                            "small_backend": "onnx"}}), encoding="utf-8")
    env.update(MINERU_CONFIG=str(config), MINERU_HOME=str(work / "home"),
               MINERU_MODEL_SOURCE="local", MINERU_MODEL_SMALL_BACKEND="onnx",
               HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", TRANSFORMERS_OFFLINE="1",
               HF_HOME=str(work / "cache"), MODELSCOPE_CACHE=str(work / "cache"),
               XDG_CACHE_HOME=str(work / "cache"), TORCH_HOME=str(work / "cache"),
               MPLCONFIGDIR=str(work / "mpl"), TEMP=str(work), TMP=str(work),
               PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return env


@dataclass(frozen=True)
class MinerUNormalization:
    normalized: NormalizedDocument
    parsed: ParsedDocument


def normalize_archive(request: DocumentRequest, archive: bytes, receipt: Mapping[str, Any],
                      *, allow_partial: bool = False) -> MinerUNormalization:
    """Read saved local SDK bytes; never infer source offsets from SDK Markdown."""
    digest = _sha(request.data)
    if receipt.get("source_sha256") != digest:
        _fail("MINERU_SOURCE_HASH_MISMATCH")
    count = inspect_source(request)
    if receipt.get("source_page_count", count) != count:
        _fail("MINERU_PAGE_COUNT_MISMATCH")
    if len(archive) > MAX_ARCHIVE_BYTES:
        _fail("MINERU_ARCHIVE_TOO_LARGE")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        members = zipped.infolist()
        names = [safe_asset_path(item.filename) for item in members if not item.is_dir()]
        if len(names) != len(set(names)) or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
            _fail("MINERU_ARCHIVE_INVALID")
        files = {name: zipped.read(name) for name in names}
    if "middle_json.json" not in files:
        _fail("MINERU_MIDDLE_MISSING")
    middle_bytes = files["middle_json.json"]
    middle = json.loads(middle_bytes)
    if middle.get("schema") != SCHEMA or middle.get("schema_version") != SCHEMA_VERSION:
        _fail("MINERU_SCHEMA_UNSUPPORTED")
    producer = middle.get("metadata", {}).get("producer")
    if producer != {"name": "mineru", "version": VERSION}:
        _fail("MINERU_VERSION_UNSUPPORTED")
    mode = middle.get("extensions", {}).get("mineru", {})
    if mode.get("tier") != "basic" or mode.get("parse_mode") != "ocr":
        _fail("MINERU_OCR_MODE_REQUIRED")
    pages = middle.get("pages")
    if not isinstance(pages, list) or not pages:
        _fail("MINERU_PAGES_INVALID")
    indices = [page.get("page_idx") for page in pages]
    if (any(type(index) is not int or index < 0 or index >= count for index in indices)
            or indices != sorted(set(indices))):
        _fail("MINERU_PAGE_INDEX_INVALID")
    full = middle.get("is_full_document") is True
    if full and indices != list(range(count)):
        _fail("MINERU_PAGE_COVERAGE_INVALID")
    if not full and not allow_partial:
        _fail("MINERU_PARTIAL_DOCUMENT_UNSUPPORTED")

    middle_ref = {"logical_path": "mineru/middle_json.json", "sha256": _sha(middle_bytes)}
    identity = {"source_sha256": digest, "source_version": request.source_version,
                "document_id": request.document_id, "parser_engine": ENGINE, "parser_version": VERSION,
                "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "producer": producer,
                "offset_space": "derived_unicode_codepoints", "middle": middle_ref,
                "page_indices": indices, "is_full_document": full, "source_page_count": count}
    assets: list[ParsedAsset] = []
    # Keep exact sidecar bytes. Their logical paths, not temporary absolute paths,
    # are carried by source locators and the lifecycle's immutable asset manifest.
    for name, data in files.items():
        assets.append(ParsedAsset("mineru/" + name, data,
            mimetypes.guess_type(name)[0] or "application/octet-stream", "mineru_evidence",
            metadata={"processing_role": EVIDENCE_ROLE, "source_sha256": digest,
                      "parser_engine": ENGINE, "sha256": _sha(data)}))
    # For standalone images keep the actual uploaded bytes too. MinerU may report
    # file_suffix=pdf after internal conversion; it never replaces this identity.
    if Path(request.filename).suffix.lower() != ".pdf":
        assets.append(ParsedAsset("mineru/original" + Path(request.filename).suffix.lower(),
            request.data, mimetypes.guess_type(request.filename)[0] or "application/octet-stream",
            "original_image", 1, {"processing_role": EVIDENCE_ROLE, "source_sha256": digest,
                                  "parser_engine": ENGINE, "sha256": digest}))

    text_parts: list[str] = []
    locators: list[SourceLocator] = []
    block_manifest: list[dict[str, Any]] = []
    asset_locations: dict[str, list[dict[str, Any]]] = {}
    position = 0
    layout = middle.get("extensions", {}).get("docvortex_layout", {}).get("pages", [])
    dimensions = {p.get("page_idx"): {k: p[k] for k in ("width_pt", "height_pt") if k in p}
                  for p in layout if isinstance(p, dict)}

    def emit(block: Mapping[str, Any], path: str, page_idx: int):
        nonlocal position
        kind = block.get("type")
        if not isinstance(kind, str):
            _fail("MINERU_BLOCK_INVALID")
        bbox = block.get("bbox")
        if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in bbox)
                or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
            _fail("MINERU_BBOX_INVALID")
        index = block.get("index")
        if index is not None and (type(index) is not int or index < 0):
            _fail("MINERU_BLOCK_INDEX_INVALID")
        proof: dict[str, Any] = {"page_idx": page_idx, "block_index": index, "block_path": path,
            "block_type": kind, "bbox": bbox, "coordinate_space": "normalized_0_1",
            "page_dimensions": dimensions.get(page_idx, {}), "middle": middle_ref,
            "source_sha256": digest, "offset_space": "derived_unicode_codepoints"}
        image_path = block.get("image_path")
        if block.get("image_url") or block.get("image_base64"):
            _fail("MINERU_EXTERNAL_ASSET_UNSUPPORTED")
        if image_path:
            safe_asset_path(image_path)
            if image_path not in files:
                _fail("MINERU_ASSET_MISSING")
            proof["asset"] = {"logical_path": "mineru/" + image_path, "sha256": _sha(files[image_path])}
            asset_locations.setdefault("mineru/" + image_path, []).append(
                {"page_idx": page_idx, "block_index": index, "block_path": path, "bbox": bbox})
        content = block.get("content", "")
        if isinstance(content, list) and any(isinstance(item, dict) and
                ("index" in item or "bbox" in item or item.get("type") not in
                 {"text", "inline_equation", "equation", "hyperlink"}) for item in content):
            block_manifest.append(proof)
            for i, child in enumerate(content):
                if not isinstance(child, dict):
                    _fail("MINERU_BLOCK_INVALID")
                emit(child, path + "/content/" + str(i), page_idx)
            return
        if isinstance(content, list):
            # Only visible source span content, without invented Markdown styling.
            values = []
            for span in content:
                if not isinstance(span, dict) or not isinstance(span.get("content"), str):
                    _fail("MINERU_SPAN_UNSUPPORTED")
                values.append(span["content"])
            content = "".join(values)
        if not isinstance(content, str):
            _fail("MINERU_CONTENT_UNSUPPORTED")
        if content:
            if text_parts:
                text_parts.append("\n\n")
                position += 2
            start = position
            text_parts.append(content)
            position += len(content)
            proof.update(derived_block_start=start, derived_block_end=position)
            locators.append(SourceLocator(kind="table" if kind == "table_body" else
                ("pdf" if Path(request.filename).suffix.lower() == ".pdf" else "image"),
                page=page_idx + 1, start=start, end=position, bbox=tuple(bbox) if bbox else None,
                raw_text=content, quote=content, raw_evidence=proof,
                asset_id=(proof.get("asset") or {}).get("logical_path"),
                source_format=Path(request.filename).suffix.lstrip("."),
                parse_status="complete" if full else "partial"))
        # Empty image/chart bodies stay empty; the saved crop is still evidence.
        block_manifest.append(proof)

    for page_position, page in enumerate(pages):
        blocks = page.get("blocks")
        if not isinstance(blocks, list):
            _fail("MINERU_BLOCKS_INVALID")
        for block_position, block in enumerate(blocks):
            if not isinstance(block, dict):
                _fail("MINERU_BLOCK_INVALID")
            emit(block, f"/pages/{page_position}/blocks/{block_position}", page["page_idx"])
    text = "".join(text_parts)
    identity["derived_text_sha256"] = _sha(text.encode("utf-8"))
    assets = [replace(asset, page_number=asset_locations[asset.path][0]["page_idx"] + 1,
                      metadata={**asset.metadata, "locations": asset_locations[asset.path]})
              if asset.path in asset_locations else asset for asset in assets]
    normalized = NormalizedDocument(document_id=request.document_id, version_id=request.source_version,
        title=request.filename, media_type=mimetypes.guess_type(request.filename)[0] or "application/octet-stream",
        markdown_content=text, content_sha256=identity["derived_text_sha256"], parser_version=VERSION, parser_engine=ENGINE,
        source_mapping_available=bool(locators), source_locators=locators,
        assets=[DocumentAsset(asset_id=asset.path, asset_type="source_image", storage_key=asset.path,
                    source_bytes=asset.data, source_locator=dict(asset.metadata), page_no=asset.page_number)
                for asset in assets if asset.media_type.startswith("image/")],
        conversion_lineage={"middle_sha256": middle_ref["sha256"],
                            "derived_text_sha256": identity["derived_text_sha256"],
                            "source_sha256": digest, "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
                            "producer_name": "mineru", "producer_version": VERSION,
                            "offset_space": "derived_unicode_codepoints"},
        parse_status="complete" if full else "partial",
        parse_warnings=() if full else ("MINERU_PARTIAL_PAGES",))
    parsed = ParsedDocument(normalized.markdown_content, tuple(assets),
        {"normalization_model": "NormalizedDocument", "mineru_source": identity,
         "source_locators": [locator.model_dump(mode="json") for locator in normalized.source_locators],
         "mineru_blocks": block_manifest, "asset_processing": "parser_complete"})
    return MinerUNormalization(normalized, parsed)


class LocalMinerUParser:
    def __init__(self, settings: MinerUSettings):
        self.settings = settings

    def __call__(self, request: DocumentRequest) -> ParsedDocument:
        try:
            count = inspect_source(request)
            settings = self.settings
            if not settings.python.is_file() or not settings.model_root.is_dir():
                _fail("MINERU_LOCAL_RUNTIME_MISSING")
            settings.temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="mineru-", dir=settings.temp_root) as folder:
                work = Path(folder)
                source = work / ("source" + Path(request.filename).suffix.lower())
                source.write_bytes(request.data)
                command = [str(settings.python), "-I", "-B", str(Path(__file__).with_name("mineru_runner.py")),
                           str(source), str(work / "result.zip"), str(work / "receipt.json")]
                process = subprocess.run(command, cwd=work, env=local_environment(settings, work),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=settings.timeout_seconds, check=False)
                if process.returncode:
                    _fail("MINERU_LOCAL_PROCESS_FAILED")
                receipt = json.loads((work / "receipt.json").read_text(encoding="utf-8"))
                receipt["source_page_count"] = count
                return normalize_archive(request, (work / "result.zip").read_bytes(), receipt).parsed
        except Exception as exc:
            code = str(exc) if isinstance(exc, MinerUError) else (
                "MINERU_TIMEOUT" if isinstance(exc, subprocess.TimeoutExpired) else "MINERU_PARSE_FAILED")
            return ParsedDocument("", metadata={"parser_engine": ENGINE},
                failures=(ParseFailure("parse", code, type(exc).__name__, code),))


def register_mineru(registry: ParserRegistry, settings: MinerUSettings, *, default: bool = False) -> None:
    registry.register(ENGINE, EXTENSIONS, LocalMinerUParser(settings))
    if default:
        # Keep explicit scan-only behavior available to saved diagnostics while
        # normal configured uploads use MinerU for images and scan-only PDFs.
        for extension in EXTENSIONS:
            if extension != '.pdf':
                registry._defaults[extension] = ENGINE
        registry.scanned_pdf_engine = ENGINE


def pending_image_assets(assets: tuple[ParsedAsset, ...]) -> tuple[ParsedAsset, ...]:
    """Legacy assets retain their current behavior; local parser evidence is saved only."""
    return tuple(asset for asset in assets if asset.metadata.get("processing_role") != EVIDENCE_ROLE)


def chunk_locators(parsed: ParsedDocument, start: int, end: int) -> dict[str, Any]:
    """Annotate the existing splitter's output, without changing its chunk strategy."""
    identity = parsed.metadata.get("mineru_source")
    if not isinstance(identity, Mapping):
        return {}
    if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(parsed.content)
            or identity.get("derived_text_sha256") != _sha(parsed.content.encode("utf-8"))):
        _fail("MINERU_CHUNK_RANGE_INVALID")
    intersections = []
    for locator in parsed.metadata.get("source_locators", ()):
        a, b = locator["start"], locator["end"]
        if not 0 <= a <= b <= len(parsed.content) or locator["quote"] != parsed.content[a:b]:
            _fail("MINERU_LOCATOR_RANGE_INVALID")
        left, right = max(start, a), min(end, b)
        if left < right:
            projected = dict(locator)
            projected.update(start=left, end=right, quote=parsed.content[left:right], raw_text=parsed.content[left:right])
            intersections.append(projected)
    pages = sorted({locator["page"] for locator in intersections})
    return {"mineru_source": dict(identity), "source_locators": intersections,
            "page_numbers": pages, "page_number": pages[0] if len(pages) == 1 else None}


def public_chunk_locators(metadata: Mapping[str, Any], document_metadata: Mapping[str, Any],
                          document_id: str, version: str, start: int, end: int,
                          text: str) -> dict[str, Any]:
    """Validate persisted positions and immutable manifest references on readback."""
    identity = metadata["mineru_source"]
    saved = document_metadata.get("parser_metadata", {}).get("mineru_source")
    if (not isinstance(identity, dict) or identity != saved
            or identity.get("source_sha256") != document_metadata.get("sha256")
            or identity.get("document_id") != document_id or identity.get("source_version") != version
            or identity.get("offset_space") != "derived_unicode_codepoints"
            or type(start) is not int or type(end) is not int or not 0 <= start <= end
            or len(text) != end - start):
        raise LookupError("SOURCE_CHUNK_UNAVAILABLE")
    manifest = {item.get("logical_path"): item.get("sha256")
                for item in document_metadata.get("assets", ()) if isinstance(item, dict)}

    def reference(value):
        if (not isinstance(value, dict) or set(value) != {"logical_path", "sha256"}
                or manifest.get(value.get("logical_path")) != value.get("sha256")):
            raise LookupError("SOURCE_CHUNK_UNAVAILABLE")
        safe_asset_path(value["logical_path"])
        return dict(value)

    middle = reference(identity.get("middle"))
    expected = []
    for original in document_metadata.get("parser_metadata", {}).get("source_locators", ()):
        a, b, quote = original.get("start"), original.get("end"), original.get("quote")
        if (type(a) is not int or type(b) is not int or not 0 <= a <= b
                or not isinstance(quote, str) or len(quote) != b - a):
            raise LookupError("SOURCE_CHUNK_UNAVAILABLE")
        left, right = max(start, a), min(end, b)
        if left < right:
            clipped = dict(original)
            clipped.update(start=left, end=right, quote=quote[left-a:right-a], raw_text=quote[left-a:right-a])
            expected.append(clipped)
    if metadata.get("source_locators") != expected:
        raise LookupError("SOURCE_CHUNK_UNAVAILABLE")
    public = []
    for locator in metadata.get("source_locators", ()):
        a, b = locator.get("start"), locator.get("end")
        proof = locator.get("raw_evidence", {})
        page_idx = proof.get("page_idx")
        if (type(a) is not int or type(b) is not int or not start <= a < b <= end
                or locator.get("quote") != text[a-start:b-start]
                or type(page_idx) is not int or page_idx not in identity.get("page_indices", ())
                or locator.get("page") != page_idx + 1
                or proof.get("source_sha256") != identity["source_sha256"]
                or proof.get("middle") != middle
                or not isinstance(proof.get("block_path"), str)
                or not re.fullmatch(r"/pages/\d+/blocks/\d+(?:/content/\d+)*", proof["block_path"])):
            raise LookupError("SOURCE_CHUNK_UNAVAILABLE")
        clean_proof = {key: proof[key] for key in ("page_idx", "block_index", "block_path", "block_type",
            "bbox", "coordinate_space", "page_dimensions", "offset_space", "source_sha256") if key in proof}
        clean_proof["middle"] = middle
        if "asset" in proof:
            clean_proof["asset"] = reference(proof["asset"])
        public.append({key: locator[key] for key in ("kind", "page", "start", "end", "bbox", "quote")
                       if key in locator} | {"raw_evidence": clean_proof})
    return {"source_locators": public, "parser_evidence": {
        key: identity[key] for key in ("source_sha256", "parser_engine", "parser_version", "schema",
            "schema_version", "offset_space", "derived_text_sha256", "middle")}}
