"""Explicit 0017 upgrade for the one Owner-authorized, empty development DB.

No service is launched, no row is deleted, and no global database URL is used
for migration. Abort on unknown consumers or nonempty business tables.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, inspect, text
from scripts.with_clean_slate import resolve_environment, verify_database

REVISION = "0017_embedding_profile_identity"
OLD_REVISION = "0016_parent_child_chunks"


def snapshot(profile, env):
    identity = verify_database(profile, env)
    engine = create_engine(env['RAG_DATABASE_URL'], hide_parameters=True, connect_args={
        'application_name':'cs0-migration-preflight',
        'options':'-c default_transaction_read_only=on -c statement_timeout=5000'})
    try:
        with engine.connect() as c:
            consumers = [dict(r) for r in c.execute(text("SELECT pid,application_name,backend_type,state FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'")).mappings()]
            if consumers:
                raise RuntimeError('CLEAN_SLATE_UNREGISTERED_CONSUMER')
            tables = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename")).scalars().all()
            counts = {t:c.execute(text('SELECT count(*) FROM "'+t+'"')).scalar_one() for t in tables}
            if any(counts.values()):
                raise RuntimeError('CLEAN_SLATE_MUST_BE_EMPTY')
            return dict(identity=identity,consumers=consumers,counts=counts,
                unique_constraints=inspect(c).get_unique_constraints('embedding_profiles'),
                embedding_foreign_keys=inspect(c).get_foreign_keys('chunk_embeddings'),
                chunk_foreign_keys=inspect(c).get_foreign_keys('chunks'),
                fingerprint_nullable=next(x['nullable'] for x in inspect(c).get_columns('embedding_profiles') if x['name']=='fingerprint'))
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reapply',action='store_true',help='Verify idempotent upgrade at already applied 0017')
    args = parser.parse_args()
    prefix = 'migration-reapply' if args.reapply else 'migration'
    out = ROOT/'var/reports/p4-pre-r3-fix'
    out.mkdir(parents=True,exist_ok=True)
    path = ROOT/'deploy/clean-slate/profile.json'
    original_bytes = path.read_bytes()
    profile = json.loads(original_bytes)
    if profile['migration_revision'] != (REVISION if args.reapply else OLD_REVISION):
        raise RuntimeError('CLEAN_SLATE_UNEXPECTED_MIGRATION_BASELINE')
    env = resolve_environment(profile,os.environ.copy())
    # Four new provider roles also remain disabled throughout the dedicated run.
    env.update({f'RAG_{role}_EGRESS_ENABLED':'false' for role in ('EMBEDDING','CHAT','RERANK','VISION')})
    before = snapshot(profile,env)
    (out/(prefix+'-before.json')).write_text(json.dumps(before,indent=2),encoding='utf-8')
    command = [sys.executable,'-B','-m','alembic','upgrade',REVISION]
    with (out/(prefix+'.log')).open('w',encoding='utf-8') as log:
        result = subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    (out/(prefix+'-command.json')).write_text(json.dumps(dict(command=command,exit_code=result.returncode,
        database=profile['database'],environment='Identity-checked explicit Clean-slate URL; no inherited global DB; all model roles disabled'),indent=2),encoding='utf-8')
    if result.returncode:
        raise RuntimeError('CLEAN_SLATE_ALEMBIC_UPGRADE_FAILED')
    updated = dict(profile,migration_revision=REVISION)
    after = snapshot(updated,env)
    expected = ['provider','model_name','model_revision','dimension','distance','fingerprint']
    assert next(c['column_names'] for c in after['unique_constraints'] if c['name']=='embedding_profiles_identity_ux') == expected
    assert not after['fingerprint_nullable']
    assert before['embedding_foreign_keys'] == after['embedding_foreign_keys']
    assert before['chunk_foreign_keys'] == after['chunk_foreign_keys']
    assert before['counts'] == after['counts']
    (out/(prefix+'-after.json')).write_text(json.dumps(after,indent=2),encoding='utf-8')
    assert path.read_bytes() == original_bytes, 'Profile drift'
    path.write_bytes(original_bytes.replace(OLD_REVISION.encode(),REVISION.encode()))
    print(json.dumps(dict(status='PASS',migration_revision=REVISION,business_counts_unchanged=True,
        fingerprint_nullable=False,foreign_keys_unchanged=True,database=profile['database'])))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(status='BLOCKED',error_type=type(exc).__name__)))
        raise SystemExit(1)
