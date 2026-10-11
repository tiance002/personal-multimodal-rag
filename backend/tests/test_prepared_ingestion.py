"""Real offline parser/CAS validation; SIMULATED SQL/provider persistence.

No live database, network, model API, upload or paid provider request.
"""
from contextlib import nullcontext
from dataclasses import replace
import hashlib
from importlib.metadata import version
import io
import json
from unittest.mock import Mock
import uuid

import pytest

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.adaptive_chunking import ChunkingConfig, prepare_document
from backend.app.domain.models import DocumentAsset, DocumentTable, TableCell, TableRowProof
from backend.app.domain.parsers import ParserError
from backend.app.domain.version_source import VersionSource
from backend.app.domain.table_evidence import table_row_texts
from backend.app.ports.ingestion import parse_version_source
from backend.app.ports.prepared_ingestion import (
    PreparedIngestionInput, PRODUCER_LIBRARIES, producer_module_hashes, validate_prepared_input,
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def fixture(tmp_path, *, partial=False, asset=False, table=False):
    storage = ContentAddressedStorage(tmp_path / "cas")
    text = "# Original heading\n\n" + "source evidence. " * 90
    if table:
        cell = TableCell(coordinate="A1", row=1, column=1, value="Original", value_type="text", display="Original")
        table1 = DocumentTable(table_id="table-local", cell_range="A1:A1", cells=[cell], start=0, end=0)
        text = table_row_texts(table1)[0][1]
        table1 = table1.model_copy(update={"end": len(text)})
    stored = storage.put_stream(io.BytesIO(text.encode()))
    source = VersionSource(str(uuid.uuid4()), str(uuid.uuid4()), 1,
                           stored.sha256, stored.storage_key, "original.md", "text/markdown", stored.size)
    original = replace(source, document_id="draft:document", version_id="draft:version")
    document = parse_version_source(ParserRegistry(), storage, original)
    if partial:
        document = document.model_copy(update={"parse_status": "partial", "parse_warnings": ("OCR_EMPTY",)})
    attachments = ()
    if asset:
        image = b"SIMULATED pinned asset bytes"
        asset1 = DocumentAsset(asset_id="local-image", asset_type="source_image", source_bytes=image,
            source_locator={"sha256": sha(image), "bbox": [0, 0, 10, 10], "coordinate_basis": "image-pixels-top-left"})
        asset2 = DocumentAsset(asset_id="local-ocr", asset_type="ocr_text", derived_from_asset_id="local-image",
            status="failed", error_code="OCR_EMPTY")
        document = document.model_copy(update={"assets": [asset1, asset2]})
        attachments = ((sha(image), image),)
    if table:
        proof = TableRowProof(document_id=document.document_id, version_id=document.version_id,
            source_sha256=source.source_sha256, table_id=table1.table_id, row=1, cell_range="A1",
            header_policy="SIMULATED", cells=(cell,), parse_status="partial" if partial else "complete")
        document = document.model_copy(update={"tables": [table1], "table_row_proofs": [proof]})
    config = ChunkingConfig()
    prepared = prepare_document(document, config)
    raw = encode(document.model_dump(mode="json"))
    chunks = encode(dict(status="OFFLINE_DRAFT_NOT_FORMAL_INDEX", document_id=document.document_id,
        version_id=document.version_id, source_sha256=source.source_sha256,
        normalized_content_sha256=document.content_sha256, chunker_config_identity=config.identity,
        parents=[p.model_dump(mode="json") for p in prepared.parents],
        children=[c.model_dump(mode="json") for c in prepared.children], diagnostics=prepared.diagnostics))
    assets = []
    for a in document.assets:
        entry = a.model_dump(mode="json")
        if a.source_bytes is not None:
            entry["source_bytes_artifact"] = dict(path="D:/descriptive-only/image.png", bytes=len(a.source_bytes), sha256=sha(a.source_bytes))
        assets.append(entry)
    assets = encode(assets)
    bundle = PreparedIngestionInput(source.source_sha256, stored.size, source.file_name, source.media_type,
        raw, sha(raw), chunks, sha(chunks), assets, sha(assets), document.parser_version, document.parser_engine,
        producer_module_hashes(), tuple((name, version(name)) for name in PRODUCER_LIBRARIES), attachments)
    return storage, source, document, prepared, bundle


def mutate(bundle, field, mutation):
    value = json.loads(getattr(bundle, field + "_json"))
    mutation(value)
    data = encode(value)
    return replace(bundle, **{field + "_json": data, field + "_sha256": sha(data)})


def test_offline_original_and_reused_content_chunks_match_and_formal_ids_bind(tmp_path):
    storage, source, original, chunks, bundle = fixture(tmp_path)
    bound, reused = validate_prepared_input(bundle, storage, source, ChunkingConfig())
    assert bound.document_id == source.document_id and bound.version_id == source.version_id
    assert bound.markdown_content == original.markdown_content
    assert reused.parents == chunks.parents and reused.children == chunks.children
    native = parse_version_source(ParserRegistry(), storage, source)
    assert native == bound and prepare_document(native).children == reused.children


def test_partial_assets_and_table_proofs_preserved_and_rebound(tmp_path):
    storage, source, original, _, bundle = fixture(tmp_path, partial=True, asset=True, table=True)
    bound, _ = validate_prepared_input(bundle, storage, source, ChunkingConfig())
    assert bound.parse_status == "partial" and bound.parse_warnings == ("OCR_EMPTY",)
    assert bound.assets[1].status == "failed" and bound.assets[1].error_code == "OCR_EMPTY"
    assert bound.assets[0].source_bytes == original.assets[0].source_bytes
    assert bound.assets[0].storage_key is None
    assert bound.table_row_proofs[0].document_id == source.document_id
    assert bound.table_row_proofs[0].version_id == source.version_id
    assert bound.table_row_proofs[0].parse_status == "partial"


@pytest.mark.parametrize("case", ["source", "size", "artifact", "parser", "module", "library", "version", "schema",
    "config", "offset", "quote", "parent", "draft_id", "asset", "attachment", "foreign_storage", "geometry", "table", "coverage", "purpose"])
def test_invalid_prepared_input_rejected_before_provider_or_asset_write(tmp_path, case):
    storage, source, _, _, bundle = fixture(tmp_path, asset=True, table=True)
    if case == "source": bundle = replace(bundle, source_sha256="0" * 64)
    if case == "size": bundle = replace(bundle, original_size=1)
    if case == "artifact": bundle = replace(bundle, normalized_sha256="0" * 64)
    if case == "parser": bundle = replace(bundle, parser_version="wrong")
    if case == "module": bundle = replace(bundle, module_hashes=())
    if case == "library": bundle = replace(bundle, library_versions=())
    if case == "version": bundle = replace(bundle, chunker_version="wrong")
    if case == "schema": bundle = replace(bundle, locator_schema_version="wrong")
    if case == "config": bundle = mutate(bundle, "chunks", lambda v: v.update(chunker_config_identity="wrong"))
    if case == "offset": bundle = mutate(bundle, "chunks", lambda v: v["children"][0].update(end=999999))
    if case == "quote": bundle = mutate(bundle, "chunks", lambda v: v["children"][0]["source_locator"].update(quote="Gold answer"))
    if case == "parent": bundle = mutate(bundle, "chunks", lambda v: v["children"][0].update(parent_index=999))
    if case == "draft_id": bundle = mutate(bundle, "chunks", lambda v: v.update(document_id="other"))
    if case == "asset": bundle = mutate(bundle, "chunks", lambda v: v["children"][0]["source_locator"].update(asset_id="unknown"))
    if case == "attachment": bundle = replace(bundle, attachments=())
    if case == "foreign_storage": bundle = mutate(bundle, "normalized", lambda v: v["assets"][0].update(storage_key="D:/private"))
    if case == "geometry": bundle = mutate(bundle, "assets", lambda v: v[0]["source_locator"].update(coordinate_basis="unknown-v2"))
    if case == "table": bundle = mutate(bundle, "normalized", lambda v: v["table_row_proofs"][0].update(source_sha256="0" * 64))
    if case == "coverage": bundle = mutate(bundle, "chunks", lambda v: v.update(children=[]))
    if case == "purpose": bundle = replace(bundle, purpose="Gold")
    engine, repo = simulated_repository(storage, source)
    repo.embedding_provider = Mock(provider_kind="local")
    with pytest.raises(ParserError, match="PREPARED_"):
        validate_prepared_input(bundle, storage, source, repo.chunking_config)
    before = set(storage.root.rglob("*"))
    repo.process_job("job", worker_id="worker", claim_token="claim", prepared_input=bundle)
    repo.embedding_provider.embed.assert_not_called()
    assert set(storage.root.rglob("*")) == before
    assert not any(sql.startswith("INSERT INTO") for sql, _ in engine.calls)
    assert any(p.get("code", "").startswith("PREPARED_") for _, p in engine.calls)


def test_original_cas_corruption_fails_closed(tmp_path):
    storage, source, _, _, bundle = fixture(tmp_path)
    storage.path_for(source.storage_key).write_bytes(b"corrupted")
    with pytest.raises(ParserError, match="PREPARED_SOURCE_HASH_MISMATCH"):
        validate_prepared_input(bundle, storage, source, ChunkingConfig())


class Result:
    rowcount = 1
    def __init__(self, value=None): self.value = value
    def mappings(self): return self
    def first(self): return self.value
    def one(self): return self.value
    def scalar(self): return self.value


class SimulatedSQL:
    def __init__(self, source): self.source, self.calls = source, []
    def begin(self): return nullcontext(self)
    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split()); self.calls.append((sql, params or {}))
        if sql.startswith("SELECT * FROM ingestion_jobs"): return Result({"version_id": self.source.version_id})
        if sql.startswith("SELECT dv.*"):
            return Result(dict(id=self.source.version_id, document_id=self.source.document_id,
                knowledge_base_id=str(uuid.UUID(int=3)), version_no=1, source_sha256=self.source.source_sha256,
                storage_key=self.source.storage_key, file_name=self.source.file_name,
                media_type=self.source.media_type, original_size=self.source.original_size))
        if sql.startswith("SELECT 1 FROM ingestion_jobs"): return Result((1,))
        return Result()


