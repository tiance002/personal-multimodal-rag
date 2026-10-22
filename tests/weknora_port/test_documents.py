import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import backend.weknora_port.documents as documents
from backend.weknora_port.documents import DocumentLifecycleService, KnowledgeBaseSettings
from backend.weknora_port.storage.history import LocalPrincipal


PRINCIPAL = LocalPrincipal(tenant_id=81013, subject_id="local-owner-fixture")


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def fetchall(self):
        return self.rows


class MemoryConnection:
    def __init__(self):
        self.kbs = {}
        self.docs = {}
        self.chunks = {}
        self.events = []

    async def execute(self, sql, params=()):
        query = " ".join(sql.split())
        self.events.append(query.split(" ")[0:3])
        if query.startswith("INSERT INTO knowledge_bases"):
            kb_id, name, description, tenant, chunking, image, embedding, summary, rerank, strategy = params
            row = {"id": kb_id, "name": name, "description": description, "tenant_id": tenant,
                   "chunking_config": json.loads(chunking), "image_processing_config": json.loads(image),
                   "embedding_model_id": embedding, "summary_model_id": summary, "rerank_model_id": rerank,
                   "indexing_strategy": json.loads(strategy), "type": "document", "extract_config": None}
            self.kbs[kb_id] = row
            return Cursor([{"id": kb_id, "name": name}])
        if query.startswith("SELECT kb.id,kb.name"):
            tenant = params[0]
            return Cursor([{"id": row["id"], "name": row["name"],
                            "document_count": sum(d["tenant_id"] == tenant and
                                                   d["knowledge_base_id"] == row["id"] and
                                                   d.get("deleted_at") is None
                                                   for d in self.docs.values())}
                           for row in self.kbs.values() if row["tenant_id"] == tenant])
        if query.startswith("SELECT * FROM knowledge_bases"):
            kb_id, tenant = params
            row = self.kbs.get(kb_id)
            return Cursor([row] if row and row["tenant_id"] == tenant else [])
        if query.startswith("SELECT id,knowledge_base_id,file_name,title,parse_status,error_message,created_at FROM knowledges"):
            tenant, kb_id = params
            return Cursor([d for d in self.docs.values() if d["tenant_id"] == tenant and
                           d["knowledge_base_id"] == kb_id and d.get("deleted_at") is None])
        if query.startswith("INSERT INTO knowledges"):
            (doc_id, tenant, kb_id, title, source, embedding, file_name, file_type,
             file_size, file_path, digest, metadata, custom) = params
            self.docs[doc_id] = {"id": doc_id, "tenant_id": tenant, "knowledge_base_id": kb_id,
                "title": title, "file_name": file_name, "file_type": file_type,
                "file_size": file_size, "file_path": file_path, "file_hash": digest,
                "metadata": json.loads(metadata), "custom_metadata": json.loads(custom),
                "embedding_model_id": embedding, "parse_status": "processing", "error_message": "",
                "created_at": "now", "processed_at": None}
            return Cursor([{"id": doc_id}])
        if query.startswith("UPDATE knowledges SET parse_status=%s"):
            status, code, metadata, doc_id, tenant, kb_id, version = params
            row = self.docs.get(doc_id)
            if (row and (row["tenant_id"], row["knowledge_base_id"]) == (tenant, kb_id)
                    and row.get("deleted_at") is None
                    and row["metadata"].get("source_version") == version
                    and row["parse_status"] in ("pending", "processing")):
                row.update(parse_status=status, error_message=code, metadata=json.loads(metadata))
                return Cursor([{"id": doc_id}])
            return Cursor()
        if query.startswith("UPDATE embeddings SET is_enabled=TRUE"):
            return Cursor()
        if query.startswith("UPDATE knowledges SET parse_status='completed'"):
            metadata, doc_id, tenant, kb_id = params
            row = self.docs.get(doc_id)
            if row and row["tenant_id"] == tenant and row["knowledge_base_id"] == kb_id and row["parse_status"] == "processing":
                row.update(parse_status="completed", error_message="", metadata=json.loads(metadata), processed_at="now")
                return Cursor([{"id": doc_id}])
            return Cursor()
        if query.startswith("SELECT id,knowledge_base_id,file_name,title,parse_status,error_message,created_at,processed_at FROM knowledges"):
            doc_id, tenant, kb_id = params
            row = self.docs.get(doc_id)
            return Cursor([row] if row and row["tenant_id"] == tenant and row["knowledge_base_id"] == kb_id else [])
        if query.startswith("SELECT file_name,file_type,file_path,file_hash,file_size FROM knowledges"):
            doc_id, tenant, kb_id = params
            row = self.docs.get(doc_id)
            return Cursor([row] if row and row["tenant_id"] == tenant and row["knowledge_base_id"] == kb_id else [])
        if query.startswith("SELECT c.id AS chunk_id"):
            chunk_id, tenant1, tenant2 = params
            row = self.chunks.get(chunk_id)
            doc = self.docs.get(row.scope.knowledge_id) if row else None
            if row and doc and tenant1 == tenant2 == doc["tenant_id"]:
                return Cursor([{"chunk_id": row.id, "document_id": row.scope.knowledge_id,
                    "knowledge_base_id": row.scope.knowledge_base_id, "content": row.content,
                    "source_content": row.source_content, "chunk_index": row.chunk_index,
                    "start_at": row.start_at, "end_at": row.end_at, "filename": doc["file_name"],
                    "parse_status": doc["parse_status"], "chunk_metadata": dict(row.metadata),
                    "document_metadata": doc["metadata"], "chunk_type": row.chunk_type,
                    "image_info": row.image_info}])
            return Cursor()
        raise AssertionError(f"unhandled test SQL: {query}")

    @asynccontextmanager
    async def transaction(self):
        yield


