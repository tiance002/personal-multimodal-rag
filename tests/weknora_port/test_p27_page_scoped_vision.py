"""P31 retires P27 page selection and restores full-document native routing.

The former selected-page expectations are intentionally replaced: P31 removes
that feature. Historical P27 run evidence remains unchanged outside this suite.
"""
import asyncio
import io
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image
from pydantic import ValidationError

from backend.weknora_port.http_api import ImageProcessingWrite
from backend.weknora_port.documents import DocumentLifecycleError, DocumentLifecycleService
from backend.weknora_port.image_configuration import effective_image_config
from backend.weknora_port.image_tasks import ImageTask, ImageTaskQueue
from backend.weknora_port.ingestion import DocumentRequest, parse_document
from backend.weknora_port.ingestion.upstream_pdf_parser import PDFParser
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.models import ChatOptions, Message, build_chat_body
from backend.weknora_port.storage.configuration import ConfiguredRole


def _image_pdf(*, pages=3, scanned_page=None, embedded_pages=()):
    doc = fitz.open()
    stream = io.BytesIO()
    Image.new("RGB", (360, 480), (220, 180, 120)).save(stream, format="PNG")
    image = stream.getvalue()
    for number in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        if number == scanned_page:
            page.insert_image(page.rect, stream=image)
        else:
            for line in range(12):
                page.insert_text((55, 60 + 28 * line),
                    f"Native text page {number}, line {line}, complete source evidence for native routing.")
            if number in embedded_pages:
                figure = io.BytesIO()
                Image.new("RGB", (360, 240), (number * 40, 100, 160)).save(figure, format="PNG")
                page.insert_image(fitz.Rect(100, 450, 400, 650), stream=figure.getvalue())
    raw = doc.tobytes()
    doc.close()
    return raw


def test_native_pdf_keeps_every_text_page_without_extra_page_rendering():
    parsed = PDFParser(file_name="synthetic.pdf").parse_into_text(_image_pdf())
    assert "Native text page 1" in parsed.content
    assert "Native text page 2" in parsed.content
    assert "Native text page 3" in parsed.content
    assert parsed.metadata["text_page_count"] == 3
    assert parsed.metadata["scanned_page_count"] == 0
    assert "requested_image_pages" not in parsed.metadata
    assert "requested_rendered_pages" not in parsed.metadata
    assert not parsed.images


def test_hybrid_pdf_routes_scanned_page_and_both_native_embedded_figures():
    _, parsed = parse_document(DocumentRequest("hybrid.pdf",
        _image_pdf(pages=3, scanned_page=2, embedded_pages=(1, 3))))
    assert not parsed.failures
    assert "Native text page 1" in parsed.content and "Native text page 3" in parsed.content
    assert parsed.metadata["text_page_count"] == 2
    assert parsed.metadata["scanned_page_count"] == 1
    assert parsed.metadata["embedded_image_count"] == 2
    assert {(asset.page_number, asset.source_type) for asset in parsed.assets} == {
        (1, "embedded_pdf_image"), (2, "scanned_pdf"), (3, "embedded_pdf_image")}
    assert all(f"]({asset.path})" in parsed.content for asset in parsed.assets)
    assert all("requested_render" not in asset.path for asset in parsed.assets)


def test_retired_parser_page_selector_is_explicitly_rejected():
    with pytest.raises(ValueError, match="IMAGE_PAGES_UNSUPPORTED"):
        PDFParser(file_name="synthetic.pdf", image_pages=(2,))
    with pytest.raises(TypeError, match="pdf_image_pages"):
        DocumentRequest("synthetic.pdf", b"fixture", pdf_image_pages=(2,))


def test_retired_page_configuration_is_a_forbidden_request_field():
    for value in (None, [9, 2], [], [0], [-1], [1, 1], [True]):
        with pytest.raises(ValidationError) as exc:
            ImageProcessingWrite(image_pages=value)
        assert exc.value.errors()[0]["type"] == "extra_forbidden"
    assert "image_pages" not in ImageProcessingWrite.model_json_schema()["properties"]


def test_historical_kb_page_selector_is_ignored_without_modifying_stored_config():
    kb = {"image_processing_config": {"enable_multimodal": True, "image_pages": [10]}}
    assert effective_image_config(kb) == {"enable_multimodal": True}
    assert kb["image_processing_config"]["image_pages"] == [10]
    for value in (None, [9]):
        with pytest.raises(DocumentLifecycleError, match="IMAGE_PAGES_UNSUPPORTED"):
            effective_image_config(kb, {"image_pages": value})


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Connection:
    def __init__(self, row=None):
        self.snapshot = None
        self.row = row
        self.queries = []

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, args=()):
        self.queries.append(sql)
        if sql.startswith("SELECT * FROM port_image_tasks"):
            return _Rows([self.row] if self.row else [])
        if sql.startswith("UPDATE port_image_tasks SET state='processing'"):
            return _Rows([])
        if sql.startswith("INSERT INTO port_image_tasks"):
            self.snapshot = json.loads(args[-1])
            return _Rows([{"id": args[0]}])
        if sql.startswith("UPDATE knowledges"):
            return _Rows([{"id": args[0]}])
        raise AssertionError("unexpected SQL in offline task snapshot fixture")


class _Guard:
    def __init__(self, row=None):
        self.connection = _Connection(row)

    def require_verified(self):
        return None


class _Lease:
    def __init__(self, guard):
        self.guard = guard

    @asynccontextmanager
    async def lease(self, **kwargs):
        assert not kwargs or kwargs.get("writable_knowledge_base_ids") == frozenset({"kb-a"})
        yield self.guard


