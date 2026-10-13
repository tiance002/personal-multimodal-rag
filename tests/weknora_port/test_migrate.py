"""Schema admission tests; these do not execute a PostgreSQL migration."""
import asyncio
import hashlib
import json

import pytest

from backend.weknora_port.storage.migrate import apply_initial_schema, MIGRATIONS
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from tests.weknora_port.test_storage import FakeConnection, FakeCursor


class MigrationConnection(FakeConnection):
    def __init__(self, identifier="new", prior=()):
        super().__init__()
        self.identifier, self.prior = identifier, prior
    async def execute(self, query, params=()):
        self.calls.append((query,params))
        if "pg_control_system" in query:
            return FakeCursor([{"database":"weknora_python_port_20261013", "system_identifier":self.identifier}])
        if query.startswith("SELECT sha256"):
            return FakeCursor(list(self.prior))
        return FakeCursor([])


def test_schema_matches_manifest_and_uses_source_paradedb_revision_tables():
    source = (MIGRATIONS / "001_retrieval.sql").read_text(encoding="utf-8")
    manifest = json.loads((MIGRATIONS / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["generated_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert '"chinese_lindera"' in source and 'USING bm25' in source
    assert 'CREATE TABLE IF NOT EXISTS chunk_revisions' in source
    assert 'ADD COLUMN IF NOT EXISTS source_content' in source
    assert 'CREATE TABLE IF NOT EXISTS users' not in source


def test_old_instance_fails_before_any_ddl_and_invalid_dimensions_do_not_interpolate():
    async def exercise():
        conn = MigrationConnection(identifier="old")
        repo = PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})))
        with pytest.raises(PermissionError): await apply_initial_schema(repo,[1024])
        assert len(conn.calls)==1 and "pg_control_system" in conn.calls[0][0]
        conn.identifier="new"
        for dimensions in (["1024);DROP TABLE chunks;"], [1024,"bad"], [1,True]):
            with pytest.raises(ValueError): await apply_initial_schema(repo,dimensions)
        assert not any(query.startswith("CREATE") for query,_ in conn.calls)
    asyncio.run(exercise())


def test_migration_receipt_and_configured_hnsw_expression():
    async def exercise():
        conn = MigrationConnection()
        repo = PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})))
        digest = await apply_initial_schema(repo,[1024,1024])
        assert len(digest)==64
        assert any("INSERT INTO port_schema_migrations" in query for query,_ in conn.calls)
        indexes = [query for query,_ in conn.calls if query.startswith("CREATE INDEX IF NOT EXISTS embeddings_embedding")]
        assert len(indexes)==1 and 'embedding::halfvec(1024)' in indexes[0]
        conn2 = MigrationConnection(prior=({"sha256":"wrong"},))
        with pytest.raises(ValueError,match="APPLIED_MIGRATION_CHECKSUM"):
            await apply_initial_schema(PostgresRetriever(conn2,repo.isolation),[1024])
        assert conn2.rollback
        assert not any(query.startswith("CREATE EXTENSION") for query,_ in conn2.calls)
    asyncio.run(exercise())
