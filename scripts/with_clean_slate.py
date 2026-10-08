"""Explicit local P4-P7 entry point; defaults to read-only identity verification.

No credentials are stored in the profile or printed. Connection construction
reuses the project's public local-development default; PGPASSWORD, if supplied
privately by the operator, overrides that default in memory. The only project
file loaded is .env.local, and only for the two whitelisted provider keys.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from backend.app.config import Settings
from backend.app.domain.adaptive_chunking import ChunkingConfig

PROJECT_SECRET_KEYS = frozenset({'SILICONFLOW_API_KEY', 'DEEPSEEK_API_KEY'})


def _read_project_secrets(path: Path) -> dict[str, str]:
    """Read only approved provider credentials from this project's .env.local.

    No interpolation, logging, or error messages contain input values. Other
    variables in the file are ignored and cannot alter runtime security config.
    """
    secrets: dict[str, str] = {}
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return secrets
    except (OSError, UnicodeError):
        raise ValueError('PROJECT_SECRET_FILE_UNREADABLE') from None
    try:
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].strip()
            name, separator, value = line.partition('=')
            name, value = name.strip(), value.strip()
            if name not in PROJECT_SECRET_KEYS:
                continue
            if not separator or name in secrets:
                raise ValueError('PROJECT_SECRET_FILE_INVALID')
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError('PROJECT_SECRET_FILE_INVALID')
                value = value[1:-1]
            else:
                value = value.split(' #', 1)[0].rstrip()
            secrets[name] = value
    except UnicodeError:
        raise ValueError('PROJECT_SECRET_FILE_INVALID') from None
    return secrets


def resolve_environment(profile: dict, inherited: dict[str, str]) -> dict[str, str]:
    db = profile['database']
    storage = Path(profile['storage_root']).resolve()
    if (db['host'] != '127.0.0.1' or db['port'] != 25438 or db['user'] != 'rag'
            or db['name'] != 'rag_clean_dev_20261008t072656z_352f705b'
            or db['oid'] != 21278 or db['system_identifier'] != '7691227493754040358'):
        raise ValueError('CLEAN_SLATE_DATABASE_IDENTITY_INVALID')
    if (not storage.is_relative_to(Path(r'D:\RAG-CleanSlate').resolve())
            or storage.is_relative_to(Path(profile['public_benchmark_root']).resolve())
            or not (storage / 'objects').is_dir() or not (storage / 'tmp').is_dir()):
        raise ValueError('CLEAN_SLATE_STORAGE_INVALID')
    if profile['p3_index_identity'] != ChunkingConfig().identity:
        raise ValueError('CLEAN_SLATE_INDEX_IDENTITY_CHANGED')
    url = make_url(Settings().database_url).set(host=db['host'], port=db['port'],
        username=db['user'], database=db['name'])
    if inherited.get('PGPASSWORD'):
        url = url.set(password=inherited['PGPASSWORD'])
    env = inherited.copy()
    env.update(RAG_DATABASE_URL=url.render_as_string(hide_password=False),
        RAG_STORAGE_ROOT=str(storage), RAG_CLOUD_ENABLED='false', RAG_PREFER_CLOUD='false',
        RAG_CLOUD_FALLBACK_ENABLED='false', RAG_LANGFUSE_ENABLED='false',
        RAG_LANGFUSE_CAPTURE_CONTENT='false', RAG_LOCAL_QUERY_ENABLED='false',
        RAG_LOCAL_ANSWER_ENABLED='false', RAG_QUERY_REWRITE_ENABLED='false',
        RAG_RERANK_ENABLED='false', RAG_MMR_ENABLED='false', RAG_INLINE_INGESTION='false',
        RAG_MAX_CHUNK_CHARS='512', RAG_CHUNK_OVERLAP='80')
    # The only project-file values copied into a child process are these two
    # credentials. The file cannot change DB, Storage, or any allow/deny flag.
    env.update(_read_project_secrets(ROOT / '.env.local'))
    return env


def verify_database(profile: dict, env: dict[str, str]) -> dict:
    expected = profile['database']
    engine = create_engine(env['RAG_DATABASE_URL'], connect_args={'connect_timeout':3,
        'application_name':'cs0-profile-check',
        'options':'-c default_transaction_read_only=on -c statement_timeout=5000'}, hide_parameters=True)
    try:
        with engine.connect() as c:
            actual = c.execute(text("SELECT current_database() AS name, (SELECT oid FROM pg_database WHERE datname=current_database()) AS oid, (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier")).mappings().one()
            if any(actual[key] != expected[key] for key in ('name','oid','system_identifier')):
                raise ValueError('CLEAN_SLATE_ACTUAL_DATABASE_MISMATCH')
            if c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() != profile['migration_revision']:
                raise ValueError('CLEAN_SLATE_MIGRATION_MISMATCH')
            if not c.execute(text("SELECT current_setting('transaction_read_only')::boolean")).scalar_one():
                raise ValueError('CLEAN_SLATE_CHECK_MUST_BE_READ_ONLY')
            return dict(status='PASS', database=actual['name'], oid=actual['oid'],
                system_identifier=actual['system_identifier'], port=expected['port'],
                storage_root=env['RAG_STORAGE_ROOT'], migration_revision=profile['migration_revision'],
                verification='REAL_POSTGRESQL_READ_ONLY', model_calls=0)
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['check','api','worker'], default='check')
    args = parser.parse_args()
    profile = json.loads((ROOT / 'deploy/clean-slate/profile.json').read_text(encoding='utf-8'))
    try:
        env = resolve_environment(profile, os.environ.copy())
        result = verify_database(profile, env)
    except Exception as exc:
        # No traceback/DSN/credential disclosure; fail before constructing any service.
        print(json.dumps(dict(status='BLOCKED', error_type=type(exc).__name__)))
        return 1
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.mode == 'check':
        return 0
    command = ([sys.executable, '-B', '-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8000']
        if args.mode == 'api' else [sys.executable, '-B', '-m', 'backend.app.workers.ingestion'])
    return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
