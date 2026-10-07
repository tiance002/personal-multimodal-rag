"""Persistent request-attempt cap. Never stores prompts, responses or credentials."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from backend.app.ports.session_attempts import AttemptDenied

CANONICAL_LEDGER = Path(r'E:\codex_workspace\2026-10-01\task-2\deepseek-call-ledger.json')


class SessionAttemptGate:
    def __init__(self, ledger_path: Path = CANONICAL_LEDGER):
        self.path = Path(ledger_path)

    @contextmanager
    def _locked(self):
        # A separate, stable lock inode survives atomic ledger replacement.
        lock = None
        acquired = False
        try:
            if not self.path.is_file():
                raise AttemptDenied('SESSION_LEDGER_UNAVAILABLE')
            lock = open(str(self.path) + '.lock', 'a+b')
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'0')
                lock.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    lock.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AttemptDenied('SESSION_LEDGER_LOCK_UNAVAILABLE') from None
                    time.sleep(0.01)
            yield
        except AttemptDenied:
            raise
        except (OSError, ValueError, TypeError):
            raise AttemptDenied('SESSION_LEDGER_UNAVAILABLE') from None
        finally:
            if lock is not None:
                if acquired:
                    lock.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                lock.close()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, TypeError):
            raise AttemptDenied('SESSION_LEDGER_INVALID') from None
        return self._validate_v1_data(data)

    @staticmethod
    def _validate_v1_data(data):
        try:
            count, pending, limit = (data[k] for k in ('consumed_attempts', 'reserved_attempts', 'authorized_limit'))
            attempts = data['attempts']
            # History integrity is separate from authorization to send again.
            # A fully consumed historical limit above 50 must remain readable.
            valid = (type(data['schema_version']) is int and data['schema_version'] == 1
                     and data['provider'] == 'deepseek'
                     and all(type(v) is int for v in (count, pending, limit))
                     and 1 <= limit and 0 <= pending <= count <= limit
                     and isinstance(attempts, list) and len(attempts) == count
                     and all(isinstance(a, dict) and isinstance(a.get('attempt_id'), str)
                             and a.get('status') in ('reserved', 'ok', 'unknown', 'truncated') for a in attempts)
                     and len({a['attempt_id'] for a in attempts}) == count
                     and sum(a['status'] == 'reserved' for a in attempts) == pending)
            if not valid:
                raise ValueError()
            return data
        except (OSError, ValueError, TypeError, KeyError):
            raise AttemptDenied('SESSION_LEDGER_INVALID') from None

    def _require_attempt_authorization(self, data):
        # Parsing a larger historical limit is not a grant for new requests.
        # A future reviewed grant needs a separate implementation/activation.
        if data['authorized_limit'] > 50:
            raise AttemptDenied('SESSION_ATTEMPT_AUTHORIZATION_REQUIRED')

    def _write(self, data):
        self._write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

    def _write_bytes(self, raw):
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('xb') as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def _require_attempt_capacity(self, data):
        if data['consumed_attempts'] >= data['authorized_limit']:
            raise AttemptDenied('SESSION_CALL_LIMIT')

    def reserve(self, *, request_id: str | None = None) -> str:
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id)):
            raise AttemptDenied('SESSION_REQUEST_ID_INVALID')
        with self._locked():
            data = self._read()
            if 'validation_policy' in data:
                raise AttemptDenied('VALIDATION_GATE_REQUIRED')
            if data.get('calls_allowed_in_this_task') is not True:
                raise AttemptDenied('SESSION_CALLS_DISABLED')
            self._require_attempt_authorization(data)
            if request_id is not None and any(a.get('request_id') == request_id for a in data['attempts']):
                raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            self._require_attempt_capacity(data)
            attempt_id = uuid.uuid4().hex
            data['consumed_attempts'] += 1
            data['reserved_attempts'] += 1
            data['attempts'].append({'attempt_id': attempt_id, 'status': 'reserved',
                                     'request_id': request_id,
                                     'reserved_utc': datetime.now(timezone.utc).isoformat()})
            self._write(data)
            return attempt_id

    def finish(self, attempt_id: str, status: str) -> None:
        if status not in ('ok', 'unknown', 'truncated'):
            raise AttemptDenied('SESSION_ATTEMPT_STATUS_INVALID')
        with self._locked():
            data = self._read()
            row = next((a for a in data['attempts'] if a['attempt_id'] == attempt_id), None)
            if row is None:
                raise AttemptDenied('SESSION_ATTEMPT_UNKNOWN')
            if row['status'] != 'reserved':
                return
            row.update(status=status, finished_utc=datetime.now(timezone.utc).isoformat())
            data['reserved_attempts'] -= 1
            self._write(data)
