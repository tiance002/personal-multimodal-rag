"""SIMULATED offline persistence responses; no PostgreSQL or model calls.

Memory lifecycle tests plus the real production repository/parser handoff and
source response queries. SQL recording is not a live DB/concurrency test.
"""
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
import importlib.util
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.ingestion import (
    IngestionService, IngestionWorker, InMemoryIngestionRepository, parse_version_source,
)
from backend.app.domain.models import NormalizedDocument, SourceLocator
from backend.app.domain.parsers import ParserError
from backend.app.domain.version_source import VersionSource

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RecordingParser:
    def __init__(self):
        self.calls = []
        self.fail = False

    def parse(self, path, media_type, document_id, version_id):
        content = path.read_bytes()
        self.calls.append((path.name, media_type, document_id, version_id, content))
        if self.fail:
            raise ParserError("SIMULATED_PARSE_FAILURE")
        return NormalizedDocument(document_id=document_id, version_id=version_id,
            title=path.stem, media_type=media_type,
            content_sha256=hashlib.sha256(b"synthetic source text").hexdigest(), parser_version="SIMULATED/v1",
            markdown_content="synthetic source text", sections=[], assets=[],
            source_locators=[SourceLocator(kind="text", start=0, end=21)])


def setup(tmp_path):
    repo = InMemoryIngestionRepository()
    storage = ContentAddressedStorage(tmp_path)
    parser = RecordingParser()
    service = IngestionService(repo, storage, parser)
    worker = IngestionWorker(repo, storage, parser)
    receipt = service.submit_upload("kb", "A.pdf", PDF, io.BytesIO(b"SIMULATED PDF A"))
    return repo, storage, parser, worker, receipt


def candidate(repo, storage, first, name="A.docx", media_type=DOCX):
    stored = storage.put_stream(io.BytesIO(b"SIMULATED revision B"))
    version = repo.create_version(repo.documents[first.document_id], stored.sha256, stored.storage_key,
                                  file_name=name, media_type=media_type, original_size=stored.size)
    return version, repo.create_job(version.id)


def test_a1_same_format_activation_keeps_both_revision_metadata(tmp_path):
    repo, storage, parser, worker, first = setup(tmp_path)
    worker.process(first.job_id)
    second, job = candidate(repo, storage, first, "B.pdf", PDF)
    assert worker.process(job.id).status == "succeeded"
    assert repo.active_version(first.document_id).id == second.id
    assert repo.versions[first.version_id].file_name == "A.pdf"
    assert (second.file_name, second.media_type) == ("B.pdf", PDF)
    assert repo.documents[first.document_id].file_name == "B.pdf"


def test_a2_cross_format_candidate_does_not_change_active_semantics(tmp_path):
    repo, storage, parser, worker, first = setup(tmp_path)
    worker.process(first.job_id)
    second, job = candidate(repo, storage, first)
    doc = repo.documents[first.document_id]
    assert (doc.active_version_id, doc.file_name, doc.media_type) == (first.version_id, "A.pdf", PDF)
    worker.process(job.id)
    assert (doc.active_version_id, doc.file_name, doc.media_type) == (second.id, "A.docx", DOCX)
    assert [(c[0], c[1]) for c in parser.calls] == [("A.pdf", PDF), ("A.docx", DOCX)]


def test_a3_failed_candidate_preserves_active_blob_and_metadata(tmp_path):
    repo, storage, parser, worker, first = setup(tmp_path)
    worker.process(first.job_id)
    second, job = candidate(repo, storage, first)
    parser.fail = True
    assert worker.process(job.id).status == "failed"
    active = repo.active_version(first.document_id)
    assert (active.id, active.file_name, active.media_type) == (first.version_id, "A.pdf", PDF)
    assert storage.path_for(active.storage_key).read_bytes() == b"SIMULATED PDF A"
    assert repo.documents[first.document_id].media_type == PDF


