"""Opt-in reuse of independently pinned source-parse artifacts.

Pins must come from an approved manifest, not from the bytes being checked.
This is an internal ingestion port, not a JSON upload API or a cache lookup.
Transport/path selection belongs to the caller. No Gold or answer artifact is
accepted: the bundle is bound to the uploaded original's CAS identity.
"""
from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path

from pydantic import ValidationError

from backend.app.domain.adaptive_chunking import (
    CHUNKER_VERSION, SCHEMA_VERSION, ChunkingConfig, PreparedChunks,
)
from backend.app.domain.models import ChunkDraft, NormalizedDocument
from backend.app.domain.parsers import ParserError
from backend.app.domain.version_source import VersionSource


# Only producer behavior modules are pinned. The new port and repository wiring
# are consumers and deliberately do not claim the old producer's file identity.
PRODUCER_MODULES = (
    "adapters/parsers/__init__.py", "adapters/parsers/doc.py",
    "adapters/parsers/image_ocr_layout.py",
    "adapters/parsers/native.py", "adapters/parsers/pdf_tables.py",
    "adapters/parsers/xls.py", "adapters/parsers/xlsx.py",
    "domain/adaptive_chunking.py", "domain/chunking.py", "domain/models.py",
    "domain/parsers.py", "domain/table_evidence.py",
)
PRODUCER_LIBRARIES = ("PyMuPDF", "Pillow", "pydantic")


def producer_module_hashes() -> tuple[tuple[str, str], ...]:
    root = Path(__file__).resolve().parents[1]
    return tuple((name, hashlib.sha256((root / name).read_bytes()).hexdigest())
                 for name in PRODUCER_MODULES)


@dataclass(frozen=True)
class PreparedIngestionInput:
    """Immutable bytes plus independent frozen producer/source pins.

    Attachments are (sha256, bytes), never a foreign CAS key or filesystem path.
    On resume reconstruct exactly these bytes and pins; UNKNOWN provider claims
    still go through the original process_job controls.
    """
    source_sha256: str
    original_size: int
    file_name: str
    media_type: str
    normalized_json: bytes
    normalized_sha256: str
    chunks_json: bytes
    chunks_sha256: str
    assets_json: bytes
    assets_sha256: str
    parser_version: str
    parser_engine: str
    module_hashes: tuple[tuple[str, str], ...]
    library_versions: tuple[tuple[str, str], ...]
    attachments: tuple[tuple[str, bytes], ...] = ()
    chunker_version: str = CHUNKER_VERSION
    locator_schema_version: str = SCHEMA_VERSION
    purpose: str = "source_parse"


def _require(condition, code):
    if not condition:
        raise ParserError("PREPARED_" + code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(data, expected):
    _require(_sha(data) == expected, "ARTIFACT_HASH_MISMATCH")
    return json.loads(data)


def validate_prepared_input(bundle: PreparedIngestionInput, storage,
                            source: VersionSource, config: ChunkingConfig
                            ) -> tuple[NormalizedDocument, PreparedChunks]:
    """Validate completely before asset writes or embedding; bind formal IDs."""
    try:
        return _validate(bundle, storage, source, config)
    except ParserError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError) as exc:
        raise ParserError("PREPARED_SCHEMA_INVALID") from exc


