"""Reviewed synthetic text batches. Import/construct never registers or enables egress.

Trust is supplied by a source-reviewed task policy, never a runtime manifest.
No credentials, bootstrap, database, scheduler or transport lives in this module.
"""
import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from backend.app.application.session_attempts import CANONICAL_LEDGER
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.application.usd_pricing import input_upper_bound
from backend.app.ports.session_attempts import AttemptDenied


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ReviewedBatchSpec:
    batch_id: str
    sha256: str
    content_sha256: str
    case_identities: tuple


@dataclass(frozen=True)
class ReviewedTaskPolicy:
    comparison_id: str
    batches: tuple
    provider: str = 'deepseek'
    model: str = 'deepseek-flash'
    output_cap: int = 512
    attempt_limit: int = 12
    token_limit: int = 32768
    batch_attempt_limit: int = 4
    batch_token_limit: int = 12288
    timeout_seconds: int = 30
    temperature: int = 0
    stream: bool = False
    retry_count: int = 0


# Explicit task configuration: adding another experiment requires task authorization
# and independent review of its exact digests/identities. Manifest fields cannot add it.
_TASK = ReviewedTaskPolicy('RAG-CLOUD12-CONTRAST-PREP-01-rev1', (
    ReviewedBatchSpec('cloud12-512-synthetic-20261001-b01-rev1', '5fce416ed04f8ff2d0507ae5c65eac1beb04b18cf75b36b124587800fb1ae7a3', '0c84f31cb645e1ebb78e0acaa6386a7bcd3fb05c7d06353f61bf5b676e324afa', (('Q01', 'rag-request-v1:ef583a1cfe0b90b89fc75c7d434bab9b54cc337f3e8ff089db41026b339b9ada'), ('Q02', 'rag-request-v1:5c91ba54e1c3346e69914b3b5d4b131bab19336380c0837c0fe902ab2102b2fe'), ('Q03', 'rag-request-v1:5e39673973516ef6b3cec32f2ec4780318c439decdfe6f911525bfa9521cad70'), ('Q04', 'rag-request-v1:7badd0ed87e5f31e8111f51d254db11291f40457028a1d742161d6ad522c834c'))),
    ReviewedBatchSpec('cloud12-512-synthetic-20261001-b02-rev1', 'bfa5e54863c293bc37b9eb168b45ce380f9b934286aed527f7d8c29e6e4f07bc', '0c656df62a2c40577cb8260d829dcfeadf9a911daa2d7cbd5a31891dc62162ff', (('Q05', 'rag-request-v1:502decd5c0395a02ecaa8cebc937418a69fb6dcf488fe81e7233ee808cd0d82f'), ('Q06', 'rag-request-v1:ed27464018380d130161d274486a73c11a7e5968110c4dc7084ca5ac06a0fd97'), ('Q07', 'rag-request-v1:71a20ac67a2a094a8ae95655546005051e27d11077a5a26fc654226a36e80c5e'), ('Q08', 'rag-request-v1:64f3e2fd359384080de808da8f07e423cff757eadde5a3ab4eaf065830732f8d'))),
    ReviewedBatchSpec('cloud12-512-synthetic-20261001-b03-rev1', '743656276fedb02aa5822322f2746450c0041ae17b9b82dabf3eee948e6df8f4', 'd9a315372cf7dee56ec3508947bd01c53dcee89253c348de77d8be83a9c05de1', (('Q09', 'rag-request-v1:f0381af9437866fb4864e27ce845e6921fd3a8bfae7de2ca73e1459ceb06d540'), ('Q10', 'rag-request-v1:da1b635e2e7b6fba2f7688bf1855e1b4d57e0e45afbf885a8e8a30d363187952'), ('Q11', 'rag-request-v1:2d2d1810659652d2303f1368923a4321f8eaa125b2d18c5bfb1466ac42e90bf7'), ('Q12', 'rag-request-v1:8ccc5bafe7490d0ee2c32813b46a2c060f71bbe4386ff996d420f6b76c6289f7'))),
))
_TRUSTED_TASKS = MappingProxyType({_TASK.comparison_id: _TASK})
_POLICY_DIGESTS = MappingProxyType({_TASK.comparison_id: _digest(asdict(_TASK))})


def reviewed_task_policy(comparison_id):
    try:
        policy = _TRUSTED_TASKS[comparison_id]
        _trusted(policy)
        return policy
    except (KeyError, TypeError):
        raise AttemptDenied('REVIEWED_TASK_UNTRUSTED') from None


