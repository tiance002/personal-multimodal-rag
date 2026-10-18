"""Local parser evidence/selection contracts. No real provider or database calls."""
import asyncio
import copy
import hashlib
import io
import json
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image

from backend.app.domain.models import NormalizedDocument
from backend.weknora_port.ingestion.core import DocumentRequest, ParserRegistry, ingest_document
from backend.weknora_port.ingestion.mineru import (
    ENGINE, EXTENSIONS, LocalMinerUParser, MinerUError, MinerUSettings, chunk_locators,
    local_environment, normalize_archive, pending_image_assets, public_chunk_locators,
)


def source_pdf(pages=4, native=False):
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        if native:
            page.insert_text((30, 30), "Native PDF must use the existing default")
    data = document.tobytes()
    document.close()
    return data


def source_image():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, "PNG")
    return buffer.getvalue()


def middle(pages=(0,), full=True):
    return {"schema": "docvortex.middle", "schema_version": "2.0",
        "metadata": {"producer": {"name": "mineru", "version": "4.0.12"}},
        "extensions": {"mineru": {"tier": "basic", "parse_mode": "ocr"}},
        "is_full_document": full, "pages": [{"page_idx": index, "blocks": [
            {"type": "text", "index": 0, "bbox": [0.1, 0.1, 0.9, 0.2],
             "content": [{"type": "text", "content": "原文甲😀，重复原文甲。"}]},
            {"type": "table", "index": 1, "bbox": [0.1, 0.3, 0.9, 0.7], "content": [
                {"type": "table_body", "index": 1, "bbox": [0.1, 0.3, 0.9, 0.7],
                 "image_path": "images/table.jpg", "content": "<table><tr><td>实际12</td></tr></table>"}]},
            {"type": "chart", "index": 2, "bbox": [0.1, 0.75, 0.9, 0.95], "content": [
                {"type": "chart_body", "index": 2, "bbox": [0.1, 0.75, 0.9, 0.95],
                 "image_path": "images/chart.jpg", "content": ""}]},
        ]} for index in pages]}


