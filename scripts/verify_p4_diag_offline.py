"""P4-DIAG-R1: guarded, SIMULATED tests only; never invokes the live runner."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ('var/reports/p4-r1/live/live-receipt.json', 'docs/audits/p4-weknora-rag-report.md')
TESTS = (
    'backend/tests/test_p4_rerank_diagnostics.py',
    'backend/tests/test_model_provider_contracts.py',
    'backend/tests/test_p4_rag_pipeline.py',
    'backend/tests/test_context_neighbor_repository.py',
    'backend/tests/test_retrieval_routing.py',
)
SENTINELS = ('SIMULATED-DIAG-KEY-SENTINEL', 'SIMULATED-DIAG-CONTENT-SENTINEL',
             'SIMULATED-DIAG-RAW-ERROR-SENTINEL')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    # Unique run directory; no history/ledger or prior run is overwritten.
    label = sys.argv[1] if len(sys.argv) == 2 else 'final'
    if label not in {'red', 'baseline', 'baseline-replay', 'final', 'repair'}:
        raise SystemExit('OFFLINE_RUN_LABEL_INVALID')
    out = ROOT / 'var/reports/p4-diag-r1' / label
    out.mkdir(parents=True, exist_ok=False)
    before = {name: digest(ROOT / name) for name in HISTORY}
    started = time.perf_counter()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    # Clear project knobs without inspecting inherited values or key values.
    for name in tuple(os.environ):
        if name.startswith(('RAG_', 'LANGFUSE_', 'DEEPSEEK_', 'OLLAMA_')):
            os.environ.pop(name, None)
    os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
        SILICONFLOW_API_KEY=SENTINELS[0], DEEPSEEK_API_KEY=SENTINELS[0],
        RAG_DATABASE_URL='sqlite+pysqlite:///:memory:',
        RAG_STORAGE_ROOT=str(out / 'simulated-storage'))
    sys.dont_write_bytecode = True
    # Python's Windows platform discovery may shell out to `ver`. Cache only
    # locally available OS metadata before the strict no-subprocess guard.
    import platform
    if sys.platform == 'win32':
        version = sys.getwindowsversion()
        platform._uname_cache = platform.uname_result('Windows', '', str(version.major),
            f'{version.major}.{version.minor}.{version.build}',
            os.environ.get('PROCESSOR_ARCHITECTURE', 'UNKNOWN'))
    blocked = {'network': 0, 'secret_file': 0, 'subprocess': 0}
    def no_network(*args, **kwargs):
        blocked['network'] += 1
        raise RuntimeError('OFFLINE_NETWORK_BLOCKED')
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.socket.sendto = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    # Process audit hook also covers direct _socket/subprocess calls, before I/O.
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
    baseline_sha256 = None
    if label == 'baseline-replay':
        # Replay only the exact local git-show snapshot, inside this guarded
        # child process. Working source files and historical receipts stay put.
        source = ROOT / 'var/reports/p4-diag-r1/baseline-cloud.py.txt'
        baseline_sha256 = digest(source)
        from backend.app.adapters.models import cloud
        exec(compile(source.read_text(encoding='utf-8'), str(source), 'exec'), cloud.__dict__)
    import pytest
    class Counts:
        collected = 0
        passed = 0
        failed = 0
        skipped = 0
        def pytest_collection_finish(self, session): self.collected = len(session.items)
        def pytest_runtest_logreport(self, report):
            if report.when == 'call' and report.passed: self.passed += 1
            if report.failed: self.failed += 1
            if report.skipped: self.skipped += 1
    counts = Counts()
    args = [*TESTS, '-q', '--tb=no', '--show-capture=no', '-p', 'no:cacheprovider',
            '--basetemp=' + str(out / 'tmp'), '--junitxml=' + str(out / 'junit.xml')]
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        code = int(pytest.main(args, plugins=[counts]))
    log = stream.getvalue()
    # Even failing test output cannot export simulated secret/content markers.
    for marker in SENTINELS: log = log.replace(marker, '[SIMULATED_REDACTED]')
    (out / 'pytest.log').write_text(log, encoding='utf-8')
    junit = out / 'junit.xml'
    if junit.exists():
        value = junit.read_text(encoding='utf-8')
        for marker in SENTINELS: value = value.replace(marker, '[SIMULATED_REDACTED]')
        junit.write_text(value, encoding='utf-8')
    after = {name: digest(ROOT / name) for name in HISTORY}
    passed = code == 0 and counts.collected > 0 and counts.passed == counts.collected and not any(blocked.values()) and before == after
    report = dict(task='P4-DIAG-R1', evidence_kind='SIMULATED',
        result='P4_DIAG_OFFLINE_PASS' if passed else 'P4_DIAG_BLOCKED',
        pytest_exit_code=code, collected=counts.collected, passed=counts.passed,
        failed=counts.failed, skipped=counts.skipped,
        real_http_requests=0, real_model_requests=0, blocked_attempts=blocked,
        guard='socket APIs plus process audit hook; no subprocesses or secret files',
        historical_sha256_before=before, historical_sha256_after=after,
        elapsed_seconds=round(time.perf_counter()-started, 3), pytest_args=args,
        provider_acceptance='NOT RUN', historical_usage='UNKNOWN; no database access',
        baseline_adapter_sha256=baseline_sha256,
        artifacts_sha256={p.name:digest(p) for p in (out/'pytest.log', junit) if p.exists()})
    (out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('result','pytest_exit_code','collected','passed','failed','skipped','blocked_attempts','real_http_requests','real_model_requests','elapsed_seconds')}))
    print('Evidence: ' + str(out / 'report.json'))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