def test_a4_queued_old_job_gets_its_own_name_type_and_blob(tmp_path):
    repo, storage, parser, worker, first = setup(tmp_path)
    candidate(repo, storage, first)
    worker.process(first.job_id)
    assert parser.calls == [("A.pdf", PDF, first.document_id, first.version_id, b"SIMULATED PDF A")]


class Result:
    rowcount = 1
    def __init__(self, value=None): self.value = value
    def mappings(self): return self
    def first(self): return self.value
    def one(self): return self.value
    def scalar(self): return self.value
    def scalar_one(self): return self.value


class SQLRecorder:
    def __init__(self, handler):
        self.handler, self.calls = handler, []
    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}))
        return Result(self.handler(sql, params or {}))
    def begin(self): return nullcontext(self)
    def connect(self): return nullcontext(self)


@pytest.mark.parametrize("method", ["create_version", "create_upload"])
def test_production_candidate_insert_freezes_metadata_without_document_update(tmp_path, method):
    def handler(sql, params):
        if sql.startswith("SELECT 1 FROM knowledge_bases"): return (1,)
        if "FROM documents" in sql: return {"id":"doc", "active_version_id":"v1", "knowledge_base_id":"kb"}
        if "MAX(version_no)" in sql: return 2
    engine = SQLRecorder(handler)
    storage = ContentAddressedStorage(tmp_path)
    stored = storage.put_stream(io.BytesIO(b"revision"))
    repo = PostgresKnowledgeRepository(engine, storage)
    getattr(repo, method)("doc" if method == "create_version" else "kb", "A.docx", DOCX, stored)
    inserts = [(sql, p) for sql, p in engine.calls if sql.startswith("INSERT INTO document_versions")]
    assert len(inserts) == 1
    sql, values = inserts[0]
    assert "file_name,media_type,original_size" in sql
    assert (values["file_name"], values["media_type"], values["storage_key"], values["size"]) == ("A.docx", DOCX, stored.storage_key, stored.size)
    assert not any(sql.startswith("UPDATE documents") for sql, _ in engine.calls)


