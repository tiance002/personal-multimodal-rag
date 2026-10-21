"""Offline SQL/FS boundary tests only: no database or provider requests."""
import asyncio
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from scripts import weknora_port_p14_fence as fence
from backend.weknora_port.storage.token_budget import encode


class Result:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class Database:
    def __init__(self):
        self.month = dict(fence.AUTHORITY, month=fence.MONTH, limit_tokens=fence.OLD_LIMIT)
        self.calls = [dict(call_id='settled-old', state='settled', actual_total=607728, held_tokens=607728,
                           usage_facts={'raw': 'unchanged'}),
                      dict(call_id='unknown-old', state='unknown', actual_total=None, held_tokens=593156,
                           usage_facts=None)]
        self.identity = dict(system_identifier=fence.SYSTEM_ID, database=fence.DATABASE)
        self.clock = fence.MONTH
        self.sql = []
        self.cas_denied = False
        self.commit_ack_lost = False
        self.connections = []

    async def connect(self):
        conn = Connection(self)
        self.connections.append(conn)
        return conn


class Connection:
    def __init__(self, db):
        self.db = db
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.closed = False

    def transaction(self):
        conn = self

        class Transaction:
            async def __aenter__(self):
                self.before = deepcopy((conn.db.month, conn.db.calls))
                conn.info.transaction_status = TransactionStatus.INTRANS

            async def __aexit__(self, kind, error, tb):
                conn.info.transaction_status = TransactionStatus.IDLE
                if kind is not None:
                    conn.db.month, conn.db.calls = self.before
                elif conn.db.commit_ack_lost:
                    conn.db.commit_ack_lost = False
                    raise ConnectionError('SIMULATED_COMMIT_ACK_LOST')

        return Transaction()

    async def execute(self, sql, params=()):
        self.db.sql.append((sql, params))
        if 'pg_control_system()' in sql:
            return Result([self.db.identity])
        if 'clock_timestamp()' in sql:
            return Result([dict(month=self.db.clock)])
        if sql.startswith('SELECT * FROM port_token_budget_months'):
            assert params == fence.KEY
            return Result([self.db.month] if self.db.month is not None else [])
        if sql.startswith('SELECT * FROM port_token_calls'):
            assert params == fence.KEY
            return Result(sorted(self.db.calls, key=lambda row: row['call_id']))
        if sql.startswith('UPDATE '):
            assert sql.startswith('UPDATE port_token_budget_months SET limit_tokens=%s ')
            assert params[1:4] == fence.KEY
            assert params[4:] == (self.db.month['limit_tokens'], 14, 'KNOWN', fence.CARRY_SOURCE,
                                  fence.APPROVAL_SOURCE, '')
            if self.db.cas_denied:
                return Result([])
            self.db.month['limit_tokens'] = params[0]
            return Result([self.db.month])
        assert sql.startswith('SET TRANSACTION') or sql.startswith('SELECT pg_advisory_xact_lock')
        return Result([])

    async def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fence, '_evidence_root', lambda: tmp_path)
    return Database(), tmp_path


def run_open(db, approval='Owner approves P14 only +140000; no future remainder.'):
    return asyncio.run(fence.open_fence(approval_text=approval, connection_factory=db.connect))


def run_close(db):
    return asyncio.run(fence.close_fence(connection_factory=db.connect))


def updates(db):
    return [query for query in db.sql if query[0].startswith('UPDATE ')]


def test_fixed_p14_grant_and_exact_increment():
    assert fence.GRANT_ID == 'P14-20261016-SF-140000'
    assert fence.OLD_LIMIT == 1_327_808
    assert fence.OPEN_LIMIT == 1_467_808
    assert fence.OPEN_LIMIT - fence.OLD_LIMIT == 140_000
    assert fence.FENCE_DIR.as_posix() == 'E:/WK/run/p14-20261016/fence-140000'


