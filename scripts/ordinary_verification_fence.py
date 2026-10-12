"""Task-only G4 admission fence; never replaces the authoritative service ledger.

Mount this script read-only and use its app factory only after fresh owner
authorization. Normal production startup does not import or activate it.
Every admitted request consumes its full bound permanently in the task journal,
including unsent failures. This deliberately overcounts the 75,000 Token limit.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

SESSION = '313909f3-34d9-4f26-8778-3d5bd27f2c60'
KB = 'd78f3434-0e8f-40bf-afcb-f1c2c311ff51'
DOC = '1fd5a928-d3d7-4bae-aa00-fc74701f61e5'
BATCH = Path('/data/storage/weknora-probes-20261012')
TOKEN_LIMIT = 75000
POLICIES = {'embedding': 'owner-20261011-bge-m3-normal-v1',
            'chat_cheap': 'owner-20261011-xing-normal-v1',
            'chat_expensive': 'owner-20261011-deepseek-normal-v1'}


@contextmanager
def locked(path):
    import fcntl  # Actual execution is Linux; no simulated production lock.
    with path.open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class TaskFence:
    def __init__(self, grant_path, grant_sha256, journal_path, *, batch_root=BATCH):
        raw = grant_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != grant_sha256:
            raise ValueError('OWNER_GRANT_HASH_MISMATCH')
        grant = json.loads(raw)
        if (set(grant) != {'schema_version', 'owner_authorization', 'run_ids',
                           'probe_snapshot', 'acknowledged_unknown_runs'}
                or type(grant['schema_version']) is not int or grant['schema_version'] != 1
                or not isinstance(grant['owner_authorization'], str) or not grant['owner_authorization'].strip()
                or grant['owner_authorization'].startswith('PENDING')
                or type(grant['run_ids']) is not list or len(grant['run_ids']) != 3
                or len(set(grant['run_ids'])) != 3
                or any(str(uuid.UUID(v)) != v for v in grant['run_ids'])
                or not isinstance(grant['probe_snapshot'], dict)
                or not 1 <= len(grant['probe_snapshot']) <= 7
                or type(grant['acknowledged_unknown_runs']) is not list):
            raise ValueError('OWNER_GRANT_INVALID')
        self.grant, self.grant_hash = grant, grant_sha256
        self.batch_root, self.journal = batch_root, journal_path
        self.seed = self.check_snapshot()
        # Missing journal is a failure, not permission to reset occupancy.
        self.read_journal()

    def check_snapshot(self):
        rows = []
        snapshot = self.grant['probe_snapshot']
        if set(p.name for p in self.batch_root.glob('probe-*.json')) != set(snapshot):
            raise ValueError('PROBE_BATCH_CHANGED')
        for name, expected in snapshot.items():
            if not re.fullmatch(r'probe-[A-Za-z0-9_-]+\.json', name):
                raise ValueError('PROBE_SNAPSHOT_INVALID')
            raw = (self.batch_root / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('PROBE_BATCH_CHANGED')
            row = json.loads(raw)
            if (row['state'] not in {'VERIFIED', 'NOT_PROVEN', 'NOT_SENT', 'UNKNOWN'}
                    or any(type(row[k]) is not int or row[k] < 0
                           for k in ('input_reserved', 'output_reserved'))):
                raise ValueError('PROBE_NOT_TERMINAL')
            rows.append(row)
        unknown = {r['run_id'] for r in rows if r['state'] == 'UNKNOWN'}
        if (set(self.grant['acknowledged_unknown_runs']) != unknown
                or set(self.grant['run_ids']) & {r['run_id'] for r in rows}):
            raise ValueError('FRESH_OWNER_DECISION_REQUIRED')
        return sum(r['input_reserved'] + r['output_reserved'] for r in rows)

    def read_journal(self):
        value = json.loads(self.journal.read_bytes())
        if (set(value) != {'grant_sha256', 'entries'}
                or value['grant_sha256'] != self.grant_hash
                or type(value['entries']) is not list):
            raise ValueError('TASK_JOURNAL_INVALID')
        seen = set()
        for row in value['entries']:
            if (set(row) != {'run_id', 'request_id', 'role', 'tokens'}
                    or row['run_id'] not in self.grant['run_ids'] or row['role'] not in POLICIES
                    or type(row['tokens']) is not int or row['tokens'] <= 0
                    or not isinstance(row['request_id'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', row['request_id'])
                    or (row['role'], row['request_id']) in seen):
                raise ValueError('TASK_JOURNAL_INVALID')
            seen.add((row['role'], row['request_id']))
        return value

    def admit(self, *, run_id, request_id, role, policy_id, scope, input_tokens, output_tokens):
        if (run_id not in self.grant['run_ids'] or POLICIES.get(role) != policy_id
                or scope.knowledge_base_ids != frozenset([KB]) or scope.document_ids != frozenset([DOC])
                or not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', request_id)
                or type(input_tokens) is not int or input_tokens <= 0
                or type(output_tokens) is not int or output_tokens < 0):
            raise ValueError('TASK_REQUEST_DENIED')
        with locked(self.journal.with_suffix('.lock')):
            if self.check_snapshot() != self.seed:
                raise ValueError('PROBE_BATCH_CHANGED')
            value = self.read_journal()
            if any(r['role'] == role and r['request_id'] == request_id for r in value['entries']):
                raise ValueError('TASK_REQUEST_ALREADY_ADMITTED')
            tokens = input_tokens + output_tokens
            if self.seed + sum(r['tokens'] for r in value['entries']) + tokens > TOKEN_LIMIT:
                raise ValueError('OWNER_TASK_TOKEN_LIMIT')
            value['entries'].append(dict(run_id=run_id, request_id=request_id, role=role, tokens=tokens))
            temporary = self.journal.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.journal)
            # Persist the replacement directory entry before service reservation/I/O.
            directory = os.open(self.journal.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)


def check_unknown_stop(container, fence):
    # Read only this task's admitted identities, including unlinked rows.
    request_ids = [r['request_id'] for r in fence.read_journal()['entries']]
    if request_ids:
        from sqlalchemy import text, bindparam
        query = text("SELECT COUNT(*) FROM service_token_reservations "
            "WHERE state='unknown' AND request_id IN :requests AND policy_id IN :policies")
        query = query.bindparams(bindparam('requests', expanding=True), bindparam('policies', expanding=True))
        with container.engine.connect() as connection:
            if connection.execute(query, dict(requests=request_ids, policies=list(POLICIES.values()))).scalar_one():
                raise ValueError('OWNER_TASK_UNKNOWN_STOP_REQUIRED')


def install_fence(container, fence):
    """Wrap existing guard instances; preserve every authoritative delegate gate."""
    from backend.app.ports.run_lifecycle import current_execution, execution_owner
    from backend.app.ports.providers import ProviderRequestNotSent
    seen = set()
    for role, binding in container.provider_factory.service_bindings.items():
        guard, original = binding.usage_guard, binding.usage_guard.reserve_prepared
        if id(guard) in seen:
            continue
        seen.add(id(guard))
        def reserve(*, _guard=guard, _original=original, **kwargs):
            try:
                context = current_execution.get()
                if context is None:
                    raise ValueError('TASK_RUN_CONTEXT_REQUIRED')
                claim, lifecycle, lost, _ = context
                if (lost.is_set() or not claim.created or claim.state != 'IN_PROGRESS'
                        or execution_owner.get() != (claim.run_id, claim.owner)
                        or lifecycle.repository.read_run(claim.run_id, SESSION, [KB], [DOC]) is None):
                    raise ValueError('TASK_RUN_CONTEXT_DENIED')
                # Read only this task's admitted identities. Old UNKNOWN stays
                # occupied and acknowledged separately; a new UNKNOWN halts
                # even an automatic expensive fallback before it reserves.
                check_unknown_stop(container, fence)
                fence.admit(run_id=claim.run_id, role=_guard.spec.role, policy_id=_guard.policy.policy_id,
                    **{k: kwargs[k] for k in ('request_id','scope','input_tokens','output_tokens')})
            except Exception:
                raise ProviderRequestNotSent('OWNER_VERIFICATION_ADMISSION_DENIED') from None
            return _original(**kwargs)  # Original ledger, budget, egress and lifecycle gates.
        guard.reserve_prepared = reserve
        original_check = guard.check_before_send
        def check(reservation, _original_check=original_check):
            try:
                check_unknown_stop(container, fence)
            except Exception:
                raise ProviderRequestNotSent('OWNER_VERIFICATION_UNKNOWN_STOP') from None
            return _original_check(reservation)
        guard.check_before_send = check


def allowed_mutation(method, path, run_ids):
    return method == 'POST' and (path == f'/api/v1/conversations/{SESSION}/messages' or
        any(path == f'/api/v1/runs/{run}/cancel' for run in run_ids))


def create_app():
    """Explicit alternate test startup; no installation into the normal service."""
    fence = TaskFence(BATCH / 'web-owner-grant-v1.json', os.environ['RAG_OWNER_VERIFICATION_GRANT_SHA256'],
                      BATCH / 'web-task-admissions-v1.json')
    from backend.app.main import app
    from fastapi.responses import JSONResponse
    install_fence(app.state.container, fence)

    @app.middleware('http')
    async def task_only_mutations(request, call_next):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            if not allowed_mutation(request.method, request.url.path, fence.grant['run_ids']):
                return JSONResponse({'error': {'code': 'OWNER_VERIFICATION_MUTATION_DENIED'}}, status_code=403)
        return await call_next(request)
    return app