def archive(raw, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("middle_json.json", json.dumps(raw, ensure_ascii=False).encode("utf-8"))
        zipped.writestr("images/table.jpg", b"table-exact-bytes")
        zipped.writestr("images/chart.jpg", b"chart-exact-bytes")
        for key, value in (extra or {}).items():
            zipped.writestr(key, value)
    return output.getvalue()


def request(image=True):
    return DocumentRequest("source.png" if image else "source.pdf", source_image() if image else source_pdf(),
        "owner", "kb", "document", "version", parser_engine=ENGINE,
        kb_chunking_config={"chunk_size": 32, "chunk_overlap": 5})


def convert(req=None, raw=None, **kwargs):
    req = req or request()
    return normalize_archive(req, archive(raw or middle()),
                             {"source_sha256": hashlib.sha256(req.data).hexdigest()}, **kwargs)


def registry(parsed):
    result = ParserRegistry()
    result.register(ENGINE, (".png", ".pdf"), lambda _: parsed)
    return result


def document_metadata(parsed):
    return {"sha256": parsed.metadata["mineru_source"]["source_sha256"],
        "source_version": "version", "parser_metadata": dict(parsed.metadata),
        "assets": [{"logical_path": asset.path, "sha256": hashlib.sha256(asset.data).hexdigest()}
                   for asset in parsed.assets]}


def test_normalized_model_bridge_nested_table_empty_chart_and_exact_sidecars():
    result = convert()
    assert isinstance(result.normalized, NormalizedDocument)
    assert result.parsed.content == result.normalized.markdown_content
    assert result.normalized.content_sha256 == hashlib.sha256(result.normalized.markdown_content.encode("utf-8")).hexdigest()
    assert result.normalized.content_sha256 != result.parsed.metadata["mineru_source"]["source_sha256"]
    assert "实际12" in result.parsed.content
    assert len(result.parsed.metadata["mineru_blocks"]) == 5
    assert not any(loc.raw_evidence["block_type"] == "chart_body" for loc in result.normalized.source_locators)
    table = result.normalized.source_locators[1]
    assert table.raw_evidence["block_path"] == "/pages/0/blocks/1/content/0"
    assert table.raw_evidence["coordinate_space"] == "normalized_0_1"
    assert table.raw_evidence["asset"]["logical_path"] == "mineru/images/table.jpg"
    assert any(asset.path == "mineru/images/chart.jpg" and asset.data == b"chart-exact-bytes"
               for asset in result.parsed.assets)
    assert pending_image_assets(result.parsed.assets) == ()
    assert all(result.parsed.content[loc.start:loc.end] == loc.quote for loc in result.normalized.source_locators)


def test_unchanged_splitter_metadata_intersections_and_parent_child_readback():
    req = request(False)
    converted = convert(req, middle((0, 3), False), allow_partial=True)
    assert converted.normalized.content_sha256 == hashlib.sha256(converted.normalized.markdown_content.encode("utf-8")).hexdigest()
    assert converted.normalized.content_sha256 != hashlib.sha256(req.data).hexdigest()
    req = replace(req, kb_chunking_config={"chunk_size": 512, "chunk_overlap": 0,
        "enable_parent_child": True, "parent_chunk_size": 512, "child_chunk_size": 35})
    result = ingest_document(req, registry(converted.parsed))
    baseline = ParserRegistry()
    baseline.register(ENGINE, (".pdf",), lambda _: replace(converted.parsed, metadata={}))
    unchanged = ingest_document(req, baseline)
    assert [(r.content, r.start, r.end, r.role) for r in result.chunks] == [
        (r.content, r.start, r.end, r.role) for r in unchanged.chunks]
    assert {p for row in result.chunks for p in row.metadata["page_numbers"]} == {1, 4}
    assert any(row.metadata["page_number"] is None for row in result.chunks)
    for row in result.chunks:
        assert row.content == converted.parsed.content[row.start:row.end]
        public = public_chunk_locators(row.metadata, document_metadata(converted.parsed), "document", "version",
                                      row.start, row.end, row.content)
        for locator in public["source_locators"]:
            assert row.start <= locator["start"] < locator["end"] <= row.end
            assert locator["quote"] == converted.parsed.content[locator["start"]:locator["end"]]


@pytest.mark.parametrize(("change", "code"), [
    (lambda raw: raw.update(schema="cli-result"), "SCHEMA_UNSUPPORTED"),
    (lambda raw: raw.update(schema_version="1.0"), "SCHEMA_UNSUPPORTED"),
    (lambda raw: raw["metadata"]["producer"].update(version="9"), "VERSION_UNSUPPORTED"),
    (lambda raw: raw["extensions"]["mineru"].update(parse_mode="txt"), "OCR_MODE_REQUIRED"),
    (lambda raw: raw["pages"][0].update(page_idx=-1), "PAGE_INDEX_INVALID"),
    (lambda raw: raw["pages"][0].update(page_idx=2), "PAGE_INDEX_INVALID"),
    (lambda raw: raw["pages"].append(raw["pages"][0]), "PAGE_INDEX_INVALID"),
    (lambda raw: raw["pages"][0]["blocks"][0].update(bbox=[0, 0, 999, 1]), "BBOX_INVALID"),
    (lambda raw: raw["pages"][0]["blocks"][1]["content"][0].update(image_path="../private.jpg"), "ASSET_PATH_INVALID"),
    (lambda raw: raw["pages"][0]["blocks"][1]["content"][0].update(image_path="images/missing.jpg"), "ASSET_MISSING"),
    (lambda raw: raw["pages"][0]["blocks"][1]["content"][0].update(image_url="https://example.invalid/image"), "EXTERNAL_ASSET_UNSUPPORTED"),
])
def test_fail_closed_middle(change, code):
    raw = middle()
    change(raw)
    with pytest.raises(MinerUError, match=code):
        convert(raw=raw)


def test_source_hash_partial_coverage_and_archive_path_fail_closed():
    req = request(False)
    with pytest.raises(MinerUError, match="SOURCE_HASH_MISMATCH"):
        normalize_archive(req, archive(middle()), {"source_sha256": "0" * 64})
    with pytest.raises(MinerUError, match="PARTIAL_DOCUMENT"):
        convert(req, middle((0, 3), False))
    with pytest.raises(MinerUError, match="PAGE_COVERAGE_INVALID"):
        convert(req, middle((0, 3), True))
    with pytest.raises(MinerUError, match="ASSET_PATH_INVALID"):
        normalize_archive(request(), archive(middle(), {"../escape.txt": b"no"}),
                          {"source_sha256": hashlib.sha256(request().data).hexdigest()})


def test_native_pdf_rejected_before_subprocess_and_no_parser_fallback(tmp_path, monkeypatch):
    settings = MinerUSettings(tmp_path / "python.exe", tmp_path, tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must not launch on native PDF"))
    req = replace(request(False), data=source_pdf(native=True))
    parsers = ParserRegistry()
    parsers.register(ENGINE, (".pdf",), LocalMinerUParser(settings))
    result = ingest_document(req, parsers)
    assert result.parser_engine == ENGINE and not result.chunks
    assert result.parsed.failures[0].code == "MINERU_NATIVE_PDF_UNSUPPORTED"
    unavailable = ingest_document(req)
    assert unavailable.parsed.failures and not unavailable.chunks
    assert "MINERU_EXPLICIT_PARSER_UNAVAILABLE" in unavailable.parsed.failures[0].message
    assert ParserRegistry().resolve("", "source.pdf") == "pdf"
    assert ParserRegistry().resolve("", "source.png") == "image"


def test_failed_local_process_does_not_use_default_and_environment_drops_credentials(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.touch()
    settings = MinerUSettings(python, tmp_path, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "DO-NOT-PASS")
    monkeypatch.setenv("MINERU_API_URL", "https://provider.invalid")
    env = local_environment(settings, tmp_path)
    assert "OPENAI_API_KEY" not in env and "MINERU_API_URL" not in env
    assert env["MINERU_MODEL_SOURCE"] == "local" and env["HF_HUB_OFFLINE"] == "1"
    def failed(command, **kwargs):
        assert command[1:3] == ["-I", "-B"]
        assert "DO-NOT-PASS" not in str(kwargs["env"])
        return subprocess.CompletedProcess(command, 1, b"", b"private parser error")
    monkeypatch.setattr(subprocess, "run", failed)
    parsed = LocalMinerUParser(settings)(request())
    assert parsed.failures[0].code == "MINERU_LOCAL_PROCESS_FAILED"
    assert "private" not in str(parsed.failures)


def test_network_guard_denies_including_loopback_and_config_requires_explicit_paths(monkeypatch):
    import socket
    from backend.weknora_port.ingestion.mineru_runner import deny_network
    original = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", original)
    monkeypatch.setattr(socket.socket, "connect_ex", socket.socket.connect_ex)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    deny_network()
    with socket.socket() as client:
        with pytest.raises(RuntimeError, match="MINERU_NETWORK_DENIED"):
            client.connect(("127.0.0.1", 1))
    assert MinerUSettings.from_environment({}) is None
    with pytest.raises(MinerUError, match="EXPLICIT_PATHS_REQUIRED"):
        MinerUSettings.from_environment({"WEKNORA_PORT_MINERU_ENABLED": "1"})


def test_development_env_enabled_mineru_has_supported_defaults(tmp_path):
    from test_development_entry import settings, development_environment
    from backend.weknora_port.development import DevelopmentSettings, DevelopmentConfigurationError
    env = development_environment(settings(tmp_path))
    assert DevelopmentSettings.from_environment(env).mineru_settings is None
    env.update(WEKNORA_PORT_MINERU_ENABLED="1", WEKNORA_PORT_MINERU_PYTHON=str(tmp_path / "python.exe"),
        WEKNORA_PORT_MINERU_MODEL_ROOT=str(tmp_path / "models"), WEKNORA_PORT_MINERU_TEMP_ROOT=str(tmp_path / "temp"))
    assert set(DevelopmentSettings.from_environment(env).mineru_extensions) == set(EXTENSIONS)
    env["WEKNORA_PORT_MINERU_EXTENSIONS"] = ".png,.pdf"
    config = DevelopmentSettings.from_environment(env)
    assert config.mineru_settings.python == tmp_path / "python.exe"
    assert config.mineru_extensions == (".png", ".pdf")
    env["WEKNORA_PORT_MINERU_EXTENSIONS"] = ".txt"
    with pytest.raises(DevelopmentConfigurationError, match="EXPLICIT_EXTENSIONS_REQUIRED"):
        DevelopmentSettings.from_environment(env)


@pytest.mark.parametrize("tamper", ["version", "document", "source", "middle", "range", "quote", "asset", "bbox", "page"])
def test_readback_rejects_cross_scope_hash_and_range_tampering(tamper):
    converted = convert()
    row = ingest_document(request(), registry(converted.parsed)).chunks[0]
    metadata = copy.deepcopy(dict(row.metadata))
    doc = document_metadata(converted.parsed)
    document_id, version = "document", "version"
    if tamper == "version": version = "other"
    elif tamper == "document": document_id = "other"
    elif tamper == "source": doc["sha256"] = "0" * 64
    elif tamper == "middle": doc["assets"][0]["sha256"] = "0" * 64
    elif tamper == "range": metadata["source_locators"][0]["start"] = row.start - 1
    elif tamper == "quote": metadata["source_locators"][0]["quote"] = "invented"
    elif tamper == "bbox": metadata["source_locators"][0]["bbox"] = [0, 0, 0.2, 0.2]
    elif tamper == "page": metadata["source_locators"][0]["page"] = 2
    elif tamper == "asset":
        metadata["source_locators"][0]["raw_evidence"]["asset"] = {
            "logical_path": "mineru/missing.jpg", "sha256": "0" * 64}
    with pytest.raises(LookupError, match="SOURCE_CHUNK_UNAVAILABLE"):
        public_chunk_locators(metadata, doc, document_id, version, row.start, row.end, row.content)


def test_bad_derived_text_or_locator_cannot_annotate_chunk():
    parsed = convert().parsed
    with pytest.raises(MinerUError, match="CHUNK_RANGE_INVALID"):
        chunk_locators(parsed, -1, 3)
    with pytest.raises(MinerUError, match="CHUNK_RANGE_INVALID"):
        chunk_locators(replace(parsed, content="tampered"), 0, 3)
    metadata = copy.deepcopy(dict(parsed.metadata))
    metadata["source_locators"][0]["quote"] = "not source"
    with pytest.raises(MinerUError, match="LOCATOR_RANGE_INVALID"):
        chunk_locators(replace(parsed, metadata=metadata), 0, 3)


def test_explicit_mineru_lifecycle_saves_assets_without_image_queue_or_processor(tmp_path, monkeypatch):
    from test_documents import (Factory, PRINCIPAL, FakeChunks, FakeIndex, fixture_settings)
    import backend.weknora_port.documents as documents
    from backend.weknora_port.documents import DocumentLifecycleService
    converted = convert()
    async def post(*args): return None
    async def no_calls(*args, **kwargs): pytest.fail("legacy OCR/caption must not resolve or execute")
    class Queue:
        create = no_calls
        start = no_calls
    class Processor:
        process_plan = no_calls
    async def exercise(multimodal):
        factory = Factory()
        config = replace(fixture_settings(PRINCIPAL), image_processing_config={"enable_multimodal": multimodal})
        service = DocumentLifecycleService(factory, tmp_path / str(multimodal), principal=PRINCIPAL,
            resolve_kb_settings=lambda _: config, parser_registry=registry(converted.parsed),
            parser_overrides={".png": ENGINE}, asset_processor=Processor(), post_processor=post)
        service.image_tasks = Queue()
        monkeypatch.setattr(documents, "ScopedKnowledgeRepository", lambda repo, resolve_tenant: FakeChunks(repo, resolve_tenant))
        monkeypatch.setattr(documents, "HybridIndexer", FakeIndex)
        kb = await service.create_knowledge_base(PRINCIPAL, "local mineru")
        # Parser fixture must receive actual server document/version identities.
        def callback(req): return convert(req).parsed
        service.parser_registry.register(ENGINE, (".png",), callback)
        result = await service.upload_document(PRINCIPAL, kb["id"], "source.png", "image/png", source_image())
        assert result["status"] == "completed", result
        saved = factory.connection.docs[result["id"]]
        assert len(saved["metadata"]["assets"]) == len(converted.parsed.assets)
        for chunk_id in factory.connection.chunks:
            source = await service.read_source_chunk(PRINCIPAL, chunk_id)
            assert source["parser_evidence"]["parser_engine"] == ENGINE
            assert "storage" not in json.dumps(source["source_locators"])
    asyncio.run(exercise(True))
    asyncio.run(exercise(False))


def test_default_image_still_enters_existing_image_task_queue(tmp_path):
    from test_documents import Factory, PRINCIPAL, fixture_settings
    from backend.weknora_port.documents import DocumentLifecycleService
    calls = []
    class Queue:
        async def create(self, *args, **kwargs): calls.append("create")
        async def start(self): calls.append("start")
    async def exercise():
        config = replace(fixture_settings(PRINCIPAL), image_processing_config={"enable_multimodal": True})
        service = DocumentLifecycleService(Factory(), tmp_path, principal=PRINCIPAL,
            resolve_kb_settings=lambda _: config)
        service.image_tasks = Queue()
        kb = await service.create_knowledge_base(PRINCIPAL, "default")
        await service.upload_document(PRINCIPAL, kb["id"], "default.png", "image/png", source_image())
        assert calls == ["create", "start"]
    asyncio.run(exercise())
