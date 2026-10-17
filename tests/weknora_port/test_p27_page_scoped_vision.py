"""Offline contracts for P27 physical-page-scoped image ingestion."""
import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image
from pydantic import ValidationError

from backend.weknora_port.http_api import ImageProcessingWrite
from backend.weknora_port.documents import DocumentLifecycleError
from backend.weknora_port.image_configuration import (
    effective_image_config,
    normalize_image_pages,
    select_image_assets,
)
from backend.weknora_port.image_tasks import ImageTaskQueue, page_scope_snapshot_valid
from backend.weknora_port.ingestion.core import ParsedAsset
from backend.weknora_port.ingestion.upstream_pdf_parser import PDFPageScopeError, PDFParser
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.models import ChatOptions, Message, build_chat_body
from backend.weknora_port.storage.configuration import ConfiguredRole


def _image_pdf(*, pages=3, scanned_page=None):
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
                    f"Native text page {number}, line {line}, source evidence for page scoped rendering.")
    raw = doc.tobytes()
    doc.close()
    return raw


def test_requested_native_pdf_page_is_rendered_with_source_identity_and_full_text():
    parsed = PDFParser(file_name="synthetic.pdf", image_pages=(2,)).parse_into_text(_image_pdf())
    assert "Native text page 1" in parsed.content
    assert "Native text page 2" in parsed.content
    assert "Native text page 3" in parsed.content
    assert parsed.metadata["requested_image_pages"] == [2]
    assert parsed.metadata["requested_rendered_pages"] == [2]
    assert len(parsed.images) == 1
    path = next(iter(parsed.images))
    assert path.endswith("_p2_requested_render.jpg")
    assert f"]({path})" in parsed.content


def test_requested_scanned_page_uses_existing_scanned_asset_reference():
    parsed = PDFParser(file_name="scan.pdf", image_pages=(2,)).parse_into_text(
        _image_pdf(pages=3, scanned_page=2))
    target_paths = [path for path in parsed.images if "_page_2.jpg" in path]
    assert len(target_paths) == 1
    assert f"]({target_paths[0]})" in parsed.content
    assert "_p2_requested_render.jpg" not in parsed.content
    target = ParsedAsset(target_paths[0], b"jpeg", "image/jpeg", "scanned_pdf", 2, {})
    distractor = ParsedAsset("images/scan_page_1.jpg", b"jpeg", "image/jpeg", "scanned_pdf", 1, {})
    assert select_image_assets((distractor, target), [2]) == (target,)
    with pytest.raises(DocumentLifecycleError, match="IMAGE_PAGE_ASSET_MISSING"):
        select_image_assets((distractor,), [2])


def test_page_scope_rejects_out_of_range_without_full_document_fallback():
    with pytest.raises(PDFPageScopeError, match="PDF_IMAGE_PAGE_OUT_OF_RANGE"):
        PDFParser(file_name="synthetic.pdf", image_pages=(4,)).parse_into_text(_image_pdf(pages=3))


def test_page_configuration_validates_positive_unique_bounded_physical_pages():
    assert normalize_image_pages([9, 2]) == [2, 9]
    assert ImageProcessingWrite(image_pages=[9, 2]).image_pages == [2, 9]
    for value in ([0], [-1], [1, 1], [True], list(range(1, 1002))):
        with pytest.raises(ValidationError):
            ImageProcessingWrite(image_pages=value)


def test_upload_page_scope_precedes_kb_default_and_empty_does_not_clear_default():
    kb = {"image_processing_config": {"enable_multimodal": True, "image_pages": [10]}}
    assert effective_image_config(kb)["image_pages"] == [10]
    assert effective_image_config(kb, {"image_pages": None})["image_pages"] == [10]
    assert effective_image_config(kb, {"image_pages": [9]})["image_pages"] == [9]


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
    def __init__(self):
        self.snapshot = None

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, args=()):
        if sql.startswith("INSERT INTO port_image_tasks"):
            self.snapshot = json.loads(args[-1])
            return _Rows([{"id": args[0]}])
        if sql.startswith("UPDATE knowledges"):
            return _Rows([{"id": args[0]}])
        raise AssertionError("unexpected SQL in offline task snapshot fixture")


class _Guard:
    def __init__(self):
        self.connection = _Connection()

    def require_verified(self):
        return None


class _Lease:
    def __init__(self, guard):
        self.guard = guard

    @asynccontextmanager
    async def lease(self, **kwargs):
        assert kwargs.get("writable_knowledge_base_ids") == frozenset({"kb-a"})
        yield self.guard


class _ModelStore:
    def __init__(self, role):
        self.role = role

    async def resolve(self, model_id):
        assert model_id == "deepseek-config-id"
        return self.role


def test_image_task_creation_persists_page_scope_and_deepseek_caption_identity():
    async def scenario():
        principal = SimpleNamespace(tenant_id=17, subject_id="owner-a")
        guard = _Guard()
        role = ConfiguredRole("vlm", "deepseek", "deepseek-flash",
                              "https://api.deepseek.com/v1")
        queue = ImageTaskQueue(_Lease(guard), principal, None,
                               _ModelStore(role), SourceCatalogue())
        kb = {"id": "kb-a", "vlm_config": {}, "image_processing_config": {
            "enable_multimodal": True, "ocr_adapter": "tesseract",
            "caption_mode": "explicit", "caption_model_id": "deepseek-config-id"}}
        task_id = await queue.create(principal, kb, "doc-a", "v3",
            {"image_pages": [9]}, source_sha256="ab" * 32)
        assert task_id and guard.connection.snapshot is not None
        snapshot = guard.connection.snapshot
        assert snapshot["override"]["image_pages"] == [9]
        assert snapshot["selection"]["ocr"]["adapter"] == "tesseract"
        assert snapshot["selection"]["caption"]["model_id"] == "deepseek-config-id"
        assert snapshot["page_scope"] == {
            "tenant_id": 17, "subject_id": "owner-a", "knowledge_base_id": "kb-a",
            "document_id": "doc-a", "source_version": "v3", "source_sha256": "ab" * 32,
            "physical_pages": [9]}
    import asyncio
    asyncio.run(scenario())


def test_task_page_snapshot_binds_document_version_tenant_and_source_hash():
    row = {"tenant_id": 17, "subject_id": "owner-a", "knowledge_base_id": "kb-a",
           "knowledge_id": "doc-a", "source_version": "v3"}
    digest = "ab" * 32
    override = {"image_pages": [9]}
    scope = {"tenant_id": 17, "subject_id": "owner-a", "knowledge_base_id": "kb-a",
             "document_id": "doc-a", "source_version": "v3", "physical_pages": [9],
             "source_sha256": digest}
    assert page_scope_snapshot_valid(scope, row, override)
    for key, value in (("subject_id", "owner-b"), ("knowledge_id", "doc-b"),
                       ("source_version", "v4"), ("tenant_id", 18)):
        changed = dict(row, **{key: value})
        assert not page_scope_snapshot_valid(scope, changed, override)
    assert not page_scope_snapshot_valid(dict(scope, source_sha256=""), row, override)
    assert not page_scope_snapshot_valid(scope, row, {"image_pages": [8]})


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
