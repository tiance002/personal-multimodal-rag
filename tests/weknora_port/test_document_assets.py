"""Offline manifest-bound image readback through the ordinary source endpoint."""
import asyncio
import hashlib

import httpx
import pytest

from backend.weknora_port.documents import DocumentLifecycleError, DocumentLifecycleService
from backend.weknora_port.http_api import create_app
from backend.weknora_port.ingestion.core import ParsedAsset
from backend.weknora_port.runtime import ApplicationRuntime
from backend.weknora_port.storage.history import LocalPrincipal


OWNER = LocalPrincipal(81013, "source-asset-owner")
KB, DOC, VERSION = "kb-images", "document-pptx", "version-1"
LOGICAL_PATH = "images/source card.png"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"synthetic-image-fixture"


@pytest.fixture
def asset_service(tmp_path, monkeypatch):
    service = DocumentLifecycleService(object(), tmp_path / "storage", principal=OWNER,
                                       resolve_kb_settings=lambda _: None)
    manifest = service._persist_assets(OWNER, KB, DOC, VERSION,
        [ParsedAsset(LOGICAL_PATH, PNG, "image/png", "pptx_image", page_number=2)])
    source = service.storage_root / str(OWNER.tenant_id) / KB / DOC / VERSION / "original" / "source.pptx"
    service._write_immutable(source, b"original-pptx-source")
    row = {"metadata": {"source_version": VERSION, "assets": manifest}, "parse_status": "completed",
           "file_name": "source.pptx", "file_type": "pptx", "file_path": str(source),
           "file_size": source.stat().st_size,
           "file_hash": hashlib.sha256(source.read_bytes()).hexdigest()}

    async def rows(sql, params=(), **_kwargs):
        if sql.startswith("SELECT * FROM knowledge_bases"):
            return [{"id": KB, "tenant_id": OWNER.tenant_id}] if params == (KB, OWNER.tenant_id) else []
        assert sql.startswith(("SELECT metadata,parse_status FROM knowledges",
                               "SELECT file_name,file_type,file_path,file_hash,file_size FROM knowledges"))
        return [row] if params == (DOC, OWNER.tenant_id, KB) else []

    monkeypatch.setattr(service, "_rows", rows)
    return service, row


def read_asset(service, **overrides):
    args = {"principal": OWNER, "knowledge_base_id": KB, "document_id": DOC,
            "asset_path": LOGICAL_PATH, "source_version": VERSION, **overrides}
    return asyncio.run(service.read_document_asset(**args))


def test_manifest_asset_readback_retains_original_download(asset_service):
    service, _ = asset_service
    image = read_asset(service)
    assert (image.filename, image.media_type, image.content) == ("source card.png", "image/png", PNG)
    original = asyncio.run(service.read_document(OWNER, KB, DOC))
    assert original.filename == "source.pptx" and original.content == b"original-pptx-source"


@pytest.mark.parametrize("principal", [LocalPrincipal(81014, OWNER.subject_id),
                                       LocalPrincipal(OWNER.tenant_id, "other-subject")])
def test_manifest_asset_rejects_wrong_tenant_or_owner(asset_service, principal):
    with pytest.raises(PermissionError, match="LOCAL_PRINCIPAL_SCOPE_MISMATCH"):
        read_asset(asset_service[0], principal=principal)


@pytest.mark.parametrize("scope", [{"knowledge_base_id": "other-kb"}, {"document_id": "other-doc"}])
def test_manifest_asset_rejects_wrong_kb_or_document(asset_service, scope):
    with pytest.raises(LookupError):
        read_asset(asset_service[0], **scope)


@pytest.mark.parametrize("value", ["../source.png", "/images/source.png", "images/../source.png",
                                    "images//source.png", "images/./source.png", "images\\source.png",
                                    "C:/source.png", "https://example.test/source.png", "images/a\x00.png"])
def test_manifest_asset_rejects_noncanonical_caller_paths(asset_service, value):
    with pytest.raises(DocumentLifecycleError, match="DOCUMENT_ASSET_PATH_INVALID"):
        read_asset(asset_service[0], asset_path=value)


