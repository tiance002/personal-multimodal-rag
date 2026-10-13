"""Explicit new-instance P1 ledger DDL; no old runtime or default connection."""
import hashlib,json
from pathlib import Path

async def apply_send_gate_schema(identity_guard):
    await identity_guard.verify_identity()
    root=Path(__file__).with_name('migrations')
    sql=(root/'005_send_gate.sql').read_text(encoding='utf-8')
    manifest=json.loads((root/'source_manifest_send_gate.json').read_text(encoding='utf-8'))
    digest=hashlib.sha256(sql.encode()).hexdigest()
    if manifest['generated_sha256']!=digest:raise ValueError('MIGRATION_SOURCE_CHECKSUM_MISMATCH')
    conn=identity_guard.connection
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(20261013,5)')
        await conn.execute('CREATE TABLE IF NOT EXISTS port_schema_migrations(version TEXT PRIMARY KEY,sha256 TEXT NOT NULL,applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        rows=await (await conn.execute('SELECT sha256 FROM port_schema_migrations WHERE version=%s',('005_send_gate',))).fetchall()
        if rows and rows[0]['sha256']!=digest:raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await conn.execute(sql)
            await conn.execute('INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)',('005_send_gate',digest))
    return digest