def simulated_repository(storage, source):
    engine = SimulatedSQL(source)
    repo = PostgresKnowledgeRepository(engine, storage)
    repo.renew_job = Mock(return_value=True)
    repo.update_job_progress = Mock()
    repo._lease_heartbeat = lambda *a, **kw: nullcontext()
    repo.get_job = lambda *a: {"SIMULATED": True}
    return engine, repo


def test_process_job_reuses_artifacts_and_keeps_formal_planned_child_uuid_path(tmp_path, monkeypatch):
    storage, source, _, prepared, bundle = fixture(tmp_path)
    engine, repo = simulated_repository(storage, source)
    repo.parsers = Mock()
    repo.embedding_provider = Mock(provider_kind="local")
    planned = tuple(str(uuid.UUID(int=i + 10)) for i in range(len(prepared.children)))
    repo._planned_index_chunk_ids = Mock(return_value=planned)
    repo._embed_index_chunks = Mock(return_value=[[1.0] + [0.0] * 1023 for _ in planned])
    repo._get_or_create_embedding_profile = Mock(return_value="SIMULATED-profile")
    monkeypatch.setattr("backend.app.adapters.postgres.knowledge_repository.prepare_document",
                        Mock(side_effect=AssertionError("prepared chunks must be reused")))
    repo.process_job("job", worker_id="worker", claim_token="claim", prepared_input=bundle)
    repo.parsers.parse.assert_not_called()
    assert repo._planned_index_chunk_ids.call_args.kwargs["version_id"] == source.version_id
    assert repo._embed_index_chunks.call_args.kwargs["chunk_ids"] == planned
    assert repo._embed_index_chunks.call_args.kwargs["claim_token"] == "claim"
    child_rows = [p for sql, p in engine.calls if sql.startswith("INSERT INTO chunks") and p["chunk_role"] == "child"]
    assert [str(p["id"]) for p in child_rows] == list(planned)
    assert all(p["version_id"] == source.version_id and p["document_id"] == source.document_id for p in child_rows)
    assert any("index_status='ready'" in sql for sql, _ in engine.calls)


def test_process_job_default_keeps_original_parser_chunker_path(tmp_path):
    storage, source, _, _, _ = fixture(tmp_path)
    engine, repo = simulated_repository(storage, source)
    repo.process_job("job", worker_id="worker", claim_token="claim")
    assert any(sql.startswith("INSERT INTO chunks") for sql, _ in engine.calls)
    assert any("index_status='ready'" in sql for sql, _ in engine.calls)
