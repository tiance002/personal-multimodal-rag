"""Explicit isolated PG schema probe; no old DSN/env or model credentials read."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import selectors

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.weknora_port.storage.migrate import apply_initial_schema, apply_history_and_memory_schema
from backend.weknora_port.storage.postgres import IsolationPolicy, PostgresRetriever


async def run(options):
    import psycopg
    from psycopg.rows import dict_row
    policy = IsolationPolicy(options.expected_system_identifier, frozenset(options.prohibited_system_identifier))
    async with await psycopg.AsyncConnection.connect(options.dsn, autocommit=True, row_factory=dict_row) as connection:
        repository = PostgresRetriever(connection, policy)
        identity = await repository.verify_identity()
        migrations = {'001_retrieval': await apply_initial_schema(repository, [3])}
        migrations.update(await apply_history_and_memory_schema(repository))
        extensions = await (await connection.execute("SELECT extname,extversion FROM pg_extension ORDER BY extname")).fetchall()
        receipts = await (await connection.execute("SELECT version,sha256 FROM port_schema_migrations ORDER BY version")).fetchall()
        evidence = {'status':'PASS', 'kind':'ACTUAL_ISOLATED_PG_SCHEMA',
                    'database':identity.database, 'system_identifier':identity.system_identifier,
                    'migrations':migrations, 'receipts':receipts, 'extensions':extensions,
                    'embedding_dimension':3, 'embedding_kind':'SYNTHETIC_FIXTURE_ONLY',
                    'real_provider_calls':0, 'old_database_writes':0}
        output = Path(options.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', required=True)
    parser.add_argument('--expected-system-identifier', required=True)
    parser.add_argument('--prohibited-system-identifier', action='append', required=True)
    parser.add_argument('--report', required=True)
    options = parser.parse_args()
    if sys.platform == 'win32':
        asyncio.run(run(options), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(run(options))
