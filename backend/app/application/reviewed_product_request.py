"""One reviewed product Quick request; no manifest, credentials or live activation.

Trust belongs to immutable service startup/source configuration, never request data.
The shipped registry is empty until an exact no-provider capture is independently
reviewed. Tests may install SIMULATED startup configuration without real egress.
Canonical accounting and execution ownership are inherited, not reimplemented.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from types import MappingProxyType

from backend.app.application.reviewed_immutable_batch import (
    ReviewedBatchSpec, ReviewedImmutableBatchGate, ReviewedTaskPolicy, _digest,
)
from backend.app.application.session_attempts import CANONICAL_LEDGER
from backend.app.application.reviewed_grant60 import (
    read_v2, require_current, trusted_for_product,
)
from backend.app.application.usd_pricing import input_upper_bound
from backend.app.domain.scope import Scope
from backend.app.ports.session_attempts import AttemptDenied, request_identity


@dataclass(frozen=True)
class ReviewedProductRequestSpec:
    scope_id: str
    run_id: str
    knowledge_base_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    question_sha256: str
    prompt_sha256: str
    input_cap: int
    output_cap: int = 512
    grant_epoch_id: str | None = None


# Only independently reviewed service/source configuration can populate these.
# No public manifest loader/installer, request authorization or default live scope.
_TRUSTED_PRODUCT_REQUESTS = MappingProxyType({})
_PRODUCT_SPEC_DIGESTS = MappingProxyType({})


def _trusted_product(spec):
    if (type(spec) is not ReviewedProductRequestSpec
            or _TRUSTED_PRODUCT_REQUESTS.get(spec.scope_id) is not spec
            or _digest(asdict(spec)) != _PRODUCT_SPEC_DIGESTS.get(spec.scope_id)):
        raise AttemptDenied('PRODUCT_SCOPE_UNTRUSTED')
    try:
        valid = (str(uuid.UUID(spec.run_id)) == spec.run_id
                 and isinstance(spec.scope_id, str) and bool(spec.scope_id)
                 and all(type(ids) is tuple and bool(ids) and tuple(sorted(set(ids))) == ids
                         and all(isinstance(i, str) and bool(i) for i in ids)
                         for ids in (spec.knowledge_base_ids, spec.document_ids))
                 and all(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h)
                         for h in (spec.question_sha256, spec.prompt_sha256))
                 and type(spec.input_cap) is int and spec.input_cap >= 343
                 and type(spec.output_cap) is int and spec.output_cap in (512, 896)
                 and (spec.grant_epoch_id is None or isinstance(spec.grant_epoch_id, str)
                      and bool(spec.grant_epoch_id))
                 and spec.input_cap + spec.output_cap <= 8192)
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise AttemptDenied('PRODUCT_SCOPE_BOUND_INVALID')
    if spec.grant_epoch_id is not None:
        trusted_for_product(spec.grant_epoch_id, spec.scope_id, _digest(asdict(spec)))
    return spec


def reviewed_product_request(scope_id):
    try:
        return _trusted_product(_TRUSTED_PRODUCT_REQUESTS[scope_id])
    except (KeyError, TypeError):
        raise AttemptDenied('PRODUCT_SCOPE_UNTRUSTED') from None


def _manifest(spec):
    return {'parent_comparison_id': spec.scope_id, 'batch_id': spec.scope_id,
            'synthetic_only': True, 'model': 'deepseek-flash', 'output_cap': spec.output_cap,
            'attempt_limit': 1, 'token_bound_planned': 8192,
            'parent_attempt_limit': 1, 'parent_token_bound_planned': 8192,
            'cases': [{'id': 'Q01', 'request_id': request_identity(spec.run_id, 'quick.answer', 1),
                       'prompt_sha256': spec.prompt_sha256, 'input_cap': spec.input_cap,
                       'max_output_tokens': spec.output_cap,
                       'product_spec_sha256': _digest(asdict(spec))}]}


def _policy(spec):
    manifest = _manifest(spec)
    return ReviewedTaskPolicy(spec.scope_id,
        (ReviewedBatchSpec(spec.scope_id, _digest(asdict(spec)), _digest(manifest),
                           (('Q01', request_identity(spec.run_id, 'quick.answer', 1)),)),),
        output_cap=spec.output_cap, attempt_limit=1, token_limit=8192,
        batch_attempt_limit=1, batch_token_limit=8192)


class ReviewedProductRequestGate(ReviewedImmutableBatchGate):
    allowed_output_caps = (512, 896)

    def __init__(self, ledger_path=CANONICAL_LEDGER, *, scope_id):
        self.product_spec = reviewed_product_request(scope_id)
        self.grant_spec = (trusted_for_product(self.product_spec.grant_epoch_id,
            scope_id, _digest(asdict(self.product_spec)))
            if self.product_spec.grant_epoch_id is not None else None)
        self._grant_owner = None
        policy = _policy(self.product_spec)
        super().__init__(ledger_path, task_policy=policy, batch_id=scope_id,
                         scope_path='.', case_id='Q01')

    def _grant(self):
        spec = _trusted_product(self.product_spec)
        if spec.grant_epoch_id is None:
            if self.grant_spec is not None:
                raise AttemptDenied('GRANT60_UNTRUSTED')
            return None
        grant = trusted_for_product(spec.grant_epoch_id, spec.scope_id, _digest(asdict(spec)))
        if grant is not self.grant_spec:
            raise AttemptDenied('GRANT60_UNTRUSTED')
        return grant

    def _read(self):
        grant = self._grant()
        return super()._read() if grant is None else read_v2(self, grant)

    def _used(self, data):
        used = super()._used(data)
        grant = self._grant()
        if grant is None:
            return used
        # Unknown external usage occupies the full budget until reviewed bounds exist.
        if grant.other_hold_tokens is None:
            return grant.global_token_limit
        used += grant.manual_hold_tokens + grant.other_hold_tokens
        if used > grant.global_token_limit:
            raise AttemptDenied('VALIDATION_TOKEN_CAP')
        return used

    def _require_attempt_authorization(self, data):
        grant = self._grant()
        if grant is None:
            return super()._require_attempt_authorization(data)
        require_current(grant)

    def _require_attempt_capacity(self, data):
        grant = self._grant()
        if grant is None:
            return super()._require_attempt_capacity(data)
        if data['grant60']['consumed_attempts'] >= grant.max_attempts:
            raise AttemptDenied('GRANT60_CALL_LIMIT')

    def _record_reserved_attempt(self, data):
        if self._grant() is not None:
            # Persist epoch consumption and its row in the same inherited write/lock.
            data['grant60']['consumed_attempts'] += 1

    def _admit(self, data, request_id):
        super()._admit(data, request_id)
        if self._grant() is not None:
            self._admit_owner(data)

    @contextmanager
    def execution_scope(self):
        grant = self._grant()
        if grant is None:
            with super().execution_scope():
                yield self
            return
        owner = uuid.uuid4().hex
        with self._locked():
            data = self._read()
            self._require_attempt_authorization(data)
            self._require_attempt_capacity(data)
            entry = self._entry(data)
            epoch = data['grant60']
            if (data.get('calls_allowed_in_this_task') is not True or data['reserved_attempts']
                    or epoch['enabled'] or epoch.get('execution_owner') is not None
                    or self._active_scope(data) or entry.get('execution_owner') is not None):
                raise AttemptDenied('GRANT60_ACTIVATION_DENIED')
            if data.get('validation_blocked_reason'):
                raise AttemptDenied('VALIDATION_USAGE_BOUND_EXCEEDED')
            rows = self._rows(data)
            if self._uncertain(rows):
                raise AttemptDenied('REVIEWED_PREVIOUS_ATTEMPT_UNCERTAIN')
            rid = self.case['request_id']
            if any(row.get('request_id') == rid for row in data['attempts']):
                raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            if rows:
                raise AttemptDenied('REVIEWED_ATTEMPT_LIMIT')
            if self._used(data) + self.input_cap + self.output_cap > grant.global_token_limit:
                raise AttemptDenied('VALIDATION_TOKEN_CAP')
            entry.update(enabled=True, execution_owner=owner)
            epoch.update(enabled=True, execution_owner=owner, active_scope_id=self.product_spec.scope_id)
            self._write(data)
            self._grant_owner = owner
        try:
            yield self
        finally:
            try:
                with self._locked():
                    self._close_execution(self._read(), self.product_spec.scope_id, owner)
            finally:
                self._grant_owner = None

    def _close_execution(self, data, comparison_id, owner):
        if self._grant() is None:
            return super()._close_execution(data, comparison_id, owner)
        epoch = data['grant60']
        entry = data.get('reviewed_immutable_tasks', {}).get(comparison_id, {}).get('batches', {}).get(self.batch_id)
        if (not isinstance(entry, dict) or entry.get('execution_owner') != owner
                or epoch.get('execution_owner') != owner):
            return
        entry['enabled'] = False
        entry.pop('execution_owner')
        epoch['enabled'] = False
        epoch.pop('execution_owner')
        epoch.pop('active_scope_id', None)
        if not self._active_scope(data):
            data['calls_allowed_in_this_task'] = False
        self._write(data)

    def validate_before_send(self, attempt_id):
        """A transport must invoke this after reservation, immediately before send."""
        grant = self._grant()
        if grant is None:
            return
        with self._locked():
            data = self._read()
            require_current(grant)
            self._used(data)
            if data.get('validation_blocked_reason'):
                raise AttemptDenied('VALIDATION_USAGE_BOUND_EXCEEDED')
            row = next((r for r in self._rows(data) if r['attempt_id'] == attempt_id), None)
            if (data.get('calls_allowed_in_this_task') is not True or row is None
                    or row['status'] != 'reserved' or row.get('grant_epoch_id') != grant.epoch_id):
                raise AttemptDenied('GRANT60_PRESEND_DENIED')
            self._admit_owner(data)

    def _admit_owner(self, data):
        epoch, entry = data['grant60'], self._entry(data)
        if (epoch['enabled'] is not True or self._grant_owner is None
                or epoch.get('active_scope_id') != self.product_spec.scope_id
                or epoch.get('execution_owner') != self._grant_owner
                or entry.get('execution_owner') != self._grant_owner):
            raise AttemptDenied('GRANT60_EXECUTION_OWNER_REQUIRED')

    def _trusted_policy(self):
        spec = _trusted_product(self.product_spec)
        if asdict(self.task_policy) != asdict(_policy(spec)):
            raise AttemptDenied('PRODUCT_SCOPE_UNTRUSTED')
        return json.loads(json.dumps(asdict(self.task_policy)))

    def _policy_digest(self):
        self._trusted_policy()
        return _digest(asdict(self.task_policy))

    def _validate_manifest(self, manifest, spec):
        self._trusted_policy()
        expected = _policy(self.product_spec).batches[0]
        if spec != expected or manifest != _manifest(self.product_spec):
            raise AttemptDenied('REVIEWED_MANIFEST_CHANGED')
        return manifest

    def _load_manifest(self):
        self._trusted_policy()
        if self.batch_spec != _policy(self.product_spec).batches[0]:
            raise AttemptDenied('PRODUCT_SCOPE_UNTRUSTED')
        return _manifest(self.product_spec)

    def validate_product_scope(self, *, run_id, scope, question):
        self._frozen()
        spec = self.product_spec
        if (type(scope) is not Scope or run_id != spec.run_id
                or tuple(sorted(scope.knowledge_base_ids)) != spec.knowledge_base_ids
                or tuple(sorted(scope.document_ids)) != spec.document_ids
                or not isinstance(question, str)
                or hashlib.sha256(question.encode('utf-8')).hexdigest() != spec.question_sha256):
            raise AttemptDenied('PRODUCT_REQUEST_SCOPE_MISMATCH')

    def validate_request(self, prompt, timeout_seconds, max_tokens, *, model):
        self._frozen()
        grant = self._grant()
        if grant is not None:
            require_current(grant)
        if (not isinstance(prompt, str)
                or hashlib.sha256(prompt.encode('utf-8')).hexdigest() != self.product_spec.prompt_sha256
                or input_upper_bound(prompt) != self.input_cap
                or type(max_tokens) is not int or max_tokens != self.product_spec.output_cap
                or model != 'deepseek-flash'
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30):
            raise AttemptDenied('PRODUCT_FROZEN_REQUEST_MISMATCH')

    def _reservation_fields(self, data):
        spec = _trusted_product(self.product_spec)
        fields = {'reviewed_comparison': spec.scope_id, 'reviewed_batch': spec.scope_id,
                'reviewed_policy_sha256': self._policy_digest(), 'scope_sha256': self.batch_spec.sha256,
                'case_id': 'Q01', 'prompt_sha256': spec.prompt_sha256, 'retry_count': 0,
                'product_spec_sha256': _digest(asdict(spec)), 'product_run_id': spec.run_id,
                'product_knowledge_base_ids': list(spec.knowledge_base_ids),
                'product_document_ids': list(spec.document_ids)}
        grant = self._grant()
        if grant is not None:
            fields.update(product_scope_id=spec.scope_id, grant_epoch_id=grant.epoch_id,
                          grant_policy_sha256=_digest(asdict(grant)))
        return fields

    def _rows(self, data):
        rows = super()._rows(data)
        fields = self._reservation_fields(data)
        for row in rows:
            if (any(row.get(k) != v for k, v in fields.items())
                    or row['validation_tokens']['reserved_input'] != self.input_cap
                    or row['validation_tokens']['reserved_output'] != self.output_cap):
                raise AttemptDenied('REVIEWED_REGISTRY_CHANGED')
        return rows


class ProductReceiptFileSink:
    """Bound, exclusive receipt file; fsync plus byte readback acknowledges storage.

    Service startup chooses the path. Arbitrary callbacks and derived writers are
    not supported by the product gateway. Construction does not write or enable.
    """
    def __init__(self, path, *, scope_id):
        self.path = Path(path).resolve()
        self.spec = reviewed_product_request(scope_id)
        self.spec_sha256 = _digest(asdict(self.spec))
        # Process-local immutable tuple, created only from the adapter payload.
        # Never restored from intent/receipt files after restart or memory loss.
        self._wire_anchor = None

    def _binding(self, gate, request_id, wire_request_sha256):
        spec = _trusted_product(self.spec)
        if (type(gate) is not ReviewedProductRequestGate or gate.product_spec is not spec
                or self.spec_sha256 != _digest(asdict(spec))
                or request_id != request_identity(spec.run_id, 'quick.answer', 1)
                or not isinstance(wire_request_sha256, str)
                or not re.fullmatch('[0-9a-f]{64}', wire_request_sha256)):
            raise AttemptDenied('PRODUCT_RECEIPT_BINDING_MISMATCH')
        return {'scope_id': spec.scope_id, 'product_spec_sha256': self.spec_sha256,
                'request_id': request_id, 'prompt_sha256': spec.prompt_sha256,
                'wire_request_sha256': wire_request_sha256}

    def prepare(self, *, gate, request_id, wire_request_payload):
        if type(wire_request_payload) is not bytes or self._wire_anchor is not None:
            raise AttemptDenied('PRODUCT_RECEIPT_BINDING_ANCHOR_INVALID')
        wire_request_sha256 = hashlib.sha256(wire_request_payload).hexdigest()
        binding = self._binding(gate, request_id, wire_request_sha256)
        record = {'schema': 'product-provider-receipt-v1', 'binding': binding,
                  'state': 'prepared_not_sent', 'receipt': None}
        # Exclusive creation prevents overwrite/replay and proves a durable path
        # is available before any attempt reservation or provider transport.
        with self.path.open('xb') as target:
            target.write(json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            target.flush()
            os.fsync(target.fileno())
        self._wire_anchor = (gate, self.spec, request_id, wire_request_sha256)

    def persist(self, *, gate, attempt_id, receipt):
        anchor = self._wire_anchor
        if (type(anchor) is not tuple or len(anchor) != 4
                or anchor[0] is not gate or anchor[1] is not self.spec):
            raise AttemptDenied('PRODUCT_RECEIPT_BINDING_ANCHOR_REQUIRED')
        prepared = json.loads(self.path.read_text(encoding='utf-8'))
        binding = prepared['binding']
        expected = self._binding(gate, anchor[2], anchor[3])
        if (prepared.get('schema') != 'product-provider-receipt-v1'
                or prepared.get('state') != 'prepared_not_sent'
                or prepared.get('receipt') is not None or binding != expected
                or not isinstance(receipt, dict)
                or receipt.get('attempt_id') != attempt_id
                or receipt.get('product_scope_id') != expected['scope_id']
                or any(receipt.get(k) != expected[k] for k in
                       ('product_spec_sha256', 'request_id', 'prompt_sha256', 'wire_request_sha256'))):
            raise AttemptDenied('PRODUCT_RECEIPT_BINDING_MISMATCH')
        # A stored foreign request/attempt cannot stand in for the reserved row.
        # Use the existing canonical lock and row validation, not a new ledger.
        with gate._locked():
            data = gate._read()
            gate._used(data)
            rows = gate._rows(data)
            row = next((r for r in rows if r['attempt_id'] == attempt_id), None)
            if row is None or row['status'] != 'reserved' or row['request_id'] != expected['request_id']:
                raise AttemptDenied('PRODUCT_RECEIPT_ATTEMPT_MISMATCH')
        record = {**prepared, 'state': 'receipt_persisted', 'receipt': dict(receipt)}
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8')
        temp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('xb') as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp, self.path)
            if self.path.read_bytes() != raw:
                raise OSError('PRODUCT_RECEIPT_ACKNOWLEDGEMENT_FAILED')
            self._wire_anchor = None
            return hashlib.sha256(raw).hexdigest()
        finally:
            if temp.exists():
                temp.unlink()
