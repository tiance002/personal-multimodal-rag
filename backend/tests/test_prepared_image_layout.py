"""Real local image producer/consumer; v2 compatibility fixture is SIMULATED.

No database, model, network, admission or provider calls.
"""
from dataclasses import replace
from importlib.metadata import version
import io
from pathlib import Path
import uuid

import fitz
import pytest

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.adaptive_chunking import ChunkingConfig, prepare_document
from backend.app.domain.parsers import ParserError
from backend.app.domain.version_source import VersionSource
from backend.app.ports.ingestion import parse_version_source
from backend.app.ports.prepared_ingestion import (
    PreparedIngestionInput, PRODUCER_LIBRARIES, PRODUCER_MODULES,
    producer_module_hashes, validate_prepared_input,
)
from backend.tests.test_prepared_ingestion import encode, mutate, sha


@pytest.fixture(scope="module")
def image_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp("image-layout-consumer")
    image = root / "original.png"
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=200)
        page.insert_text((30, 60), "Alpha Evidence", fontsize=24)
        page.insert_text((30, 120), "Beta Evidence", fontsize=24)
        page.get_pixmap(matrix=fitz.Matrix(2, 2)).save(image)
    storage = ContentAddressedStorage(root / "cas")
    stored = storage.put_stream(io.BytesIO(image.read_bytes()))
    source = VersionSource(str(uuid.uuid4()), str(uuid.uuid4()), 1, stored.sha256,
                           stored.storage_key, "original.png", "image/png", stored.size)
    draft = replace(source, document_id="draft:document", version_id="draft:version")
    document = parse_version_source(
        ParserRegistry(tessdata=Path("var/tessdata"), collect_caption_geometry=True), storage, draft)
    config = ChunkingConfig()
    prepared = prepare_document(document, config)
    raw = encode(document.model_dump(mode="json"))
    chunks = encode(dict(status="OFFLINE_DRAFT_NOT_FORMAL_INDEX", document_id=document.document_id,
        version_id=document.version_id, source_sha256=source.source_sha256,
        normalized_content_sha256=document.content_sha256, chunker_config_identity=config.identity,
        parents=[p.model_dump(mode="json") for p in prepared.parents],
        children=[c.model_dump(mode="json") for c in prepared.children], diagnostics=prepared.diagnostics))
    assets, attachments = [], []
    for asset in document.assets:
        item = asset.model_dump(mode="json")
        if asset.source_bytes is not None:
            item["source_bytes_artifact"] = dict(path="SIMULATED descriptive path only",
                bytes=len(asset.source_bytes), sha256=sha(asset.source_bytes))
            attachments.append((sha(asset.source_bytes), asset.source_bytes))
        assets.append(item)
    assets = encode(assets)
    bundle = PreparedIngestionInput(source.source_sha256, source.original_size, source.file_name,
        source.media_type, raw, sha(raw), chunks, sha(chunks), assets, sha(assets),
        document.parser_version, document.parser_engine, producer_module_hashes(),
        tuple((name, version(name)) for name in PRODUCER_LIBRARIES), tuple(attachments))
    return storage, source, document, prepared, bundle


def test_real_v3_image_layout_survives_normal_consumer(image_bundle):
    storage, source, original, prepared, bundle = image_bundle
    bound, chunks = validate_prepared_input(bundle, storage, source, ChunkingConfig())
    assert bound.parser_version == "image/v3-ocr-layout"
    assert bound.document_id == source.document_id and bound.version_id == source.version_id
    assert bound.markdown_content == original.markdown_content
    assert bound.source_locators[0].raw_evidence == original.source_locators[0].raw_evidence
    assert bound.source_locators[0].quote == bound.markdown_content
    assert chunks.parents == prepared.parents and chunks.children == prepared.children
    assert bound.assets[0].source_bytes == original.assets[0].source_bytes


def test_simulated_v2_semantics_remain_with_matching_current_pins(image_bundle):
    storage, source, _, _, bundle = image_bundle
    # Explicit compatibility fixture, never evidence that old producer pins pass.
    bundle = mutate(bundle, "normalized", lambda value: value.update(parser_version="image/v2-ocr"))
    bundle = replace(bundle, parser_version="image/v2-ocr")
    bound, _ = validate_prepared_input(bundle, storage, source, ChunkingConfig())
    assert bound.parser_version == "image/v2-ocr"


@pytest.mark.parametrize("case", ["missing", "changed", "duplicate"])
def test_helper_pin_is_required_and_exact(image_bundle, case):
    storage, source, _, _, bundle = image_bundle
    helper = "adapters/parsers/image_ocr_layout.py"
    assert helper in PRODUCER_MODULES
    hashes = bundle.module_hashes
    if case == "missing": hashes = tuple(item for item in hashes if item[0] != helper)
    if case == "changed": hashes = tuple((name, "0" * 64 if name == helper else value) for name, value in hashes)
    if case == "duplicate": hashes = hashes + (next(item for item in hashes if item[0] == helper),)
    with pytest.raises(ParserError, match="PREPARED_MODULE_MISMATCH"):
        validate_prepared_input(replace(bundle, module_hashes=hashes), storage, source, ChunkingConfig())


def test_unknown_image_version_and_wrong_quote_still_rejected(image_bundle):
    storage, source, _, _, bundle = image_bundle
    unknown = mutate(bundle, "normalized", lambda value: value.update(parser_version="image/v999"))
    with pytest.raises(ParserError, match="PREPARED_PARSER_MISMATCH"):
        validate_prepared_input(replace(unknown, parser_version="image/v999"), storage, source, ChunkingConfig())
    bad = mutate(bundle, "chunks", lambda value: value["children"][0]["source_locator"].update(quote="Gold answer"))
    with pytest.raises(ParserError, match="PREPARED_"):
        validate_prepared_input(bad, storage, source, ChunkingConfig())
