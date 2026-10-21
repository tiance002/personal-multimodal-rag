"""Explicit P5 lifecycle migration; isolated physical identity verified first."""
import hashlib
import json
from pathlib import Path

from .postgres import PostgresRetriever


async def apply_generation_schema(identity_guard: PostgresRetriever) -> str:
    await identity_guard.verify_identity()
    root = Path(__file__).with_name("migrations")
    sql = (root / "004_generation.sql").read_text(encoding="utf-8")
    manifest = json.loads((root / "source_manifest_generation.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(sql.encode()).hexdigest()
    if manifest["generated_sha256"] != digest:
        raise ValueError("MIGRATION_SOURCE_CHECKSUM_MISMATCH")
    conn = identity_guard.connection
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(20261013,4)")
        await conn.execute("CREATE TABLE IF NOT EXISTS port_schema_migrations(version TEXT PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        rows = await (await conn.execute("SELECT sha256 FROM port_schema_migrations WHERE version=%s", ("004_generation",))).fetchall()
        if rows and rows[0]["sha256"] != digest:
            raise ValueError("APPLIED_MIGRATION_CHECKSUM_MISMATCH")
        if not rows:
            await conn.execute(sql)
            await conn.execute("INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)", ("004_generation",digest))
    return digest
