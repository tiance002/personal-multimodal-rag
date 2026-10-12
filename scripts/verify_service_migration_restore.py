"""Restore the local backup into an isolated temporary PostgreSQL, then migrate.

No business DB writes, model credentials, cloud requests, or raw document output.
Docker resources created here have exact task labels and are removed on exit.
"""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'var/reports/no-candidates-20261011'
NETWORK = 'rag-no-candidates-migration-20261011'
CONTAINER = 'rag-no-candidates-migration-db-20261011'
LABEL = 'codex.task=no-candidates-20261011-migration-restore'
SNAPSHOT = """SELECT json_build_object(
 'revision',(SELECT version_num FROM alembic_version),
 'documents',(SELECT count(*) FROM documents),
 'versions',(SELECT count(*) FROM document_versions),
 'chunks',(SELECT count(*) FROM chunks),
 'runs',(SELECT count(*) FROM rag_runs),
 'active_hash',(SELECT md5(coalesce(string_agg(id::text || ':' || coalesce(active_version_id::text,'NULL'),',' ORDER BY id::text),'')) FROM documents),
 'source_hash',(SELECT md5(coalesce(string_agg(id::text || ':' || document_id::text || ':' || source_sha256,',' ORDER BY id::text),'')) FROM document_versions),
 'chunk_hash',(SELECT md5(coalesce(string_agg(id::text || ':' || version_id::text || ':' || content,',' ORDER BY id::text),'')) FROM chunks)
);"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quota-check', action='store_true')
    options = parser.parse_args()
    receipts = []
    report = dict(schema_version=1, check_kind='ISOLATED_REAL_POSTGRES_BACKUP_RESTORE_MIGRATION',
        status='FAIL', business_db_migration='NOT RUN', provider_calls=0, commands=receipts)
    network_created = container_created = False
    def run(args, *, data=None, check=True):
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        receipts.append(dict(command=args, exit_code=result.returncode))
        if check and result.returncode:
            raise RuntimeError('ISOLATED_COMMAND_FAILED:' + args[0])
        return result
    def snapshot():
        return json.loads(run(['docker', 'exec', CONTAINER, 'psql', '-U', 'postgres', '-d', 'rag_restore',
            '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', SNAPSHOT]).stdout)
    try:
        backup = REPORTS / 'business-db-before-0023.dump'
        metadata = json.loads((REPORTS / 'business-db-backup.json').read_text(encoding='utf-8'))
        raw = backup.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == metadata['backup_sha256']
        report['backup_sha256'] = metadata['backup_sha256']
        run(['docker', 'network', 'create', '--internal', '--label', LABEL, NETWORK])
        network_created = True
        run(['docker', 'run', '--pull=never', '--rm', '-d', '--name', CONTAINER, '--label', LABEL,
            '--network', NETWORK, '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=rag_restore',
            'pgvector/pgvector:pg16'])
        container_created = True
        for _ in range(30):
            if run(['docker', 'exec', CONTAINER, 'pg_isready', '-U', 'postgres', '-d', 'rag_restore'], check=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('ISOLATED_POSTGRES_NOT_READY')
        run(['docker', 'exec', '-i', CONTAINER, 'pg_restore', '-U', 'postgres', '-d', 'rag_restore',
            '--no-owner', '--no-acl', '--exit-on-error'], data=raw)
        before = snapshot()
        assert before['revision'] == '0013_message_run_link'
        # Existing local image supplies the dependency runtime; mounted code
        # supplies the current migrations. No application server is started.
        run(['docker', 'run', '--pull=never', '--rm', '--network', NETWORK,
            '--mount', f'type=bind,source={ROOT / "backend"},target=/app/backend,readonly',
            '--mount', f'type=bind,source={ROOT / "alembic"},target=/app/alembic,readonly',
            '-e', 'RAG_DATABASE_URL=postgresql+psycopg://postgres@' + CONTAINER + ':5432/rag_restore',
            '--entrypoint', 'python', 'raglocalfirst0930-api', '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'])
        after = snapshot()
        assert after['revision'] == '0023_service_token_policy'
        assert {k:v for k,v in before.items() if k != 'revision'} == {k:v for k,v in after.items() if k != 'revision'}
        if options.quota_check:
            result = run(['docker', 'run', '--pull=never', '--rm', '--network', NETWORK,
                '--mount', f'type=bind,source={ROOT / "backend"},target=/app/backend,readonly',
                '--mount', f'type=bind,source={ROOT / "scripts"},target=/app/scripts,readonly',
                '-e', 'RAG_DATABASE_URL=postgresql+psycopg://postgres@' + CONTAINER + ':5432/rag_restore',
                '-e', 'PYTHONPATH=/app', '--entrypoint', 'python', 'raglocalfirst0930-api',
                '/app/scripts/smoke_service_quota_postgres.py'])
            report['quota_check'] = json.loads(result.stdout)
            assert snapshot() == after
        report.update(status='CHECKS_PASSED', before=before, after=after,
            original_content_source_and_activation_unchanged=True)
    except Exception as error:
        report['failure_type'] = type(error).__name__
        raise
    finally:
        if container_created:
            info = json.loads(run(['docker', 'inspect', CONTAINER]).stdout)[0]
            assert info['Config']['Labels'].get('codex.task') == LABEL.split('=',1)[1]
            run(['docker', 'stop', CONTAINER])
        if network_created:
            info = json.loads(run(['docker', 'network', 'inspect', NETWORK]).stdout)[0]
            assert info['Labels'].get('codex.task') == LABEL.split('=',1)[1]
            run(['docker', 'network', 'rm', NETWORK])
        filename = 'migration-quota-isolated.json' if options.quota_check else 'migration-restore-isolated.json'
        (REPORTS / filename).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k != 'commands'}))


if __name__ == '__main__':
    main()
