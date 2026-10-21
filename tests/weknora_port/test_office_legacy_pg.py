"""Real native upload/PG/Redis/readback; only legacy tool output is substituted."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from markitdown import MarkItDown

from backend.weknora_port.ingestion import upstream_doc_parser as doc_source
from backend.weknora_port.ingestion import upstream_ppt_convert as ppt_source
from tests.weknora_port.test_office_legacy import office_payloads, install_doc_tools
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run


def test_native_legacy_office_original_assets_versions_and_failure_isolation(monkeypatch):
    docx, pptx, png = office_payloads()
    legacy_doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1synthetic-doc-tool-input"
    legacy_ppt = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1synthetic-ppt-tool-input"
    trace = install_doc_tools(monkeypatch, doc_source.DocParser, docx)
    monkeypatch.setattr(ppt_source, "convert_ppt_to_pptx_bytes", lambda raw, suffix: pptx if raw == legacy_ppt else None)
    original_convert = MarkItDown.convert
    def convert(self, stream, **kwargs):
        if kwargs.get("file_extension") == ".pptx":
            return SimpleNamespace(text_content="Blue widgets use protocol W-17. ![diagram](unresolved.png)")
        return original_convert(self, stream, **kwargs)
    monkeypatch.setattr(MarkItDown, "convert", convert)
    async def scenario():
        run_id = os.environ["WEKNORA_PORT_TEST_REDIS_RUN_ID"]
        async with isolated_mock_application(expected_redis_run_id=run_id, redis_db=15, redis_port=56425) as (app, wire, root):
            kb = (await app.create_knowledge_base(app.principal, "Legacy Office scoped fixture"))["id"]
            await app.update_knowledge_base_consent(app.principal, kb, True)
            receipts = []
            cases = [("legacy.doc", "application/msword", legacy_doc),
                     ("legacy.ppt", "application/vnd.ms-powerpoint", legacy_ppt)]
            for name, media_type, raw in cases:
                result = await app.upload_document(app.principal, kb, name, media_type, raw)
                assert result["status"] == "completed", result
                assert (await app.read_document(app.principal, kb, result["id"])).content == raw
                async with app.leases.lease() as guard:
                    metadata = (await (await guard.connection.execute(
                        "SELECT metadata FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND id=%s",
                        (app.principal.tenant_id, kb, result["id"]))).fetchone())["metadata"]
                    chunks = await (await guard.connection.execute(
                        "SELECT content,source_content,metadata FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=%s AND chunk_type='text'",
                        (app.principal.tenant_id, kb, result["id"]))).fetchall()
                    count = (await (await guard.connection.execute(
                        "SELECT count(*) AS n FROM embeddings WHERE knowledge_id=%s AND is_enabled=true", (result["id"],))).fetchone())["n"]
                assert chunks and count > 0
                assert all(row["metadata"]["source_version"] == metadata["source_version"] for row in chunks)
                assets = metadata["assets"]
                assert assets and all(asset["logical_path"] in "\n".join(row["source_content"] or row["content"] for row in chunks) for asset in assets)
                for asset in assets:
                    assert metadata["source_version"] in Path(asset["path"]).parts
                    payload = (root / asset["path"]).read_bytes()
                    assert hashlib.sha256(payload).hexdigest() == asset["sha256"]
                    assert payload == png
                receipts.append({"name": name, "document_id": result["id"], "original_sha256": hashlib.sha256(raw).hexdigest(),
                                 "source_version": metadata["source_version"], "assets": assets, "index_count": count})
            bad = await app.upload_document(app.principal, kb, "broken.ppt", "application/vnd.ms-powerpoint", b"unconvertible")
            assert bad["status"] == "failed", bad
            assert (await app.read_document(app.principal, kb, bad["id"])).content == b"unconvertible"
            healthy = await app.upload_document(app.principal, kb, "healthy.txt", "text/plain", b"Blue widgets W-17.")
            assert healthy["status"] == "completed"
            for item in receipts:
                assert (await app.read_document(app.principal, kb, item["document_id"])).content in (legacy_doc, legacy_ppt)
            Path("docs/weknora-port/evidence/OFFICE_LEGACY_20261015/" + os.environ.get("WEKNORA_OFFICE_TEST_RECEIPT", "native-pg.json")).write_text(json.dumps({
                "kind": "REAL_NATIVE_PG_REDIS_ENTRY_WITH_LEGACY_TOOL_SUBSTITUTES_AND_REAL_DOCX_PPTX_DECODING",
                "cases": receipts, "legacy_doc_commands": len(trace), "failed_upload_preserved": True,
                "later_upload_healthy": True, "redis_db": 15, "redis_port": 56425, "redis_run_id": run_id,
                "storage_root": str(root), "real_provider_calls": 0, "mock_protocol_calls": len(wire),
                "real_soffice_antiword_conversion": "NOT_RUN"}, indent=2), encoding="utf-8")
    actual_pg_run(scenario())
