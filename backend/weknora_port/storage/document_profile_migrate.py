"""Explicit local role/privacy schema; no import-time or old startup migration."""
import hashlib
import json
from pathlib import Path

from .postgres import PostgresRetriever


async def apply_document_profile_schema(guard: PostgresRetriever) -> str:
    await guard.verify_identity()
    root = Path(__file__).with_name('migrations')
    sql = (root / '009_document_profile.sql').read_text(encoding='utf-8')
    digest = hashlib.sha256(sql.encode()).hexdigest()
    manifest = json.loads((root / 'source_manifest_document_profile.json').read_text(encoding='utf-8'))
    if digest != manifest['generated_sha256']:
        raise ValueError('MIGRATION_SOURCE_CHECKSUM_MISMATCH')
    connection = guard.connection
    async with connection.transaction():
        await connection.execute('SELECT pg_advisory_xact_lock(20261013,9)')
        rows = await (await connection.execute('SELECT sha256 FROM port_schema_migrations WHERE version=%s',('009_document_profile',))).fetchall()
        if rows and rows[0]['sha256'] != digest:
            raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await connection.execute(sql)
            await connection.execute('INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)',('009_document_profile',digest))
    return digest
