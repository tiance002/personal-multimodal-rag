"""Opt-in SQL verification against the approved isolated PostgreSQL target."""
import asyncio
import uuid

import pytest

from tests.weknora_port.test_memory_storage_pg import approved_dsn, actual_pg_run, connection
from backend.weknora_port.documents import DocumentLifecycleService, KnowledgeBaseSettings
from backend.weknora_port.storage.connections import IsolatedConnectionFactory
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.storage.postgres import IsolationPolicy


EXPECTED_SYSTEM_ID = "7696088403504697382"
PROHIBITED_SYSTEM_IDS = frozenset({"7691227493754040358", "7689510505384681511", "7696038373595934758"})


def test_actual_pg_document_upload_chunk_source_read_and_cleanup(tmp_path):
    dsn = approved_dsn()

    async def run():
        subject = "fixture-documents-" + str(uuid.uuid4())
        tenant_id = None
        knowledge_base_id = None
        document_id = None

        async def acquire():
            new_conn, _ = await connection(dsn)
            return new_conn

        async def release(new_conn):
            await new_conn.close()

        factory = IsolatedConnectionFactory(acquire, release,
            IsolationPolicy(EXPECTED_SYSTEM_ID, PROHIBITED_SYSTEM_IDS))
        principal = None

        async def settings(_principal):
            return KnowledgeBaseSettings(
                embedding_model_id="fixture-no-provider",
                summary_model_id="fixture-no-provider",
                rerank_model_id="fixture-no-provider",
                image_processing_config={"enable_multimodal": False},
                chunking_config={"chunk_size": 128, "chunk_overlap": 0},
                indexing_strategy={"vector_enabled": False, "keyword_enabled": False,
                                   "wiki_enabled": False, "graph_enabled": False})

        async def postprocess(_principal, _kb, _document, _ingest, chunks):
            assert chunks

        try:
            conn, guard = await connection(dsn)
            try:
                tenant_id = (await (await conn.execute(
                    "INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id",
                    ("document lifecycle fixture", "isolated-test"))).fetchone())["id"]
            finally:
                await conn.close()
            principal = LocalPrincipal(tenant_id, subject)
            service = DocumentLifecycleService(factory, tmp_path, principal=principal,
                                                resolve_kb_settings=settings, post_processor=postprocess)
            kb = await service.create_knowledge_base(principal, "document lifecycle fixture")
            knowledge_base_id = kb["id"]
            uploaded = await service.upload_document(principal, knowledge_base_id, "source.txt",
                "text/plain", b"actual isolated source paragraph")
            document_id = uploaded["id"]
            assert uploaded["status"] == "completed", uploaded
            listed = await service.list_documents(principal, knowledge_base_id)
            assert [row["id"] for row in listed] == [document_id]
            raw = await service.read_document(principal, knowledge_base_id, document_id)
            assert raw.content == b"actual isolated source paragraph"

            async with factory.lease() as retriever:
                rows = await (await retriever.connection.execute(
                    "SELECT id FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s "
                    "AND knowledge_id=%s AND deleted_at IS NULL ORDER BY chunk_index",
                    (tenant_id, knowledge_base_id, document_id))).fetchall()
            assert rows
            source = await service.read_source_chunk(principal, rows[0]["id"])
            assert source["document_id"] == document_id
            assert source["text"] == "actual isolated source paragraph"

            async with factory.lease() as retriever:
                identity = await retriever.verify_identity()
                assert identity.system_identifier == EXPECTED_SYSTEM_ID
        finally:
            if tenant_id is not None:
                conn, _ = await connection(dsn)
                try:
                    if knowledge_base_id:
                        await conn.execute("DELETE FROM embeddings WHERE knowledge_base_id=%s", (knowledge_base_id,))
                        await conn.execute("DELETE FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s",
                                           (tenant_id, knowledge_base_id))
                        await conn.execute("DELETE FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s",
                                           (tenant_id, knowledge_base_id))
                        await conn.execute("DELETE FROM knowledge_bases WHERE tenant_id=%s AND id=%s",
                                           (tenant_id, knowledge_base_id))
                    await conn.execute("DELETE FROM tenants WHERE id=%s", (tenant_id,))
                finally:
                    await conn.close()

    actual_pg_run(run())