class VerifiedRepository:
    def __init__(self, connection):
        self.connection = connection
        self.writable_knowledge_base_ids = frozenset()

    def require_verified(self):
        return None

    async def batch_save(self, rows, embeddings):
        self.connection.events.append(["batch_save", str(len(rows)), str(len(embeddings))])


class Factory:
    def __init__(self):
        self.connection = MemoryConnection()

    @asynccontextmanager
    async def lease(self, *, writable_knowledge_base_ids=frozenset()):
        repository = VerifiedRepository(self.connection)
        repository.writable_knowledge_base_ids = writable_knowledge_base_ids
        yield repository


class FixtureUpload:
    def __init__(self, data):
        self.data = data

    async def read(self):
        return self.data


class FakeEmbedder:
    async def embed(self, texts):
        return [[0.25, 0.75] for _ in texts]


class FakeIndex:
    def __init__(self, repository):
        self.repository = repository

    async def batch_index(self, rows, *, vector_enabled, embedder=None, sleep=None):
        assert all(not row.is_enabled for row in rows)
        assert vector_enabled is False
        await self.repository.batch_save(rows, {})


class FakeChunks:
    def __init__(self, repository, resolve_tenant):
        self.repository = repository

    async def create_chunks(self, plan, context):
        self.repository.require_verified()
        assert context.scope.owner_id == PRINCIPAL.subject_id
        assert context.scope.knowledge_base_ids == (plan.persisted[0].scope.knowledge_base_id,)
        self.repository.connection.chunks.update({row.id: row for row in plan.persisted})


def fixture_settings(_principal):
    return KnowledgeBaseSettings(
        embedding_model_id="embedding-fixture", summary_model_id="summary-fixture",
        rerank_model_id="rerank-fixture",
        chunking_config={"chunk_size": 90, "chunk_overlap": 0, "strategy": "recursive"},
        indexing_strategy={"vector_enabled": False, "keyword_enabled": True,
                           "wiki_enabled": False, "graph_enabled": False},
        image_processing_config={"enable_multimodal": False, "model_id": ""})


def make_service(tmp_path, *, post_processor=None):
    factory = Factory()
    service = DocumentLifecycleService(factory, tmp_path / "storage-new", principal=PRINCIPAL,
        resolve_kb_settings=fixture_settings, post_processor=post_processor)
    return service, factory