def _trusted(policy):
    if (type(policy) is not ReviewedTaskPolicy
            or _TRUSTED_TASKS.get(policy.comparison_id) is not policy
            or _digest(asdict(policy)) != _POLICY_DIGESTS.get(policy.comparison_id)):
        raise AttemptDenied('REVIEWED_TASK_UNTRUSTED')
    # Normalize tuple-valued frozen config to its persisted JSON representation.
    return json.loads(json.dumps(asdict(policy)))


class ReviewedImmutableBatchGate(ValidationAttemptGate):
    allowed_output_caps = (512,)

    def __init__(self, ledger_path=CANONICAL_LEDGER, *, task_policy, batch_id,
                 scope_path, case_id):
        self.task_policy = task_policy
        self._trusted_policy()
        self.batch_id = batch_id
        self.batch_spec = next((b for b in task_policy.batches if b.batch_id == batch_id), None)
        if self.batch_spec is None:
            raise AttemptDenied('REVIEWED_BATCH_UNTRUSTED')
        self.scope_path = Path(scope_path)
        self.manifest = self._load_manifest()
        self.case = next((c for c in self.manifest['cases'] if c['id'] == case_id), None)
        if self.case is None:
            raise AttemptDenied('REVIEWED_CASE_UNREGISTERED')
        super().__init__(ledger_path, model=task_policy.model,
                         input_tokens_cap=self.case['input_cap'], max_output_tokens=task_policy.output_cap)
        self._canonical()

    def _trusted_policy(self):
        return _trusted(self.task_policy)

    def _policy_digest(self):
        self._trusted_policy()
        return _POLICY_DIGESTS[self.task_policy.comparison_id]

    def _canonical(self):
        if self.path.resolve() != CANONICAL_LEDGER.resolve():
            raise AttemptDenied('REVIEWED_CANONICAL_LEDGER_REQUIRED')

    def _validate_manifest(self, manifest, spec):
        p = self.task_policy
        if (_digest(manifest) != spec.content_sha256
                or manifest.get('parent_comparison_id') != p.comparison_id
                or manifest.get('batch_id') != spec.batch_id
                or manifest.get('synthetic_only') is not True
                or manifest.get('model') != p.model or manifest.get('output_cap') != p.output_cap
                or manifest.get('attempt_limit') != p.batch_attempt_limit
                or manifest.get('token_bound_planned') != p.batch_token_limit
                or manifest.get('parent_attempt_limit') != p.attempt_limit
                or manifest.get('parent_token_bound_planned') != p.token_limit):
            raise AttemptDenied('REVIEWED_MANIFEST_CHANGED')
        for c in manifest['cases']:
            if (c['messages'] != [{'role': 'user', 'content': c['prompt']}]
                    or hashlib.sha256(c['prompt'].encode('utf-8')).hexdigest() != c['prompt_sha256']
                    or c['input_cap'] != input_upper_bound(c['prompt']) or c['max_output_tokens'] != p.output_cap):
                raise AttemptDenied('REVIEWED_MANIFEST_CHANGED')
        if tuple((c['id'], c['request_id']) for c in manifest['cases']) != spec.case_identities:
            raise AttemptDenied('REVIEWED_MANIFEST_CHANGED')
        return manifest

    def _load_manifest(self):
        self._trusted_policy()
        # Caller-mutated batch_spec cannot create a new trusted pair.
        spec = next((b for b in self.task_policy.batches if b.batch_id == self.batch_id), None)
        if spec != self.batch_spec:
            raise AttemptDenied('REVIEWED_BATCH_UNTRUSTED')
        try:
            raw = self.scope_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != spec.sha256:
                raise AttemptDenied('REVIEWED_MANIFEST_CHANGED')
            return self._validate_manifest(json.loads(raw), spec)
        except (OSError, ValueError, TypeError, KeyError):
            raise AttemptDenied('REVIEWED_MANIFEST_UNAVAILABLE') from None

    def _frozen(self):
        self._canonical()
        m = self._load_manifest()
        if (m != self.manifest or self.case not in m['cases'] or self.model != self.task_policy.model
                or self.input_cap != self.case['input_cap'] or self.output_cap != self.task_policy.output_cap):
            raise AttemptDenied('REVIEWED_MANIFEST_CHANGED')
        return m

    def _task_entry(self, data):
        policy = self._trusted_policy()
        entry = data.get('reviewed_immutable_tasks', {}).get(self.task_policy.comparison_id)
        if (not isinstance(entry, dict) or entry.get('policy') != policy
                or entry.get('policy_sha256') != self._policy_digest()
                or not isinstance(entry.get('batches'), dict)):
            raise AttemptDenied('REVIEWED_REGISTRY_CHANGED')
        specs = {b.batch_id: b for b in self.task_policy.batches}
        for bid, batch in entry['batches'].items():
            if (bid not in specs or not isinstance(batch, dict) or type(batch.get('enabled')) is not bool
                    or batch.get('sha256') != specs[bid].sha256):
                raise AttemptDenied('REVIEWED_REGISTRY_CHANGED')
            try:
                self._validate_manifest(batch['manifest'], specs[bid])
            except (KeyError, TypeError, ValueError, AttemptDenied):
                raise AttemptDenied('REVIEWED_REGISTRY_CHANGED') from None
        return entry

    def _entry(self, data):
        self._frozen()
        entry = self._task_entry(data)['batches'].get(self.batch_id)
        if entry is None or entry['manifest'] != self.manifest:
            raise AttemptDenied('REVIEWED_REGISTRY_CHANGED')
        return entry

    def register_disabled_scope(self):
        self._frozen()
        with self._locked():
            data = self._read()
            self._used(data)
            if data.get('calls_allowed_in_this_task') is not False or data['reserved_attempts']:
                raise AttemptDenied('REVIEWED_REGISTRATION_REQUIRES_DISABLED')
            tasks = data.setdefault('reviewed_immutable_tasks', {})
            tid = self.task_policy.comparison_id
            if tid in tasks:
                task = self._task_entry(data)
                if any(b['enabled'] for b in task['batches'].values()):
                    raise AttemptDenied('REVIEWED_REGISTRATION_REQUIRES_DISABLED')
                if self.batch_id in task['batches']:
                    self._entry(data)
                    return
            identities = {rid for _, rid in self.batch_spec.case_identities}
            if any(a.get('request_id') in identities for a in data['attempts']):
                raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            # All known validation registries share the same identity namespace.
            for old in data.get('static_pair_scopes', {}).values():
                if any(c['request_id'] in identities for c in old['manifest']['cases']):
                    raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            for old in tasks.values():
                for batch in old['batches'].values():
                    if any(c['request_id'] in identities for c in batch['manifest']['cases']):
                        raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            if tid not in tasks:
                tasks[tid] = {'policy': self._trusted_policy(),
                              'policy_sha256': self._policy_digest(), 'batches': {}}
            tasks[tid]['batches'][self.batch_id] = {'sha256': self.batch_spec.sha256,
                                                  'manifest': self.manifest, 'enabled': False}
            self._write(data)

    def validate_request(self, prompt, timeout_seconds, max_tokens, *, model):
        self._frozen()
        if (prompt != self.case['prompt'] or model != self.model or type(max_tokens) is not int
                or max_tokens != self.task_policy.output_cap
                or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= self.task_policy.timeout_seconds):
            raise AttemptDenied('REVIEWED_REQUEST_MISMATCH')

    def _rows(self, data):
        rows = [a for a in data['attempts']
                if a.get('reviewed_comparison') == self.task_policy.comparison_id]
        specs = {b.batch_id: b for b in self.task_policy.batches}
        for r in rows:
            spec = specs.get(r.get('reviewed_batch'))
            if (spec is None or r.get('scope_sha256') != spec.sha256
                    or (r.get('case_id'), r.get('request_id')) not in spec.case_identities
                    or r.get('reviewed_policy_sha256') != self._policy_digest()):
                raise AttemptDenied('REVIEWED_REGISTRY_CHANGED')
        return rows

    @staticmethod
    def _reserved(rows):
        return sum(r['validation_tokens']['reserved_input'] + r['validation_tokens']['reserved_output'] for r in rows)

    @staticmethod
    def _uncertain(rows):
        return any(r['status'] in ('unknown', 'truncated')
                   or r['status'] == 'ok' and r['validation_tokens']['state'] != 'settled' for r in rows)

    def _admit(self, data, request_id):
        if not self._entry(data)['enabled']:
            raise AttemptDenied('REVIEWED_SCOPE_DISABLED')
        if request_id != self.case['request_id']:
            raise AttemptDenied('REVIEWED_REQUEST_ID_MISMATCH')
        rows = self._rows(data)
        if self._uncertain(rows):
            raise AttemptDenied('REVIEWED_PREVIOUS_ATTEMPT_UNCERTAIN')
        batch = [r for r in rows if r['reviewed_batch'] == self.batch_id]
        if len(rows) >= self.task_policy.attempt_limit or len(batch) >= self.task_policy.batch_attempt_limit:
            raise AttemptDenied('REVIEWED_ATTEMPT_LIMIT')
        bound = self.input_cap + self.output_cap
        if (self._reserved(rows) + bound > self.task_policy.token_limit
                or self._reserved(batch) + bound > self.task_policy.batch_token_limit):
            raise AttemptDenied('REVIEWED_TOKEN_BOUND')

    def _reservation_fields(self, data):
        return {'reviewed_comparison': self.task_policy.comparison_id, 'reviewed_batch': self.batch_id,
                'reviewed_policy_sha256': self._policy_digest(),
                'scope_sha256': self.batch_spec.sha256, 'case_id': self.case['id'],
                'prompt_sha256': self.case['prompt_sha256'], 'context_sha256': self.case['context_sha256'],
                'messages_sha256': self.case['messages_sha256'], 'retry_count': 0}

    @staticmethod
    def _active_scope(data):
        # A rejected/stale caller must not revoke another execution's entry.
        for task in data.get('reviewed_immutable_tasks', {}).values():
            if isinstance(task, dict):
                for batch in task.get('batches', {}).values():
                    if isinstance(batch, dict) and batch.get('enabled') is True:
                        return True
        return any(isinstance(scope, dict) and scope.get('enabled') is True
                   for scope in data.get('static_pair_scopes', {}).values())

    def _close_execution(self, data, comparison_id, owner):
        """Called only with the canonical lock; changes entry flags, never usage."""
        changed = False
        task = data.get('reviewed_immutable_tasks', {}).get(comparison_id)
        entry = task.get('batches', {}).get(self.batch_id) if isinstance(task, dict) else None
        if isinstance(entry, dict) and entry.get('execution_owner') == owner:
            entry['enabled'] = False
            entry.pop('execution_owner')
            changed = True
        if not self._active_scope(data) and data.get('calls_allowed_in_this_task') is not False:
            data['calls_allowed_in_this_task'] = False
            changed = True
        if changed:
            self._write(data)

    @contextmanager
    def execution_scope(self):
        """Explicit activation with locked failure closure and owned exit cleanup.

        Registration/live authorization remain separate. A rejected attempt cannot
        close a different active owner. Pending/unknown usage is never released.
        """
        comparison_id = self.task_policy.comparison_id
        owner = uuid.uuid4().hex
        with self._locked():
            data = None
            try:
                data = self._read()
                used = self._used(data)
                task = self._task_entry(data)
                entry = self._entry(data)
                if (data.get('calls_allowed_in_this_task') is not True or data['reserved_attempts']
                        or self._active_scope(data)
                        or set(task['batches']) != {b.batch_id for b in self.task_policy.batches}):
                    raise AttemptDenied('REVIEWED_ACTIVATION_DENIED')
                if data.get('validation_blocked_reason'):
                    raise AttemptDenied('VALIDATION_USAGE_BOUND_EXCEEDED')
                rows = self._rows(data)
                if self._uncertain(rows):
                    raise AttemptDenied('REVIEWED_PREVIOUS_ATTEMPT_UNCERTAIN')
                consumed = {r['request_id'] for r in rows}
                remaining = [c for b in task['batches'].values() for c in b['manifest']['cases']
                             if c['request_id'] not in consumed]
                available = [c for c in entry['manifest']['cases'] if c['request_id'] not in consumed]
                if not available or len(rows) >= self.task_policy.attempt_limit:
                    raise AttemptDenied('REVIEWED_ATTEMPT_LIMIT')
                if used + sum(c['input_cap'] + self.task_policy.output_cap for c in remaining) > 1_000_000:
                    raise AttemptDenied('VALIDATION_TOKEN_CAP')
                if data['consumed_attempts'] + len(remaining) > data['authorized_limit']:
                    raise AttemptDenied('SESSION_CALL_LIMIT')
                entry['enabled'] = True
                entry['execution_owner'] = owner
                self._write(data)
            except BaseException:
                # Failure before yield is still inside the lock: no stale snapshot
                # can overwrite a concurrent reservation/activation during cleanup.
                if data is not None:
                    self._close_execution(data, comparison_id, owner)
                raise
        try:
            yield self
        finally:
            with self._locked():
                # Re-read under lock and close only this activation's owner token.
                self._close_execution(self._read(), comparison_id, owner)