def test_open_once_close_exact_occupancy_preserves_unknown_and_new_calls(setup):
    db, root = setup
    original = deepcopy(db.calls)
    opened = run_open(db)
    assert opened['commit_confirmed'] is True
    assert opened['after']['headroom'] == 266910  # prior headroom 126,910 + authorized 140,000
    assert db.month == dict(fence.AUTHORITY, month=fence.MONTH, limit_tokens=1467808)
    assert db.calls == original
    assert run_open(db) == opened and len(updates(db)) == 1
    db.calls.append(dict(call_id='p14-new-unknown', state='unknown', actual_total=None, held_tokens=100000))
    all_calls = deepcopy(db.calls)
    closed = run_close(db)
    assert closed['after']['headroom'] == 0
    assert db.month['limit_tokens'] == 1300898
    assert db.month['limit_tokens'] != 1467808  # ordinary reserve's authority comparison conflicts
    assert db.calls == all_calls
    assert run_close(db) == closed and len(updates(db)) == 2
    with pytest.raises(fence.FenceError, match='ALREADY_CLOSING_OR_CLOSED'):
        run_open(db)
    for name in ('intent', 'open-before', 'open-precommit', 'open-after',
                 'close-before', 'close-precommit', 'close-after'):
        assert fence._read(root, name) is not None
    assert len(opened['before']['calls']) == 2
    assert all('sha256' in row and 'call_id' in row for row in opened['before']['calls'])
    assert all(conn.closed for conn in db.connections)


def test_same_lock_as_normal_token_store_and_no_other_mutation_sql(setup):
    db, _ = setup
    run_open(db)
    run_close(db)
    expected = 'weknora-token-month:' + encode([str(k) for k in fence.KEY])
    locks = [params for sql, params in db.sql if 'pg_advisory_xact_lock' in sql]
    assert locks == [(expected,), (expected,)]
    for sql, params in updates(db):
        assert 'SET limit_tokens=%s WHERE account_id=%s AND provider=%s AND month=%s ' in sql
        assert 'AND approval_source=%s AND blocked_reason=%s RETURNING *' in sql
    assert not any(sql.startswith(('INSERT ', 'DELETE ')) for sql, _ in db.sql)


@pytest.mark.parametrize('condition', ['system', 'database', 'month', 'carry', 'status', 'source',
                                      'approval', 'blocked', 'limit', 'reserved', 'missing'])
def test_open_denies_wrong_identity_authority_reserved_or_limit_without_mutation(setup, condition):
    db, _ = setup
    if condition == 'system':
        db.identity['system_identifier'] = 'old-prohibited-db'
    elif condition == 'database':
        db.identity['database'] = 'other'
    elif condition == 'month':
        db.clock = date(2026, 11, 1)
    elif condition == 'carry':
        db.month['carry_in_tokens'] = 0
    elif condition == 'status':
        db.month['carry_status'] = 'UNKNOWN'
    elif condition == 'source':
        db.month['carry_source'] = 'other'
    elif condition == 'approval':
        db.month['approval_source'] += ':different'
    elif condition == 'blocked':
        db.month['blocked_reason'] = 'USAGE_EXCEEDS_RESERVATION'
    elif condition == 'limit':
        db.month['limit_tokens'] = 1467808
    elif condition == 'reserved':
        db.calls[0]['state'] = 'reserved'
    elif condition == 'missing':
        db.month = None
    before = deepcopy((db.month, db.calls))
    with pytest.raises(fence.FenceError):
        run_open(db)
    assert (db.month, db.calls) == before and not updates(db)


def test_cas_failure_rolls_back_and_never_confirms_commit(setup):
    db, root = setup
    db.cas_denied = True
    with pytest.raises(fence.FenceError, match='CAS_FAILED'):
        run_open(db)
    assert db.month['limit_tokens'] == 1327808
    assert fence._read(root, 'open-after') is None


@pytest.mark.parametrize('action', ['open', 'close'])
def test_lost_commit_ack_recovers_once_without_second_update(setup, action):
    db, root = setup
    if action == 'close':
        run_open(db)
    db.commit_ack_lost = True
    execute = run_open if action == 'open' else run_close
    with pytest.raises(ConnectionError, match='ACK_LOST'):
        execute(db)
    assert fence._read(root, action + '-precommit')['commit_confirmed'] is False
    assert fence._read(root, action + '-after') is None
    count = len(updates(db))
    recovered = execute(db)
    assert recovered['commit_confirmed'] is True and len(updates(db)) == count


