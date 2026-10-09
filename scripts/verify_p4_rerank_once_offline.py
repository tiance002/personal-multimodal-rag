"""Guarded one-shot preparation checks. SIMULATED, never invokes live_ranker."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT/'var/reports/p4-rerank-once-prep-r1'
SOURCES = ('scripts/verify_p4_rerank_once.py', 'backend/tests/test_p4_rerank_once.py',
           'scripts/verify_p4_rerank_once_offline.py', 'backend/app/adapters/models/cloud.py',
           'backend/app/adapters/models/factory.py', 'backend/app/application/provider_usage.py',
           'backend/app/application/budget.py', 'backend/app/ports/model_access.py',
           'scripts/verify_p4_rag.py')
PRESERVED = ('backend/app/adapters/models/cloud.py', 'backend/tests/test_p4_rerank_diagnostics.py',
             'scripts/verify_p4_diag_offline.py', 'docs/audits/p4-rerank-offline-diagnostics.md',
             'var/reports/p4-r1/live/live-receipt.json',
             'var/reports/p4-r1/database-final-read-only.json',
             'var/reports/p4-diag-r1/repair/report.json', 'var/reports/p4-diag-r1/repair/junit.xml')


def hashes(names):
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}


def main():
    label = sys.argv[1] if len(sys.argv) == 2 else 'checks-1'
    if label not in {'checks-1', 'checks-2', 'checks-3', 'checks-4'}:
        raise SystemExit('OFFLINE_LABEL_INVALID')
    out = PREP/label
    out.mkdir(exist_ok=False)
    before, sources = hashes(PRESERVED), hashes(SOURCES)
    started = time.perf_counter()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    for name in tuple(os.environ):
        if name.startswith(('RAG_', 'LANGFUSE_', 'DEEPSEEK_', 'OLLAMA_', 'SILICONFLOW_')):
            os.environ.pop(name, None)
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['RAG_DATABASE_URL'] = 'sqlite+pysqlite:///:memory:'
    os.environ['RAG_STORAGE_ROOT'] = str(out/'simulated-storage')
    sys.dont_write_bytecode = True
    import platform
    if sys.platform == 'win32':
        version = sys.getwindowsversion()
        platform._uname_cache = platform.uname_result('Windows', '', str(version.major),
            f'{version.major}.{version.minor}.{version.build}', 'UNKNOWN')
    blocked = dict(network=0, secret_file=0, subprocess=0, database=0)
    def no_network(*a, **k):
        blocked['network'] += 1
        raise RuntimeError('OFFLINE_NETWORK_BLOCKED')
    socket.create_connection = socket.getaddrinfo = no_network
    socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = no_network
    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
            no_network()
        if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
            blocked['subprocess'] += 1
            raise RuntimeError('OFFLINE_SUBPROCESS_BLOCKED')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            name = os.fsdecode(args[0]).replace('\\', '/').rsplit('/', 1)[-1].lower()
            if name in {'.env', '.env.local', 'auth.json', 'token'}:
                blocked['secret_file'] += 1
                raise RuntimeError('OFFLINE_SECRET_FILE_BLOCKED')
    sys.addaudithook(guard)
    # libpq can open native sockets without Python socket audit events.
    # Block the actual project DB driver as well; tests use InMemoryBudgetGate.
    import psycopg
    def no_database(*a, **k):
        blocked['database'] += 1
        raise RuntimeError('OFFLINE_DATABASE_BLOCKED')
    psycopg.connect = no_database
    psycopg.Connection.connect = classmethod(no_database)
    import pytest
    class Counts:
        collected = None
        collection_completed = False
        passed = failed = errors = skipped = 0
        def pytest_collection_finish(self, session):
            self.collected = len(session.items)
            self.collection_completed = True
        def pytest_runtest_logreport(self, report):
            if report.when == 'call' and report.passed: self.passed += 1
            if report.failed:
                if report.when == 'call': self.failed += 1
                else: self.errors += 1
            if report.skipped: self.skipped += 1
        def pytest_collectreport(self, report):
            if report.failed: self.errors += 1
    counts, stream = Counts(), io.StringIO()
    args = ['backend/tests/test_p4_rerank_once.py', '-q', '--tb=short', '-p', 'no:cacheprovider',
            '--basetemp='+str(out/'tmp'), '--junitxml='+str(out/'junit.xml')]
    code, returned_normally, exception_type, exception_winerror = None, False, None, None
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            code = int(pytest.main(args, plugins=[counts]))
        returned_normally = True
    except BaseException as exc:
        exception_type = type(exc).__name__
        exception_winerror = getattr(exc, 'winerror', None)
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            raise SystemExit(1) from exc
        raise  # Preserve failure and traceback after saving evidence; never retry.
    finally:
        log = stream.getvalue()
        for marker in ('SIMULATED-ONCE-KEY-SENTINEL', 'SIMULATED-ONCE-RAW-SENTINEL'):
            log = log.replace(marker, '[SIMULATED_REDACTED]')
        (out/'pytest.log').write_text(log, encoding='utf-8')
        junit, junit_counts, junit_error = out/'junit.xml', None, None
        try:
            text = junit.read_text(encoding='utf-8')
            for marker in ('SIMULATED-ONCE-KEY-SENTINEL', 'SIMULATED-ONCE-RAW-SENTINEL'):
                text = text.replace(marker, '[SIMULATED_REDACTED]')
            junit.write_text(text, encoding='utf-8')
            suites = list(ET.fromstring(text).iter('testsuite'))
            junit_counts = {key:sum(int(suite.attrib[key]) for suite in suites)
                            for key in ('tests','failures','errors','skipped')}
        except (OSError, ValueError, KeyError, ET.ParseError) as exc:
            junit_error = type(exc).__name__
        after, final_sources = hashes(PRESERVED), hashes(SOURCES)
        passed = (returned_normally and code == 0 and counts.collection_completed
                  and counts.collected == counts.passed > 0 and counts.failed == counts.errors == counts.skipped == 0
                  and junit_counts == dict(tests=counts.collected, failures=0, errors=0, skipped=0)
                  and not any(blocked.values()) and before == after and sources == final_sources)
        report = dict(task='P4-RERANK-ONCE-PREP/r1/'+label, attempt=label, evidence_kind='SIMULATED',
            result='CHECKS_PASSED' if passed else 'BLOCKED', pytest_exit_code=code, pytest_args=args,
            pytest_main_returned_normally=returned_normally, pytest_exception_type=exception_type,
            pytest_exception_winerror=exception_winerror,
            completion_state='COMPLETED' if returned_normally else 'EXCEPTION',
            collection_completed=counts.collection_completed, counts_kind='OBSERVED',
            collected=counts.collected, passed=counts.passed, failed=counts.failed,
            errors=counts.errors, skipped=counts.skipped, junit_counts=junit_counts, junit_error=junit_error,
            elapsed_seconds=round(time.perf_counter()-started, 3), blocked_attempts=blocked,
            real_http_requests=0, real_model_requests=0, real_database_connections=0,
            preserved_before=before, preserved_after=after, sources_before=sources, sources_after=final_sources,
            artifacts_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (out/'pytest.log', out/'junit.xml') if p.exists()},
            owner_authorization='CONDITIONAL; NOT_EXERCISED_OFFLINE', execution='MANUALLY_SUPERVISED_TRIAL')
        (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('result','pytest_exit_code','collected','passed','failed',
            'errors','skipped','collection_completed','pytest_main_returned_normally','pytest_exception_type',
            'pytest_exception_winerror','completion_state','junit_counts',
            'blocked_attempts','real_http_requests','real_database_connections','elapsed_seconds')}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
