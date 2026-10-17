"""Explicit usage-only schema installation; never changes historical ledgers."""
import hashlib
from pathlib import Path


async def apply_provider_calls_schema(guard):
    await guard.verify_identity()
    raw = (Path(__file__).with_name('migrations')/'015_provider_calls.sql').read_text(encoding='utf-8')
    digest = hashlib.sha256(raw.encode()).hexdigest()
    async with guard.connection.transaction():
        await guard.connection.execute('SELECT pg_advisory_xact_lock(20261017,15)')
        rows = await (await guard.connection.execute(
            'SELECT sha256 FROM port_schema_migrations WHERE version=%s', ('015_provider_calls',))).fetchall()
        if rows and rows[0]['sha256'] != digest:
            raise ValueError('APPLIED_MIGRATION_CHECKSUM_MISMATCH')
        if not rows:
            await guard.connection.execute(raw)
            await guard.connection.execute('INSERT INTO port_schema_migrations(version,sha256) VALUES(%s,%s)',
                                           ('015_provider_calls', digest))
    return digest


if __name__ == '__main__':
    import asyncio
    import psycopg
    from psycopg.rows import dict_row
    from ..development import DevelopmentSettings, PROHIBITED
    from .postgres import PostgresRetriever, IsolationPolicy

    async def main():
        settings = DevelopmentSettings.from_environment()
        async with await psycopg.AsyncConnection.connect(settings.database_url,
                autocommit=True, row_factory=dict_row, connect_timeout=5) as connection:
            guard = PostgresRetriever(connection, IsolationPolicy(
                settings.system_id, PROHIBITED, settings.database))
            print('015_provider_calls ' + await apply_provider_calls_schema(guard))

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