def test_precommit_file_failure_rolls_back_then_retry_uses_same_baseline(setup, monkeypatch):
    db, root = setup
    write = fence._write

    def fail(root, name, value):
        if name == 'open-precommit':
            raise OSError('SIMULATED_E_DISK_FAILURE')
        return write(root, name, value)

    monkeypatch.setattr(fence, '_write', fail)
    with pytest.raises(OSError):
        run_open(db)
    assert db.month['limit_tokens'] == 1327808
    assert fence._read(root, 'open-after') is None
    monkeypatch.setattr(fence, '_write', write)
    assert run_open(db)['after']['month']['limit_tokens'] == 1467808


@pytest.mark.parametrize('condition', ['reserved', 'historical_changed', 'historical_deleted',
                                      'grant', 'receipt_tampered', 'limit', 'fully_used'])
def test_close_denies_untrusted_or_unsettled_state_retains_every_row(setup, condition):
    db, root = setup
    run_open(db)
    if condition == 'reserved':
        db.calls.append(dict(call_id='active', state='reserved', actual_total=None, held_tokens=100))
    elif condition == 'historical_changed':
        db.calls[1]['held_tokens'] -= 1
    elif condition == 'historical_deleted':
        db.calls.pop()
    elif condition == 'grant':
        intent = fence._read(root, 'intent')
        intent['grant_id'] = 'other'
        (root / 'intent.json').unlink()
        fence._write(root, 'intent', intent)
    elif condition == 'receipt_tampered':
        path = root / 'open-after.json'
        path.write_text(path.read_text().replace('P14-20261016-SF-140000', 'different'), encoding='utf-8')
    elif condition == 'limit':
        db.month['limit_tokens'] += 1
    elif condition == 'fully_used':
        db.calls.append(dict(call_id='p14-new', state='unknown', actual_total=None, held_tokens=266910))
    before = deepcopy((db.month, db.calls))
    count = len(updates(db))
    with pytest.raises(fence.FenceError):
        run_close(db)
    assert before == (db.month, db.calls) and len(updates(db)) == count


def test_approval_conflict_or_missing_never_opens_second_grant(setup):
    db, root = setup
    with pytest.raises(fence.FenceError, match='APPROVAL_REQUIRED'):
        run_open(db, '')
    assert not db.sql
    run_open(db)
    with pytest.raises(fence.FenceError, match='APPROVAL_CONFLICT'):
        run_open(db, 'different approval')
    assert len(updates(db)) == 1


def test_evidence_file_never_overwritten(setup):
    _, root = setup
    fence._write(root, 'example', {'one': 1})
    original = (root / 'example.json').read_bytes()
    with pytest.raises(fence.FenceError, match='IMMUTABLE_RECEIPT_CONFLICT'):
        fence._write(root, 'example', {'one': 2})
    assert (root / 'example.json').read_bytes() == original
    assert not list(root.glob('*.tmp'))


def test_non_e_volume_rejected_without_connecting(tmp_path, monkeypatch):
    monkeypatch.setattr(fence, 'FENCE_DIR', PathLikeWrongDrive(tmp_path))
    with pytest.raises(fence.FenceError, match='MUST_BE_ON_E'):
        fence._evidence_root()


class PathLikeWrongDrive:
    drive = 'C:'

    def __init__(self, ignored):
        pass

    def resolve(self):
        return self


def test_cli_error_does_not_print_dsn_or_raw_exception(monkeypatch, capsys):
    def fail():
        raise RuntimeError('postgresql://credential-must-not-print@127.0.0.1')

    monkeypatch.setattr(fence, '_evidence_root', fail)
    assert fence.main(['close']) == 1
    captured = capsys.readouterr()
    assert 'credential' not in captured.err and 'postgresql' not in captured.err
    assert 'P14_OPERATION_FAILED_RuntimeError' in captured.err
