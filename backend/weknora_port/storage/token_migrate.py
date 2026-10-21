"""Explicit isolated migration; startup only checks, never creates schema."""
import hashlib
import json
from pathlib import Path


async def apply_token_schema(guard):
    await guard.verify_identity()
    root=Path(__file__).with_name('migrations')
    raw = root.joinpath('014_token_authority.sql').read_text(encoding='utf-8').encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    if json.loads(root.joinpath('source_manifest_token_authority.json').read_text(encoding='utf-8'))['generated_sha256']!=digest:
        raise ValueError('MIGRATION_SOURCE_CHECKSUM_MISMATCH')
    async with guard.connection.transaction():
        await guard.connection.execute('SELECT pg_advisory_xact_lock(20261015,14)')
        rows = await (await guard.connection.execute('SELECT sha256 FROM port_schema_migrations WHERE version=%s', ('014_token_authority',))).fetchall()
        if rows and rows[0]['sha256'] != digest:
            raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await guard.connection.execute(raw.decode('utf-8'))
            await guard.connection.execute('INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)', ('014_token_authority', digest))
    return digest
