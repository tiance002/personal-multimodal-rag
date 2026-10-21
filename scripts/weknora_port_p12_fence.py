"""One P12-only local SiliconFlow fence increase and mandatory closeout.

This is an explicitly invoked experiment operation, never a sender or a normal
admission path. It changes only one existing month's limit. No key is read and
no provider request is made. Receipts are immutable and live on E:.

Usage: python -B -m scripts.weknora_port_p12_fence open --approval-file <UTF8 text>
       python -B -m scripts.weknora_port_p12_fence close
Stop the P12 API/workers before close; an outstanding reservation denies close.
"""
import argparse
import asyncio
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.weknora_port.storage.token_budget import encode

GRANT_ID = 'P12-20261016-SF-200000'
ACCOUNT = 'siliconflow-owner-main'
PROVIDER = 'siliconflow'
MONTH = date(2026, 10, 1)
KEY = (ACCOUNT, PROVIDER, MONTH)
SYSTEM_ID = '7696088403504697382'
DATABASE = 'weknora_python_port_20261013'
OLD_LIMIT = 1_200_898
OPEN_LIMIT = 1_400_898
APPROVAL_SOURCE = 'OWNER:20261016-unrestricted-siliconflow-MVP'
CARRY_SOURCE = 'LEDGER:real/embedding-0:14-preserved-original-bucket'
FENCE_DIR = Path('E:/WK/run/p12-20261016/fence-200000')
LOCK_NAME = 'weknora-token-month:' + encode([str(v) for v in KEY])
AUTHORITY = dict(account_id=ACCOUNT, provider=PROVIDER, month=str(MONTH),
                 carry_in_tokens=14, carry_status='KNOWN', carry_source=CARRY_SOURCE,
                 approval_source=APPROVAL_SOURCE, blocked_reason='')


class FenceError(RuntimeError):
    """Safe public error code, never connection details."""


def require(condition, code):
    if not condition:
        raise FenceError(code)


def plain(value):
    return json.loads(json.dumps(value, default=str))


def digest(value):
    return hashlib.sha256(encode(plain(value)).encode('utf-8')).hexdigest()


def _evidence_root():
    root = FENCE_DIR.resolve()
    require(root.drive.upper() == 'E:', 'P12_EVIDENCE_MUST_BE_ON_E')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read(root, name):
    path = root / (name + '.json')
    if not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding='utf-8'))
        require(envelope['sha256'] == digest(envelope['data']), 'P12_RECEIPT_HASH_MISMATCH')
        return envelope['data']
    except (ValueError, KeyError, TypeError) as exc:
        raise FenceError('P12_RECEIPT_INVALID') from exc


