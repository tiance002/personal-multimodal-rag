"""Explicit isolated-schema application; never invoked at import or old startup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

from .postgres import PostgresRetriever

MIGRATIONS = Path(__file__).with_name("migrations")


async def apply_initial_schema(repository: PostgresRetriever, dimensions: Sequence[int]) -> str:
    # Verify the physical dedicated connection afresh before *any* DDL. There is
    # no automatic production migration and no old database fallback.
    await repository.verify_identity()
    if any(type(dim) is not int or dim <= 0 for dim in dimensions):
        raise ValueError("INVALID_EMBEDDING_DIMENSION")
    dims = sorted(set(dimensions))
    schema = (MIGRATIONS / "001_retrieval.sql").read_text(encoding="utf-8")
    manifest = json.loads((MIGRATIONS / "source_manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(schema.encode("utf-8")).hexdigest()
    if manifest["generated_sha256"] != digest:
        raise ValueError("MIGRATION_SOURCE_CHECKSUM_MISMATCH")
    conn = repository.connection
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(20261013, 1)")
        await conn.execute("CREATE TABLE IF NOT EXISTS port_schema_migrations "
                           "(version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, "
                           "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        cursor = await conn.execute("SELECT sha256 FROM port_schema_migrations WHERE version = %s", ("001_retrieval",))
        rows = await cursor.fetchall()
        if rows and rows[0]["sha256"] != digest:
            raise ValueError("APPLIED_MIGRATION_CHECKSUM_MISMATCH")
        if not rows:
            await conn.execute(schema)
            await conn.execute("INSERT INTO port_schema_migrations (version, sha256) VALUES (%s, %s)",
                               ("001_retrieval", digest))
        # Exact source HNSW expression/parameters; dimensions are configured from
        # the embedding model, rather than universally fixing 3584/798/1024.
        for dimension in dims:
            await conn.execute(f"CREATE INDEX IF NOT EXISTS embeddings_embedding_idx_{dimension} "
                f"ON embeddings USING hnsw ((embedding::halfvec({dimension})) halfvec_cosine_ops) "
                f"WITH (m = 16, ef_construction = 64) WHERE (dimension = {dimension})")
    return digest


async def apply_history_and_memory_schema(repository: PostgresRetriever) -> dict[str, str]:
    """Explicit fresh-instance DDL, after retrieval; common tables precede memory."""
    await repository.verify_identity()
    migrations = (("003_common", "source_manifest_common.json"),
                  ("002_memory", "source_manifest_memory.json"))
    prepared = []
    for version, manifest_file in migrations:
        sql = (MIGRATIONS / (version + ".sql")).read_text(encoding="utf-8")
        manifest = json.loads((MIGRATIONS / manifest_file).read_text(encoding="utf-8"))
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if manifest["generated_sha256"] != digest:
            raise ValueError("MIGRATION_SOURCE_CHECKSUM_MISMATCH")
        prepared.append((version, sql, digest))
    connection = repository.connection
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(20261013, 1)")
        initial = await (await connection.execute(
            "SELECT sha256 FROM port_schema_migrations WHERE version=%s", ("001_retrieval",))).fetchall()
        expected = json.loads((MIGRATIONS / "source_manifest.json").read_text(encoding="utf-8"))["generated_sha256"]
        if len(initial) != 1 or initial[0]["sha256"] != expected:
            raise ValueError("RETRIEVAL_SCHEMA_REQUIRED")
        for version, sql, digest in prepared:
            rows = await (await connection.execute(
                "SELECT sha256 FROM port_schema_migrations WHERE version=%s", (version,))).fetchall()
            if rows and rows[0]["sha256"] != digest:
                raise ValueError("APPLIED_MIGRATION_CHECKSUM_MISMATCH")
            if not rows:
                await connection.execute(sql)
                await connection.execute("INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)",
                                         (version, digest))
    return {version: digest for version, _, digest in prepared}
