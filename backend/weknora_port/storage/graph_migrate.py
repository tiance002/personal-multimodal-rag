"""Explicit S6 graph installation; never imported as an automatic migration."""
import hashlib
from pathlib import Path


async def apply_graph_schema(guard):
    await guard.verify_identity()
    raw = (Path(__file__).with_name('migrations') / '016_graph.sql').read_text(encoding='utf-8')
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    async with guard.connection.transaction():
        await guard.connection.execute('SELECT pg_advisory_xact_lock(20261019,16)')
        rows = await (await guard.connection.execute(
            'SELECT sha256 FROM port_schema_migrations WHERE version=%s', ('016_graph',))).fetchall()
        if rows and rows[0]['sha256'] != digest:
            raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await guard.connection.execute(raw)
            await guard.connection.execute(
                'INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)',
                ('016_graph', digest))
    return digest