def _validate(bundle, storage, source, config):
    _require(isinstance(bundle, PreparedIngestionInput), "INPUT_TYPE_INVALID")
    _require(source.metadata_known and source.original_size is not None, "SOURCE_METADATA_UNKNOWN")
    _require(bundle.purpose == "source_parse", "PURPOSE_INVALID")
    _require((bundle.source_sha256, bundle.original_size, bundle.file_name, bundle.media_type) ==
             (source.source_sha256, source.original_size, source.file_name, source.media_type),
             "SOURCE_IDENTITY_MISMATCH")
    original = storage.path_for(source.storage_key)
    digest = hashlib.sha256()
    with original.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    _require(original.stat().st_size == source.original_size and
             digest.hexdigest() == source.source_sha256, "SOURCE_HASH_MISMATCH")
    _require(bundle.chunker_version == CHUNKER_VERSION and
             bundle.locator_schema_version == SCHEMA_VERSION, "VERSION_MISMATCH")
    _require(dict(bundle.module_hashes) == dict(producer_module_hashes()) and
             len(bundle.module_hashes) == len(PRODUCER_MODULES), "MODULE_MISMATCH")
    _require(dict(bundle.library_versions) == {name: version(name) for name in PRODUCER_LIBRARIES}
             and len(bundle.library_versions) == len(PRODUCER_LIBRARIES), "LIBRARY_MISMATCH")
    raw = _json(bundle.normalized_json, bundle.normalized_sha256)
    _require(set(raw) == set(NormalizedDocument.model_fields) - {"source_bytes"}, "NORMALIZED_SCHEMA_INVALID")
    document = NormalizedDocument.model_validate(raw)
    # The pinned native ImageParser emits image/* for both JPEG and PNG.
    media_matches = document.media_type == source.media_type or (
        document.media_type == "image/*" and source.media_type.startswith("image/") and
        document.parser_version in {"image/v2-ocr", "image/v3-ocr-layout"} and
        document.parser_engine == "pymupdf/tesseract")
    _require((document.parser_version, document.parser_engine) ==
             (bundle.parser_version, bundle.parser_engine) and media_matches, "PARSER_MISMATCH")
    content = document.markdown_content
    _require(document.content_sha256 == _sha(content.encode("utf-8")), "CONTENT_HASH_MISMATCH")
    chunks = _json(bundle.chunks_json, bundle.chunks_sha256)
    _require(set(chunks) == {"status", "document_id", "version_id", "source_sha256",
             "normalized_content_sha256", "chunker_config_identity", "parents", "children", "diagnostics"},
             "CHUNK_SCHEMA_INVALID")
    _require(chunks["status"] == "OFFLINE_DRAFT_NOT_FORMAL_INDEX" and
             (chunks["document_id"], chunks["version_id"], chunks["source_sha256"],
              chunks["normalized_content_sha256"]) ==
             (document.document_id, document.version_id, source.source_sha256, document.content_sha256),
             "DRAFT_IDENTITY_MISMATCH")
    _require(chunks["chunker_config_identity"] == config.identity, "CONFIG_MISMATCH")
    assets = _json(bundle.assets_json, bundle.assets_sha256)
    _require(isinstance(assets, list) and len(assets) == len(document.assets), "ASSET_MANIFEST_MISMATCH")
    attached = dict(bundle.attachments)
    _require(len(attached) == len(bundle.attachments) and all(_sha(data) == sha for sha, data in attached.items()),
             "ATTACHMENT_HASH_MISMATCH")
    hydrated, used = [], set()
    asset_ids = {a.asset_id for a in document.assets}
    table_ids = {t.table_id for t in document.tables}
    _require(len(asset_ids) == len(document.assets) and len(table_ids) == len(document.tables), "DUPLICATE_PROVENANCE_ID")
    for asset, entry in zip(document.assets, assets):
        metadata = {key: value for key, value in entry.items() if key != "source_bytes_artifact"}
        _require(metadata == asset.model_dump(mode="json") and not asset.storage_key, "ASSET_MANIFEST_MISMATCH")
        _require(not asset.derived_from_asset_id or asset.derived_from_asset_id in asset_ids,
                 "ASSET_PROVENANCE_INVALID")
        _require(asset.derived_from_asset_id != asset.asset_id, "ASSET_PROVENANCE_INVALID")
        ref = entry.get("source_bytes_artifact")
        data = None
        if ref:
            # ref.path is descriptive provenance only, never opened or persisted.
            data = attached.get(ref["sha256"])
            _require(data is not None and len(data) == ref["bytes"], "ATTACHMENT_MISSING")
            _require(asset.source_locator.get("sha256", ref["sha256"]) == ref["sha256"], "ASSET_PROVENANCE_INVALID")
            used.add(ref["sha256"])
        _require(asset.asset_type not in {"source_image", "scanned_page"} or data is not None,
                 "ATTACHMENT_MISSING")
        _geometry(asset.source_locator)
        hydrated.append(asset.model_copy(update={"source_bytes": data}))
    _require(used == set(attached), "UNREFERENCED_ATTACHMENT")
    for item in document.sections + document.tables + document.blocks:
        _range(item.start, item.end, content)
        _require(not getattr(item, "asset_id", None) or item.asset_id in asset_ids, "ASSET_PROVENANCE_INVALID")
        _require(not getattr(item, "table_id", None) or item.table_id in table_ids, "TABLE_PROVENANCE_INVALID")
    for locator in document.source_locators:
        _locator(locator, content, asset_ids, table_ids)
    for proof in document.table_row_proofs:
        _require((proof.document_id, proof.version_id, proof.source_sha256) ==
                 (document.document_id, document.version_id, source.source_sha256) and
                 proof.table_id in table_ids, "TABLE_PROVENANCE_INVALID")
        table = next(t for t in document.tables if t.table_id == proof.table_id)
        _require(all(cell in table.cells for cell in proof.cells), "TABLE_PROVENANCE_INVALID")
    parents = [ChunkDraft.model_validate(value) for value in chunks["parents"]]
    children = [ChunkDraft.model_validate(value) for value in chunks["children"]]
    _require(bool(children), "EMPTY_CHILDREN")
    for drafts, parent in ((parents, True), (children, False)):
        for index, draft in enumerate(drafts):
            _range(draft.start, draft.end, content)
            _require(draft.chunk_index == index and draft.content == content[draft.start:draft.end] and
                     draft.content_sha256 == _sha(draft.content.encode("utf-8")), "CHUNK_READBACK_INVALID")
            _locator(draft.source_locator, content, asset_ids, table_ids)
            _require((draft.source_locator.start, draft.source_locator.end, draft.source_locator.quote) ==
                     (draft.start, draft.end, draft.content), "QUOTE_INVALID")
            _require((draft.chunk_type == "parent_text") == parent, "CHUNK_ROLE_INVALID")
            if parent:
                _require(draft.parent_index is None, "PARENT_INVALID")
            elif draft.parent_index is not None:
                _require(draft.parent_index < len(parents), "PARENT_INVALID")
                p = parents[draft.parent_index]
                _require(p.start <= draft.start < draft.end <= p.end, "PARENT_INVALID")
    covered = 0
    for draft in sorted(children, key=lambda c: (c.start, c.end)):
        _require(not content[covered:draft.start].strip(), "COVERAGE_INVALID")
        covered = max(covered, draft.end)
    _require(not content[covered:].strip(), "COVERAGE_INVALID")
    bound = document.model_copy(update={"document_id": source.document_id, "version_id": source.version_id,
        "assets": hydrated, "table_row_proofs": [p.model_copy(update={"document_id": source.document_id,
        "version_id": source.version_id}) for p in document.table_row_proofs]})
    return bound, PreparedChunks(parents, children, chunks["diagnostics"], config)


def _range(start, end, content):
    _require(0 <= start <= end <= len(content), "OFFSET_INVALID")


def _geometry(locator):
    basis = locator.get("coordinate_basis")
    _require(basis is None or basis in {"pdf-points-top-left-unrotated", "image-pixels-top-left"}, "GEOMETRY_VERSION_INVALID")
    for key in ("bbox", "table_bbox"):
        box = locator.get(key)
        if box is not None:
            _require(len(box) == 4 and all(math.isfinite(v) for v in box) and
                     box[0] <= box[2] and box[1] <= box[3], "GEOMETRY_INVALID")


def _locator(locator, content, assets, tables):
    _require(not locator.asset_id or locator.asset_id in assets, "ASSET_PROVENANCE_INVALID")
    _require(not locator.table_id or locator.table_id in tables, "TABLE_PROVENANCE_INVALID")
    if locator.start is not None or locator.end is not None:
        _require(locator.start is not None and locator.end is not None, "OFFSET_INVALID")
        _range(locator.start, locator.end, content)
        if locator.quote is not None:
            _require(locator.quote == content[locator.start:locator.end], "QUOTE_INVALID")
    _geometry(locator.model_dump())
    _geometry(locator.raw_evidence)
