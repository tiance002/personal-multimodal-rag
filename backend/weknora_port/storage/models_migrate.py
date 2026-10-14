"""Explicit checksum-verified 008 apply on the authorized physical target only."""
import hashlib
import json
from pathlib import Path


async def apply_model_schema(identity_guard):
    await identity_guard.verify_identity()
    root = Path(__file__).with_name('migrations')
    sql = (root / '008_models.sql').read_text(encoding='utf-8')
    digest = hashlib.sha256(sql.encode()).hexdigest()
    manifest = json.loads((root / 'source_manifest_models.json').read_text(encoding='utf-8'))
    if digest != manifest['generated_sha256']:
        raise ValueError('MIGRATION_SOURCE_CHECKSUM_MISMATCH')
    conn = identity_guard.connection
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(20261013,8)')
        await conn.execute('CREATE TABLE IF NOT EXISTS port_schema_migrations(version TEXT PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        rows = await (await conn.execute('SELECT sha256 FROM port_schema_migrations WHERE version=%s', ('008_models',))).fetchall()
        if rows and rows[0]['sha256'] != digest:
            raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await conn.execute(sql)
            await conn.execute('INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)', ('008_models', digest))
    return digest
