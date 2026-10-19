"""Single-run finite ingestion counter; never replaces LocalExecution or SendGate.

Install on the existing runner module before invoking run_ingestion. All files
are fully checked at startup and after success. Between documents, only the
immutable parent and last active ledger are read. The runner's existing lock
must remain in use; this adapter provides no independent authorization.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from backend.weknora_port.execution import _validate_ledger


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def _require(ok, code):
    if not ok:
        raise ValueError(code)


def _contract(data):
    return {k: v for k, v in data.items() if k not in ('entries', 'status')}


class ExecutionCounter:
    def __init__(self, module):
        self.module = module
        self.original_parent = module.parent_state
        self.original_verify = module.verify_execution
        self.path = self.plan = self.parent = None
        self.records = {}
        self.rows = {}
        self.active = None
        self.visited = set()
        self.calls = self.tokens = 0
        self.finished = False

    def _ledger(self, filename):
        data = self.module.read(filename)
        _validate_ledger(data)
        _require(data['status'] != 'STOP' and
            all(e['status'] == 'SETTLED' for e in data['entries']),
            'INGESTION_UNKNOWN_OR_INFLIGHT_STOP')
        return data

    @staticmethod
    def _record(data):
        return (len(data['entries']), sum(e['held_tokens'] for e in data['entries']),
            _hash(data['entries']), _hash(_contract(data)))

    def _scope(self, data, row):
        model = {'provider': 'siliconflow', 'model': 'BAAI/bge-m3',
            'model_id': self.module.MODEL_ID, 'capability': 'embedding'}
        _require(data['tenant_id'] == 332 and
            self.module.sha(data['subject_id'].encode()) == self.plan['subject_sha256'] and
            data['allowed_kbs'] == [row['knowledge_base_id']] and
            data['allowed_models'] == [model] and
            '*' in data['allowed_request_prefixes'], 'INGESTION_EXECUTION_SCOPE_DENIED')
        _require(data['max_calls'] == row['embedding_calls_upper'] and
            data['max_tokens'] == row['token_reservation_upper'],
            'INGESTION_FROZEN_FILE_BUDGET_CHANGED')

    def _initialize(self, path, plan):
        parent, calls, tokens = self.original_parent(path, plan)
        self.path, self.plan = Path(path).resolve(), plan
        self.plan_sha = _hash(plan)
        self.parent = copy.deepcopy(parent)
        rows = [r for r in plan['sources'] if r['status'] == 'NEW_SOURCE']
        self.rows = {self.module.source_key(r): copy.deepcopy(r) for r in rows}
        _require(len(rows) == len(self.rows) and set(self.rows) == set(parent['executions']),
            'ROOT_EXACT_FROZEN_SOURCE_SET_REQUIRED')
        allocated_calls = allocated_tokens = 0
        resolved = set()
        for key, filename in parent['executions'].items():
            identity = str(Path(filename).resolve())
            _require(identity not in resolved, 'DUPLICATE_EXECUTION_FILE')
            resolved.add(identity)
            data = self._ledger(filename)
            self._scope(data, self.rows[key])
            self.records[filename] = self._record(data)
            allocated_calls += data['max_calls']
            allocated_tokens += data['max_tokens']
        _require(allocated_calls <= parent['max_calls'] and
            allocated_tokens <= parent['max_tokens'], 'INGESTION_PARENT_PREALLOCATION_DENIED')
        self.calls, self.tokens = calls, tokens
        _require((calls, tokens) == self._totals(self.records), 'INGESTION_COUNTER_START_MISMATCH')

    @staticmethod
    def _totals(records):
        return (sum(r[0] for r in records.values()), sum(r[1] for r in records.values()))

    def _refresh(self):
        if self.active is None:
            return
        filename = self.active
        data = self._ledger(filename)
        prior = self.records[filename]
        current = self._record(data)
        _require(current[3] == prior[3], 'INGESTION_FROZEN_FILE_CONTRACT_CHANGED')
        _require(current[0] >= prior[0] and current[1] >= prior[1],
            'INGESTION_SETTLED_USAGE_ROLLBACK')
        self.calls += current[0] - prior[0]
        self.tokens += current[1] - prior[1]
        self.records[filename] = current
        self.active = None

    def parent_state(self, path, plan):
        _require(not self.finished, 'INGESTION_COUNTER_ALREADY_FINISHED')
        if self.path is None:
            self._initialize(path, plan)
        _require(Path(path).resolve() == self.path and plan is self.plan,
            'INGESTION_COUNTER_JOB_CHANGED')
        # Read the owner file each time: OPEN status, allocation, scope and exact
        # execution mapping cannot be silently renewed during this process.
        parent = self.module.read(path)
        _require(parent == self.parent, 'INGESTION_PARENT_CONTRACT_CHANGED')
        self._refresh()
        _require(self.calls <= parent['max_calls'] and self.tokens <= parent['max_tokens'],
            'INGESTION_PARENT_LIMIT_DENIED')
        return parent, self.calls, self.tokens

    def verify_execution(self, parent, plan, row, principal, calls, tokens):
        _require(self.path is not None and plan is self.plan and parent == self.parent and
            (calls, tokens) == (self.calls, self.tokens), 'INGESTION_COUNTER_JOB_CHANGED')
        key = self.module.source_key(row)
        _require(key in self.rows and row == self.rows[key], 'INGESTION_FROZEN_SOURCE_CHANGED')
        _require(self.active is None and key not in self.visited, 'INGESTION_DUPLICATE_ACTIVE_SOURCE')
        filename = parent['executions'][key]
        data = self._ledger(filename)
        self._scope(data, row)
        _require(self._record(data) == self.records[filename], 'INGESTION_INACTIVE_LEDGER_CHANGED')
        result = self.original_verify(parent, plan, row, principal, calls, tokens)
        self.active = filename
        self.visited.add(key)
        return result

    def finish(self):
        """Full final read, including untouched ledgers; never writes/releases."""
        _require(self.path is not None, 'INGESTION_COUNTER_NOT_STARTED')
        self._refresh()
        _require(_hash(self.plan) == self.plan_sha, 'INGESTION_PLAN_CHANGED')
        parent = self.module.read(self.path)
        expected = copy.deepcopy(self.parent)
        if parent.get('status') == 'CLOSED':
            expected.update(status='CLOSED', actual_calls=self.calls,
                actual_reported_tokens=self.tokens,
                unused_calls_withdrawn=expected['max_calls'] - self.calls,
                unused_token_space_withdrawn=expected['max_tokens'] - self.tokens)
        _require(parent == expected, 'INGESTION_PARENT_CONTRACT_CHANGED')
        final = {}
        for key, filename in parent['executions'].items():
            data = self._ledger(filename)
            self._scope(data, self.rows[key])
            final[filename] = self._record(data)
        _require(final == self.records and self._totals(final) == (self.calls, self.tokens),
            'INGESTION_FINAL_COUNTER_MISMATCH')
        self.finished = True
        return self.calls, self.tokens


def install(module):
    """Adapt the existing runner module in-process; no Gate or ledger mutation."""
    existing = getattr(module, '_s8_execution_counter', None)
    if existing is not None:
        return existing
    counter = ExecutionCounter(module)
    original_run = module.run_ingestion

    async def run_ingestion(args):
        result = await original_run(args)
        counter.finish()
        return result

    module.parent_state = counter.parent_state
    module.verify_execution = counter.verify_execution
    module.run_ingestion = run_ingestion
    module._s8_execution_counter = counter
    return counter