class _ModelStore:
    def __init__(self, role):
        self.role = role

    async def resolve(self, model_id):
        assert model_id == "deepseek-config-id"
        return self.role


@pytest.mark.parametrize("source_sha256", ("", "ab" * 32))
def test_image_task_creation_keeps_caption_identity_without_physical_page_constraint(source_sha256):
    async def scenario():
        principal = SimpleNamespace(tenant_id=17, subject_id="owner-a")
        guard = _Guard()
        role = ConfiguredRole("vlm", "deepseek", "deepseek-flash",
                              "https://api.deepseek.com/v1")
        queue = ImageTaskQueue(_Lease(guard), principal, None,
                               _ModelStore(role), SourceCatalogue())
        kb = {"id": "kb-a", "vlm_config": {}, "image_processing_config": {
            "enable_multimodal": True, "ocr_adapter": "tesseract",
            "caption_mode": "explicit", "caption_model_id": "deepseek-config-id",
            "image_pages": [9]}}
        task_id = await queue.create(principal, kb, "doc-a", "v3", source_sha256=source_sha256)
        assert task_id and guard.connection.snapshot is not None
        snapshot = guard.connection.snapshot
        assert "image_pages" not in snapshot["override"]
        assert "page_scope" not in snapshot
        assert snapshot["selection"]["ocr"]["adapter"] == "tesseract"
        assert snapshot["selection"]["caption"]["model_id"] == "deepseek-config-id"
        assert snapshot["source_sha256"] == source_sha256
        assert kb["image_processing_config"]["image_pages"] == [9]
        with pytest.raises(DocumentLifecycleError, match="IMAGE_PAGES_UNSUPPORTED"):
            await queue.create(principal, kb, "other-doc", "v4", {"image_pages": [9]})
    asyncio.run(scenario())


def test_legacy_task_claim_ignores_page_constraint_and_preserves_historical_snapshot():
    async def scenario():
        principal = SimpleNamespace(tenant_id=17, subject_id="owner-a")
        selected = {"selection": {"ocr": {"adapter": "tesseract"}},
                    "override": {"enable_multimodal": True, "image_pages": [9]},
                    "page_scope": {"physical_pages": [8], "source_sha256": "ab" * 32}}
        row = {"id": "task-a", "tenant_id": 17, "subject_id": "owner-a",
               "knowledge_base_id": "kb-a", "knowledge_id": "doc-a", "source_version": "v3",
               "attempt": 0, "max_retry": 3, "outcomes": {}, "selected_config": selected}
        before = json.dumps(selected, sort_keys=True)
        guard = _Guard(row)
        task = await ImageTaskQueue(_Lease(guard), principal, None, None, None)._claim()
        assert task.config == {"enable_multimodal": True}
        assert task.source_version == "v3" and task.source_sha256 == "ab" * 32
        assert task.claim_token and task.selection == selected["selection"]
        assert json.dumps(selected, sort_keys=True) == before
        assert not any("orphaned" in query or "selected_config=" in query
                       for query in guard.connection.queries)
    asyncio.run(scenario())


def test_image_task_still_requires_current_owner_version_hash_and_claim():
    async def scenario():
        principal = SimpleNamespace(tenant_id=17, subject_id="owner-a")
        task = ImageTask("task-a", 17, "owner-a", "kb-a", "doc-a", "v3",
                         {}, {}, 1, "claim-a", {}, "ab" * 32)
        current = {"metadata": {"source_version": "v3"}, "file_hash": "ab" * 32,
                   "parse_status": "processing", "state": "processing", "claim_token": "claim-a"}
        service = object.__new__(DocumentLifecycleService)
        service.principal = principal

        async def rows(*args):
            return [current]

        service._rows = rows
        await service._assert_current_image_task(principal, task)
        for key, value in (("metadata", {"source_version": "v4"}), ("file_hash", "cd" * 32),
                           ("parse_status", "completed"), ("state", "cancelled"),
                           ("claim_token", "claim-b")):
            original = current[key]
            current[key] = value
            with pytest.raises(DocumentLifecycleError, match="IMAGE_SOURCE_VERSION_CHANGED"):
                await service._assert_current_image_task(principal, task)
            current[key] = original
        with pytest.raises(DocumentLifecycleError, match="IMAGE_TASK_OWNER_MISMATCH"):
            await service._assert_current_image_task(principal, replace(task, subject_id="owner-b"))
    asyncio.run(scenario())


def test_deepseek_flash_registry_and_image_request_wire_are_vision_capable():
    role = ConfiguredRole("vlm", "deepseek", "deepseek-flash", "https://api.deepseek.com/v1")
    config = SourceCatalogue().resolve(role, "deepseek-config-id")
    assert config.model == "deepseek-flash"
    assert config.supports_vision
    assert config.chat.supports_multi_content
    assert config.api == "openai-completions"
    body = build_chat_body(config,
        (Message("user", "Describe the chart", images=("data:image/jpeg;base64,QUJD",)),),
        ChatOptions(max_tokens=256))
    assert body["model"] == "deepseek-flash"
    content = body["messages"][0]["content"]
    assert {part["type"] for part in content} == {"image_url", "text"}
    assert next(part for part in content if part["type"] == "image_url")["image_url"]["url"] == \
        "data:image/jpeg;base64,QUJD"
    assert next(part for part in content if part["type"] == "text")["text"] == "Describe the chart"