def _write(root, name, value):
    """fsync an E: temporary file, then atomically link without overwriting."""
    value = plain(value)
    existing = _read(root, name)
    if existing is not None:
        require(existing == value, 'P12_IMMUTABLE_RECEIPT_CONFLICT')
        return existing
    target = root / (name + '.json')
    temporary = root / ('.' + name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write((encode(dict(data=value, sha256=digest(value))) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            require(_read(root, name) == value, 'P12_IMMUTABLE_RECEIPT_CONFLICT')
    finally:
        temporary.unlink(missing_ok=True)
    return value


def _intent(root, approval_text=None):
    expected = dict(schema='weknora-p12-fence-intent/v1', grant_id=GRANT_ID,
                    account=AUTHORITY, system_identifier=SYSTEM_ID, database=DATABASE,
                    old_limit=OLD_LIMIT, open_limit=OPEN_LIMIT, increment=200_000,
                    scope='P12 current round only; unused headroom closes; DeepSeek prohibited')
    existing = _read(root, 'intent')
    if existing is not None:
        require(all(existing.get(k) == v for k, v in expected.items()), 'P12_GRANT_MISMATCH')
        require(bool(existing.get('approval_text', '').strip()), 'P12_APPROVAL_REQUIRED')
        if approval_text is not None:
            require(existing['approval_text'] == approval_text, 'P12_APPROVAL_CONFLICT')
        return existing
    require(isinstance(approval_text, str) and bool(approval_text.strip()), 'P12_APPROVAL_REQUIRED')
    return _write(root, 'intent', dict(expected, approval_text=approval_text,
                  recorded_at=datetime.now(timezone.utc).isoformat()))


async def _one(conn, sql, params=()):
    return await (await conn.execute(sql, params)).fetchone()


async def _locked(conn):
    from psycopg.pq import TransactionStatus
    require(conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE,
            'P12_OWN_TRANSACTION_REQUIRED')


async def _identity_and_lock(conn):
    await conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
    identity = await _one(conn, 'SELECT system_identifier::text AS system_identifier, current_database() AS database FROM pg_control_system()')
    require(identity == dict(system_identifier=SYSTEM_ID, database=DATABASE), 'P12_PG_IDENTITY_MISMATCH')
    await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (LOCK_NAME,))
    clock = await _one(conn, "SELECT (date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC'))::date AS month")
    require(clock['month'] == MONTH, 'P12_MONTH_CHANGED')


async def _snapshot(conn):
    month = await _one(conn, 'SELECT * FROM port_token_budget_months WHERE account_id=%s AND provider=%s AND month=%s FOR UPDATE', KEY)
    require(month is not None, 'P12_EXISTING_MONTH_REQUIRED')
    rows = await (await conn.execute('SELECT * FROM port_token_calls WHERE account_id=%s AND budget_provider=%s AND month=%s ORDER BY call_id', KEY)).fetchall()
    used = 0
    for row in rows:
        require(row['state'] != 'reserved', 'P12_RESERVED_CALLS_EXIST')
        require(row['state'] in ('settled', 'unknown', 'released'), 'P12_INVALID_CALL_STATE')
        amount = row['actual_total'] if row['state'] == 'settled' else row['held_tokens'] if row['state'] == 'unknown' else 0
        require(isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0, 'P12_INVALID_OCCUPANCY')
        used += amount
    month = plain(month)
    require(all(month.get(k) == v for k, v in AUTHORITY.items()), 'P12_MONTH_AUTHORITY_MISMATCH')
    entries = [dict(call_id=row['call_id'], state=row['state'], sha256=digest(row)) for row in rows]
    return dict(month=month, calls=entries, call_count=len(entries), ledger_sha256=digest(rows),
                used_tokens=used, occupied_with_carry=used + 14,
                headroom=month['limit_tokens'] - used - 14)


def _preserved(before, after, *, exact=False):
    old = {entry['call_id']: entry for entry in before['calls']}
    new = {entry['call_id']: entry for entry in after['calls']}
    require(all(new.get(key) == value for key, value in old.items()), 'P12_HISTORICAL_CALL_CHANGED')
    if exact:
        require(before['ledger_sha256'] == after['ledger_sha256'] and old == new, 'P12_CALLS_CHANGED_DURING_OPERATION')


async def _change(conn, before, limit):
    row = await _one(conn,
        'UPDATE port_token_budget_months SET limit_tokens=%s WHERE account_id=%s AND provider=%s AND month=%s '
        'AND limit_tokens=%s AND carry_in_tokens=%s AND carry_status=%s AND carry_source=%s '
        'AND approval_source=%s AND blocked_reason=%s RETURNING *',
        (limit, *KEY, before['month']['limit_tokens'], 14, 'KNOWN', CARRY_SOURCE, APPROVAL_SOURCE, ''))
    require(row is not None, 'P12_LIMIT_CAS_FAILED')
    after = await _snapshot(conn)
    require(after['month'] == dict(before['month'], limit_tokens=limit), 'P12_NONLIMIT_FIELD_CHANGED')
    _preserved(before, after, exact=True)
    return after


def _receipt(action, intent, before, after):
    return dict(schema='weknora-p12-fence-receipt/v1', grant_id=GRANT_ID,
                action=action, intent_sha256=digest(intent), before_sha256=digest(before),
                before=before, after=after, commit_confirmed=False)


def _check_receipt(receipt, action, intent):
    require(receipt is not None and receipt.get('grant_id') == GRANT_ID
            and receipt.get('action') == action and receipt.get('intent_sha256') == digest(intent)
            and receipt.get('before_sha256') == digest(receipt.get('before')), 'P12_GRANT_RECEIPT_MISMATCH')


async def open_fence(*, approval_text, connection_factory=None):
    """Explicit one-time 200k increase; a retry cannot increment it again."""
    root = _evidence_root()
    intent = _intent(root, approval_text)
    require(_read(root, 'close-after') is None and _read(root, 'close-before') is None,
            'P12_GRANT_ALREADY_CLOSING_OR_CLOSED')
    if connection_factory is None:
        from scripts.weknora_port_mvp_real import connection as connection_factory
    conn = await connection_factory()
    try:
        await _locked(conn)
        async with conn.transaction():
            await _identity_and_lock(conn)
            current = await _snapshot(conn)
            completed = _read(root, 'open-after')
            if completed is not None:
                _check_receipt(completed, 'open', intent)
                require(completed['commit_confirmed'] is True and current['month']['limit_tokens'] == OPEN_LIMIT,
                        'P12_OPEN_STATE_CONFLICT')
                _preserved(completed['before'], current)
                return completed
            before = _read(root, 'open-before')
            prepared = _read(root, 'open-precommit')
            if current['month']['limit_tokens'] == OPEN_LIMIT:
                _check_receipt(prepared, 'open', intent)
                require(before == prepared['before'], 'P12_OPEN_RECOVERY_CONFLICT')
                _preserved(before, current)
                receipt = prepared
            else:
                require(current['month']['limit_tokens'] == OLD_LIMIT and current['headroom'] >= 0,
                        'P12_ORIGINAL_LIMIT_REQUIRED')
                if before is not None:
                    require(before == current, 'P12_OPEN_BASELINE_CHANGED')
                before = _write(root, 'open-before', current)
                after = await _change(conn, before, OPEN_LIMIT)
                receipt = _write(root, 'open-precommit', _receipt('open', intent, before, after))
        # A precommit file alone never claims a confirmed database commit.
        return _write(root, 'open-after', dict(receipt, commit_confirmed=True))
    finally:
        await conn.close()


async def close_fence(*, connection_factory=None):
    """Contract to occupied+carry, retaining every UNKNOWN and historical call."""
    root = _evidence_root()
    intent = _intent(root)
    opened = _read(root, 'open-after')
    _check_receipt(opened, 'open', intent)
    require(opened['commit_confirmed'] is True, 'P12_OPEN_COMMIT_REQUIRED')
    if connection_factory is None:
        from scripts.weknora_port_mvp_real import connection as connection_factory
    conn = await connection_factory()
    try:
        await _locked(conn)
        async with conn.transaction():
            await _identity_and_lock(conn)
            current = await _snapshot(conn)
            _preserved(opened['before'], current)
            completed = _read(root, 'close-after')
            if completed is not None:
                _check_receipt(completed, 'close', intent)
                require(completed['commit_confirmed'] is True and completed['after'] == current,
                        'P12_CLOSED_STATE_CONFLICT')
                return completed
            before = _read(root, 'close-before')
            prepared = _read(root, 'close-precommit')
            if current['month']['limit_tokens'] != OPEN_LIMIT:
                _check_receipt(prepared, 'close', intent)
                require(before == prepared['before'] and current == prepared['after'], 'P12_CLOSE_RECOVERY_CONFLICT')
                receipt = prepared
            else:
                require(current['occupied_with_carry'] < OPEN_LIMIT, 'P12_NO_LIMIT_CONTRACTION')
                if before is not None:
                    require(before == current, 'P12_CLOSE_BASELINE_CHANGED')
                before = _write(root, 'close-before', current)
                after = await _change(conn, before, before['occupied_with_carry'])
                require(after['headroom'] == 0 and after['month']['limit_tokens'] != OPEN_LIMIT,
                        'P12_CLOSE_HEADROOM_NOT_ZERO')
                receipt = _write(root, 'close-precommit', _receipt('close', intent, before, after))
        return _write(root, 'close-after', dict(receipt, commit_confirmed=True))
    finally:
        await conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    opening = commands.add_parser('open')
    opening.add_argument('--approval-file', type=Path, required=True,
                         help='Owner approval text for this P12-only 200000 increment; no credentials')
    commands.add_parser('close')
    args = parser.parse_args(argv)
    try:
        # psycopg async connections on Windows require a Selector loop.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            if args.action == 'open':
                receipt = runner.run(open_fence(approval_text=args.approval_file.read_text(encoding='utf-8-sig')))
            else:
                receipt = runner.run(close_fence())
        print(encode(dict(action=args.action, grant_id=GRANT_ID, status='PASS',
              limit_tokens=receipt['after']['month']['limit_tokens'],
              headroom=receipt['after']['headroom'], receipt=str(FENCE_DIR / (args.action + '-after.json')))))
        return 0
    except Exception as exc:
        # No raw exception/traceback: connection errors can contain credentials.
        code = str(exc) if isinstance(exc, FenceError) else 'P12_OPERATION_FAILED_' + type(exc).__name__
        print(encode(dict(status='FAIL', code=code)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
