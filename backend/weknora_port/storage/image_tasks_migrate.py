"""Explicit isolated-image-task migration; never runs at application import."""
import hashlib
from pathlib import Path


async def apply_image_task_schema(identity_guard):
    await identity_guard.verify_identity()
    path = Path(__file__).with_name('migrations') / '010_image_tasks.sql'
    sql = path.read_text(encoding='utf-8')
    digest = hashlib.sha256(sql.encode()).hexdigest()
    conn = identity_guard.connection
    async with conn.transaction():
        await conn.execute('SELECT pg_advisory_xact_lock(20261013,10)')
        rows = await (await conn.execute('SELECT sha256 FROM port_schema_migrations WHERE version=%s',
                                        ('010_image_tasks',))).fetchall()
        if rows and rows[0]['sha256'] != digest:
            raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await conn.execute(sql)
            await conn.execute('INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)',
                               ('010_image_tasks',digest))
    return digest
