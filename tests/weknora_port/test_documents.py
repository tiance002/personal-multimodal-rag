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
        if query.startswith("SELECT id,knowledge_base_id,file_name,parse_status,error_message,created_at FROM knowledges"):
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
            status, code, metadata, doc_id, tenant, kb_id = params
            row = self.docs.get(doc_id)
            if row and (row["tenant_id"], row["knowledge_base_id"]) == (tenant, kb_id):
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
                    "parse_status": doc["parse_status"]}])
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