@pytest.mark.parametrize("fail", [False, True])
def test_production_old_job_parser_and_activation_use_version_metadata(tmp_path, monkeypatch, fail):
    storage = ContentAddressedStorage(tmp_path)
    stored = storage.put_stream(io.BytesIO(b"SIMULATED PDF A"))
    version = dict(id="v1", document_id="doc", knowledge_base_id="kb", version_no=1,
                   file_name="A.pdf", media_type=PDF, storage_key=stored.storage_key,
                   source_sha256=stored.sha256, original_size=stored.size)
    def handler(sql, params):
        if sql.startswith("SELECT * FROM ingestion_jobs"): return {"version_id":"v1"}
        if sql.startswith("SELECT dv.*"):
            assert "d.file_name" not in sql and "d.media_type" not in sql
            return version
        if sql.startswith("SELECT 1 FROM ingestion_jobs"): return (1,)
        if sql.startswith("SELECT active_version_id"): return None
    engine = SQLRecorder(handler)
    parser = RecordingParser(); parser.fail = fail
    repo = PostgresKnowledgeRepository(engine, storage, parsers=parser)
    monkeypatch.setattr(repo, "renew_job", lambda *a, **kw: True)
    monkeypatch.setattr(repo, "update_job_progress", lambda *a, **kw: None)
    monkeypatch.setattr(repo, "_lease_heartbeat", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(repo, "get_job", lambda *a: {})
    repo.process_job("job", worker_id="worker", claim_token="claim")
    assert parser.calls == [("A.pdf", PDF, "doc", "v1", b"SIMULATED PDF A")]
    updates = [(sql, p) for sql, p in engine.calls if sql.startswith("UPDATE documents SET active_version_id")]
    if fail:
        assert not updates
        assert any("index_status='failed'" in sql for sql, _ in engine.calls)
    else:
        assert len(updates) == 1
        assert (updates[0][1]["file_name"], updates[0][1]["media_type"], updates[0][1]["size"]) == ("A.pdf", PDF, stored.size)


@pytest.mark.parametrize("active", ["v1", None])
def test_a5_production_source_readback_and_http_response_match_selected_version(tmp_path, active):
    from backend.app.api.routes import get_document_source, get_document_preview
    from starlette.requests import Request
    storage = ContentAddressedStorage(tmp_path)
    stored = storage.put_stream(io.BytesIO(b"source A"))
    row = dict(file_name="A.pdf", media_type=PDF, active_version_id=active, version_id="v1",
               storage_key=stored.storage_key, version_no=1)
    def handler(sql, params):
        assert "SELECT dv.file_name,dv.media_type" in sql
        assert "COALESCE( d.active_version_id" in sql
        return row
    repo = PostgresKnowledgeRepository(SQLRecorder(handler), storage)
    result = repo.get_document_source("doc")
    assert (result["version_id"], result["file_name"], result["media_type"], result["path"].read_bytes()) == ("v1", "A.pdf", PDF, b"source A")
    container = SimpleNamespace(store=repo, office_preview=SimpleNamespace(is_office_document=lambda *a: False))
    request = Request({"type":"http", "app":SimpleNamespace(state=SimpleNamespace(container=container))})
    for route in (get_document_source, get_document_preview):
        response = route("doc", request)
        assert response.path == result["path"] and response.media_type == PDF
        assert "A.pdf" in response.headers["content-disposition"]


def test_legacy_unknown_is_generic_download_and_refuses_reingestion(tmp_path):
    storage = ContentAddressedStorage(tmp_path)
    stored = storage.put_stream(io.BytesIO(b"legacy bytes"))
    row = dict(id="v0", document_id="doc", version_no=1, source_sha256=stored.sha256,
               storage_key=stored.storage_key, file_name=None, media_type=None, version_id="v0")
    repo = PostgresKnowledgeRepository(SQLRecorder(lambda *a: row), storage)
    source = repo.get_document_source("doc")
    assert (source["file_name"], source["media_type"], source["metadata_status"]) == ("v0.bin", "application/octet-stream", "legacy_unknown")
    parser = Mock()
    with pytest.raises(ParserError, match="LEGACY_VERSION_METADATA_UNKNOWN"):
        parse_version_source(parser, storage, VersionSource.from_row(row))
    parser.parse.assert_not_called()


def test_version_source_contract_is_immutable_and_migration_keeps_unknowns(monkeypatch):
    source = VersionSource("v", "d", 1, "a" * 64, "key", "A.pdf", PDF)
    with pytest.raises(FrozenInstanceError): source.media_type = DOCX
    spec = importlib.util.spec_from_file_location("p1_migration", "alembic/versions/0014_version_source_metadata.py")
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    operations = Mock(); monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert operations.method_calls and all(c[0] == "add_column" for c in operations.method_calls)
    assert [(c.args[1].name, c.args[1].nullable) for c in operations.add_column.call_args_list] == [("file_name", True), ("media_type", True), ("original_size", True)]
    with pytest.raises(RuntimeError, match="forward-only"): migration.downgrade()


def test_historical_citation_keeps_old_version_and_quote_after_document_changes():
    quote = "old PDF source fact"
    row = dict(label="E1", version_id="v1", chunk_id="old-chunk", quote=quote,
               quote_sha256=hashlib.sha256(quote.encode()).hexdigest(), locator={"kind":"pdf", "page":1},
               document_id="doc", active_version_id="v2", content=quote)
    def handler(sql, params):
        assert "c.version_id=ae.version_id" in sql
        assert "d.media_type" not in sql and "d.file_name" not in sql
        return row
    repository = PostgresKnowledgeRepository(SQLRecorder(handler), storage=None)
    citation = repository.get_citation("old-run", "E1")
    assert (citation["version_id"], citation["chunk_id"], citation["quote"], citation["current_status"]) == ("v1", "old-chunk", quote, "superseded")