def test_source_readback_exposes_version_position_and_safe_image_details_and_rejects_stale(tmp_path, monkeypatch):
    service, _ = make_service(tmp_path)
    row = {"chunk_id": "chunk", "document_id": "document", "knowledge_base_id": "kb",
           "filename": "scan.png", "content": "OCR source", "source_content": "",
           "chunk_index": 2, "start_at": 10, "end_at": 20, "parse_status": "completed",
           "chunk_type": "image_ocr", "chunk_metadata": {"source_version": "version-1", "page_number": 3},
           "document_metadata": {"source_version": "version-1"},
           "image_info": json.dumps([{"url": "E:/private/internal.png", "page_number": 3,
               "source_type": "scanned_pdf", "ocr_text": "OCR source", "caption": "A chart",
               "ocr_status": {"status": "completed", "binding": {"internal": "hidden"}},
               "caption_status": {"status": "failed", "error": "MODEL_CALL_FAILED"}}])}
    async def rows(query, params):
        assert params == ("chunk", PRINCIPAL.tenant_id, PRINCIPAL.tenant_id)
        return [row]
    monkeypatch.setattr(service, "_rows", rows)
    async def exercise():
        source = await service.read_source_chunk(PRINCIPAL, "chunk")
        assert (source["source_version"], source["page_number"], source["start"], source["end"]) == ("version-1", 3, 10, 20)
        assert source["image_info"][0]["ocr_status"] == {"status": "completed"}
        assert source["image_info"][0]["caption_status"] == {"status": "failed", "error": "MODEL_CALL_FAILED"}
        assert "private" not in json.dumps(source) and "binding" not in json.dumps(source)
        row["document_metadata"]["source_version"] = "version-2"
        with pytest.raises(LookupError, match="SOURCE_CHUNK_UNAVAILABLE"):
            await service.read_source_chunk(PRINCIPAL, "chunk")
        row["chunk_metadata"] = {}
        with pytest.raises(LookupError, match="SOURCE_CHUNK_UNAVAILABLE"):
            await service.read_source_chunk(PRINCIPAL, "chunk")
    asyncio.run(exercise())


def test_text_and_markdown_uploads_persist_immutable_source_chunks_and_complete_with_injected_postprocessor(tmp_path, monkeypatch):
    async def post_processor(principal, kb, document, ingest, chunks):
        assert principal == PRINCIPAL
        assert document["source_version"]
        assert ingest.parser_engine in ("plain", "markdown")
        assert chunks

    async def exercise():
        service, factory = make_service(tmp_path, post_processor=post_processor)

        def create_chunks(repo, resolve_tenant):
            return FakeChunks(repo, resolve_tenant)

        monkeypatch.setattr(documents, "ScopedKnowledgeRepository", create_chunks)
        monkeypatch.setattr(documents, "HybridIndexer", FakeIndex)

        kb = await service.create_knowledge_base(PRINCIPAL, "Research")
        text = await service.upload_document(PRINCIPAL, kb["id"], "guide.txt", "text/plain",
                                             FixtureUpload(b"first paragraph\n\nsecond paragraph"))
        markdown = await service.upload_document(PRINCIPAL, kb["id"], "notes.md", "text/markdown",
                                                 FixtureUpload(b"# Notes\n\n- immutable\n- source"))

        assert text["status"] == markdown["status"] == "completed", tuple(
            (row["status"], row["error_code"]) for row in (text, markdown))
        docs = await service.list_documents(PRINCIPAL, kb["id"])
        assert [row["filename"] for row in docs] == ["guide.txt", "notes.md"]
        stored_text=factory.connection.docs[text['id']]
        stored_text['file_name']=''
        assert (await service.list_documents(PRINCIPAL,kb['id']))[0]['filename']==stored_text['title']
        stored_text['file_name']='guide.txt'
        assert (await service.list_knowledge_bases(PRINCIPAL))[0]["document_count"] == 2

        for item, raw in ((text, b"first paragraph\n\nsecond paragraph"),
                          (markdown, b"# Notes\n\n- immutable\n- source")):
            downloaded = await service.read_document(PRINCIPAL, kb["id"], item["id"])
            assert downloaded.content == raw
            source_rows = [row for row in factory.connection.chunks.values()
                           if row.scope.knowledge_id == item["id"]]
            assert source_rows
            source = await service.read_source_chunk(PRINCIPAL, source_rows[0].id)
            assert source["text"] == (source_rows[0].source_content or source_rows[0].content)
            assert source["document_id"] == item["id"]
            assert len({row.scope.source_version for row in source_rows}) == 1
            assert Path(source_rows[0].scope.knowledge_base_id) != Path("E:/RAG")

        raw_paths = [Path(row["file_path"]) for row in factory.connection.docs.values()]
        assert len(raw_paths) == 2 and len(set(raw_paths)) == 2
        assert all(path.is_file() for path in raw_paths)
        assert any(event[0] == "batch_save" for event in factory.connection.events)
        raw_paths[0].write_bytes(b'mutated synthetic fixture')
        with pytest.raises(documents.DocumentLifecycleError,match='DOCUMENT_SOURCE_HASH_MISMATCH'):
            await service.read_document(PRINCIPAL,kb['id'],text['id'])

    asyncio.run(exercise())