def test_manifest_asset_rejects_stale_version_and_unfinished_document(asset_service):
    service, row = asset_service
    with pytest.raises(LookupError, match="DOCUMENT_ASSET_UNAVAILABLE"):
        read_asset(service, source_version="old-version")
    row["parse_status"] = "processing"
    with pytest.raises(LookupError, match="DOCUMENT_ASSET_UNAVAILABLE"):
        read_asset(service)


def test_manifest_asset_never_falls_back_to_original_or_ambiguous_manifest(asset_service):
    service, row = asset_service
    with pytest.raises(LookupError, match="DOCUMENT_ASSET_NOT_FOUND"):
        read_asset(service, asset_path="images/unknown.png")
    asset = row["metadata"]["assets"][0]
    row["metadata"]["assets"] = [asset, dict(asset)]
    with pytest.raises(DocumentLifecycleError, match="DOCUMENT_ASSET_MANIFEST_AMBIGUOUS"):
        read_asset(service)
    row["metadata"].pop("assets")
    with pytest.raises(LookupError, match="DOCUMENT_ASSET_NOT_FOUND"):
        read_asset(service)


@pytest.mark.parametrize("change", [{"media_type": "image/svg+xml"}, {"media_type": "text/html"},
                                    {"sha256": ""}, {"sha256": "not-a-sha"}, {"size": -1}])
def test_manifest_asset_rejects_invalid_or_active_content_manifest(asset_service, change):
    service, row = asset_service
    row["metadata"]["assets"][0].update(change)
    with pytest.raises(DocumentLifecycleError, match="DOCUMENT_ASSET_MANIFEST_INVALID"):
        read_asset(service)


def test_manifest_asset_rejects_missing_corrupt_or_cross_version_file(asset_service):
    service, row = asset_service
    asset = row["metadata"]["assets"][0]
    path = service.storage_root / asset["path"]
    path.write_bytes(b"x" * len(PNG))
    with pytest.raises(DocumentLifecycleError, match="DOCUMENT_ASSET_HASH_MISMATCH"):
        read_asset(service)
    path.unlink()
    with pytest.raises(LookupError, match="DOCUMENT_ASSET_UNAVAILABLE"):
        read_asset(service)
    asset["path"] = str(service.storage_root / "other-version" / "assets" / "source.png")
    with pytest.raises(DocumentLifecycleError, match="DOCUMENT_STORAGE_PATH_INVALID"):
        read_asset(service)


def test_source_http_asset_contract_is_inline_and_original_stays_attachment(asset_service):
    service, _ = asset_service
    runtime = ApplicationRuntime.__new__(ApplicationRuntime)
    runtime.principal, runtime.documents = OWNER, service
    app = create_app(runtime, resolve_principal=lambda _request: OWNER, manage_background_workers=False)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = f"/api/v1/knowledge-bases/{KB}/documents/{DOC}/source"
            image = await client.get(url, params={"asset_path": LOGICAL_PATH, "source_version": VERSION})
            assert image.status_code == 200 and image.content == PNG
            assert image.headers["content-type"] == "image/png"
            assert image.headers["content-disposition"].startswith("inline;")
            assert image.headers["x-content-type-options"] == "nosniff"
            original = await client.get(url)
            assert original.status_code == 200 and original.content == b"original-pptx-source"
            assert original.headers["content-disposition"].startswith("attachment;")
            for params in ({"asset_path": LOGICAL_PATH}, {"source_version": VERSION}, {"asset_path": ""}):
                assert (await client.get(url, params=params)).status_code == 422
            assert (await client.get(url, params={"asset_path": LOGICAL_PATH,
                                                  "source_version": "stale"})).status_code == 404
            assert (await client.get(url.replace(KB, "other-kb"), params={"asset_path": LOGICAL_PATH,
                                                                          "source_version": VERSION})).status_code == 404

    asyncio.run(exercise())