def test_missing_postprocessor_keeps_text_document_pending_and_not_search_indexed(tmp_path, monkeypatch):
    async def exercise():
        service, factory = make_service(tmp_path)
        monkeypatch.setattr(documents, "ScopedKnowledgeRepository",
                            lambda repo, resolve_tenant: FakeChunks(repo, resolve_tenant))
        monkeypatch.setattr(documents, "HybridIndexer", FakeIndex)
        kb = await service.create_knowledge_base(PRINCIPAL, "Pending")
        item = await service.upload_document(PRINCIPAL, kb["id"], "pending.txt", "text/plain",
                                             FixtureUpload(b"summary processing is required"))
        assert item["status"] == "pending", (item["status"], item["error_code"])
        assert item["error_code"] == "POSTPROCESSOR_ADAPTER_REQUIRED"
        assert factory.connection.docs[item["id"]]["parse_status"] == "pending"

    asyncio.run(exercise())


def test_bound_principal_and_kb_scope_are_checked_before_file_write(tmp_path):
    async def exercise():
        service, _ = make_service(tmp_path, post_processor=lambda *args: None)
        foreign = LocalPrincipal(tenant_id=PRINCIPAL.tenant_id, subject_id="other-subject")
        with pytest.raises(PermissionError, match="LOCAL_PRINCIPAL_SCOPE_MISMATCH"):
            await service.list_knowledge_bases(foreign)
        assert not (tmp_path / "storage-new").exists()

    asyncio.run(exercise())


def test_legacy_page_selection_does_not_filter_full_pdf_image_processing(tmp_path, monkeypatch):
    """P31 removes P27 filtering while preserving the historical KB setting."""
    import io
    import fitz
    from PIL import Image

    async def exercise():
        service, factory = make_service(tmp_path, post_processor=lambda *args: None)
        monkeypatch.setattr(documents, "ScopedKnowledgeRepository",
                            lambda repo, resolve_tenant: FakeChunks(repo, resolve_tenant))
        monkeypatch.setattr(documents, "HybridIndexer", FakeIndex)
        kb = await service.create_knowledge_base(PRINCIPAL, "Full PDF")
        config = factory.connection.kbs[kb["id"]]["image_processing_config"]
        config.update(enable_multimodal=True, image_pages=[999])
        processed = []

        async def process_images(principal, image_kb, scope, assets):
            assert principal == PRINCIPAL and image_kb["id"] == scope.knowledge_base_id
            processed.extend(assets)
            return ()

        service.asset_processor = process_images
        pdf = fitz.open()
        image = io.BytesIO()
        Image.new("RGB", (360, 480), (70, 100, 150)).save(image, format="PNG")
        native = pdf.new_page(width=595, height=842)
        native.insert_text((55, 80), "Native text evidence with an embedded figure.")
        native.insert_image(fitz.Rect(100, 200, 400, 600), stream=image.getvalue())
        scanned = pdf.new_page(width=595, height=842)
        scanned.insert_image(scanned.rect, stream=image.getvalue())
        raw = pdf.tobytes()
        pdf.close()
        item = await service.upload_document(PRINCIPAL, kb["id"], "full.pdf", "application/pdf",
                                             FixtureUpload(raw))
        assert item["status"] == "completed", item
        assert {(asset.page_number, asset.source_type) for asset in processed} == {
            (1, "embedded_pdf_image"), (2, "scanned_pdf")}
        metadata = factory.connection.docs[item["id"]]["metadata"]
        assert len(metadata["assets"]) == 2
        assert config["image_pages"] == [999]
        assert (await service.read_document(PRINCIPAL, kb["id"], item["id"])).content == raw

    asyncio.run(exercise())
