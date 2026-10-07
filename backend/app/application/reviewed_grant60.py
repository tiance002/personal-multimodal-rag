"""One source-reviewed product grant. Empty by default; no startup activation.

V1/V2 conversion is explicit, under the existing canonical ledger lock. Nothing
here constructs a provider, installs a scope, reads credentials or grants trust
from ledger contents. Rollback cannot discard any new attempt.
"""
import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from backend.app.application.session_attempts import CANONICAL_LEDGER, SessionAttemptGate
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.ports.session_attempts import AttemptDenied, request_identity


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Grant60Spec:
    epoch_id: str
    owner_thread_id: str
    owner_message_ids: tuple[str, ...]
    approval_sha256: str
    issued_at: str
    expires_at: str
    baseline_sha256: str
    baseline_json_sha256: str
    product_bindings: tuple[tuple[str, str], ...]
    question_set_sha256: str
    material_set_sha256: str
    other_hold_tokens: int | None = None
    activation_allowed: bool = False
    provider: str = 'deepseek'
    model: str = 'deepseek-flash'
    max_attempts: int = 60
    global_token_limit: int = 1_000_000
    manual_hold_tokens: int = 53_026


@dataclass(frozen=True)
class MigrationReceipt:
    epoch_id: str
    policy_sha256: str
    v1_bytes: bytes
    post_sha256: str


_GRANT60_SPECS = MappingProxyType({})
_GRANT60_DIGESTS = MappingProxyType({})


def utc_now():
    return datetime.now(timezone.utc)


def _utc(value):
    if not isinstance(value, str):
        raise ValueError()
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError()
    return stamp


def reviewed_grant60(epoch_id):
    try:
        spec = _GRANT60_SPECS[epoch_id]
        if (type(spec) is not Grant60Spec or _GRANT60_SPECS.get(spec.epoch_id) is not spec
                or digest(asdict(spec)) != _GRANT60_DIGESTS.get(spec.epoch_id)):
            raise AttemptDenied('GRANT60_UNTRUSTED')
    except (KeyError, TypeError):
        raise AttemptDenied('GRANT60_UNTRUSTED') from None
    try:
        hashes = (spec.approval_sha256, spec.baseline_sha256, spec.baseline_json_sha256,
                  spec.question_set_sha256, spec.material_set_sha256)
        valid = (all(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h) for h in hashes)
                 and isinstance(spec.epoch_id, str) and bool(spec.epoch_id)
                 and isinstance(spec.owner_thread_id, str) and bool(spec.owner_thread_id)
                 and type(spec.owner_message_ids) is tuple and len(spec.owner_message_ids) >= 2
                 and all(isinstance(s, str) and bool(s) for s in spec.owner_message_ids)
                 and len(set(spec.owner_message_ids)) == len(spec.owner_message_ids)
                 and type(spec.product_bindings) is tuple and bool(spec.product_bindings)
                 and all(type(b) is tuple and len(b) == 2 and isinstance(b[0], str) and bool(b[0])
                         and isinstance(b[1], str) and re.fullmatch('[0-9a-f]{64}', b[1])
                         for b in spec.product_bindings)
                 and tuple(sorted(spec.product_bindings)) == spec.product_bindings
                 and len({b[0] for b in spec.product_bindings}) == len(spec.product_bindings)
                 and spec.provider == 'deepseek' and spec.model == 'deepseek-flash'
                 and type(spec.max_attempts) is int and spec.max_attempts == 60
                 and type(spec.global_token_limit) is int and spec.global_token_limit == 1_000_000
                 and type(spec.manual_hold_tokens) is int and spec.manual_hold_tokens == 53_026
                 and (spec.other_hold_tokens is None or type(spec.other_hold_tokens) is int
                      and 0 <= spec.other_hold_tokens <= 1_000_000 - 53_026)
                 and type(spec.activation_allowed) is bool
                 and 0 < (_utc(spec.expires_at) - _utc(spec.issued_at)).total_seconds() <= 86400)
    except (ValueError, TypeError, AttributeError, OverflowError):
        valid = False
    if not valid:
        raise AttemptDenied('GRANT60_SPEC_INVALID')
    return spec


def trusted_for_product(epoch_id, scope_id, spec_sha256):
    spec = reviewed_grant60(epoch_id)
    if (scope_id, spec_sha256) not in spec.product_bindings:
        raise AttemptDenied('GRANT60_PRODUCT_SCOPE_UNTRUSTED')
    return spec


def require_current(spec):
    if reviewed_grant60(spec.epoch_id) is not spec:
        raise AttemptDenied('GRANT60_UNTRUSTED')
    if not spec.activation_allowed:
        raise AttemptDenied('GRANT60_DISABLED')
    if spec.other_hold_tokens is None:
        raise AttemptDenied('GRANT60_EXTERNAL_USAGE_UNKNOWN')
    now = utc_now()
    if (type(now) is not datetime or now.tzinfo is None
            or now.utcoffset().total_seconds() != 0):
        raise AttemptDenied('GRANT60_CLOCK_INVALID')
    if now < _utc(spec.issued_at):
        raise AttemptDenied('GRANT60_NOT_YET_VALID')
    if now >= _utc(spec.expires_at):
        raise AttemptDenied('GRANT60_EXPIRED')


def _canonical(path):
    if Path(path).resolve() != CANONICAL_LEDGER.resolve():
        raise AttemptDenied('GRANT60_CANONICAL_LEDGER_REQUIRED')


def _has_owner(data):
    if data.get('grant60', {}).get('execution_owner') is not None:
        return True
    for task in data.get('reviewed_immutable_tasks', {}).values():
        for entry in task.get('batches', {}).values():
            if entry.get('enabled') is True or entry.get('execution_owner') is not None:
                return True
    return any(e.get('enabled') is True or e.get('execution_owner') is not None
               for e in data.get('static_pair_scopes', {}).values())


def validate_v2(gate, data, spec):
    """Integrity only: expiry must not block late receipts/settlement/cleanup."""
    from backend.app.application.reviewed_product_request import reviewed_product_request
    try:
        if reviewed_grant60(spec.epoch_id) is not spec:
            raise ValueError()
        legacy = SessionAttemptGate._validate_v1_data(data['legacy_v1'])
        old = legacy['consumed_attempts']
        rows = data['attempts']
        epoch = data['grant60']
        n, pending = data['consumed_attempts'], data['reserved_attempts']
        good = (type(data['schema_version']) is int and data['schema_version'] == 2
                and data['provider'] == 'deepseek' and old == legacy['authorized_limit'] == 53
                and digest(legacy) == spec.baseline_json_sha256
                and type(data['authorized_limit']) is int and data['authorized_limit'] == legacy['authorized_limit']
                and isinstance(rows, list) and rows[:old] == legacy['attempts']
                and digest(rows[:old]) == digest(legacy['attempts'])
                and all(type(v) is int for v in (n, pending)) and 0 <= pending <= n == len(rows)
                and all(isinstance(r, dict) and isinstance(r.get('attempt_id'), str)
                        and r.get('status') in ('reserved', 'ok', 'unknown', 'truncated') for r in rows)
                and len({r['attempt_id'] for r in rows}) == n
                and sum(r['status'] == 'reserved' for r in rows) == pending
                and epoch['epoch_id'] == spec.epoch_id and epoch['policy_sha256'] == digest(asdict(spec))
                and type(epoch['consumed_attempts']) is int and epoch['consumed_attempts'] == n - old
                and 0 <= epoch['consumed_attempts'] <= 60 and type(epoch['enabled']) is bool
                and type(data['calls_allowed_in_this_task']) is bool)
        if not good:
            raise ValueError()
        known_ids = {r.get('request_id') for r in rows[:old] if isinstance(r.get('request_id'), str)}
        for r in rows[old:]:
            p = reviewed_product_request(r['product_scope_id'])
            if (p.grant_epoch_id != spec.epoch_id
                    or (p.scope_id, digest(asdict(p))) not in spec.product_bindings
                    or r['grant_epoch_id'] != spec.epoch_id or r['grant_policy_sha256'] != digest(asdict(spec))
                    or r['product_spec_sha256'] != digest(asdict(p))
                    or r['request_id'] != request_identity(p.run_id, 'quick.answer', 1)
                    or r['request_id'] in known_ids
                    or r['validation_tokens']['reserved_input'] != p.input_cap
                    or r['validation_tokens']['reserved_output'] != p.output_cap
                    or r['validation_cost']['model'] != 'deepseek-flash'):
                raise ValueError()
            known_ids.add(r['request_id'])
        ValidationAttemptGate._used(gate, data)
        return data
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AttemptDenied('GRANT60_LEDGER_INVALID') from None


def read_v2(gate, spec):
    _canonical(gate.path)
    try:
        data = json.loads(gate.path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        raise AttemptDenied('GRANT60_LEDGER_INVALID') from None
    return validate_v2(gate, data, spec)


def migrate_disabled_v1(path, epoch_id):
    spec = reviewed_grant60(epoch_id)
    _canonical(path)
    gate = ValidationAttemptGate(path)
    with gate._locked():
        raw = gate.path.read_bytes()
        data = gate._read()
        gate._used(data)
        if (hashlib.sha256(raw).hexdigest() != spec.baseline_sha256 or digest(data) != spec.baseline_json_sha256
                or data['consumed_attempts'] != 53 or data['authorized_limit'] != 53
                or data.get('calls_allowed_in_this_task') is not False or data['reserved_attempts']
                or _has_owner(data) or 'grant60' in data or 'legacy_v1' in data):
            raise AttemptDenied('GRANT60_MIGRATION_REQUIRES_DISABLED_BASELINE')
        migrated = copy.deepcopy(data)
        migrated.update(schema_version=2, legacy_v1=copy.deepcopy(data),
                        grant60={'epoch_id': spec.epoch_id, 'policy_sha256': digest(asdict(spec)),
                                 'consumed_attempts': 0, 'enabled': False})
        validate_v2(gate, migrated, spec)
        gate._write(migrated)
        return MigrationReceipt(spec.epoch_id, digest(asdict(spec)), raw,
                                hashlib.sha256(gate.path.read_bytes()).hexdigest())


def rollback_disabled_v1(path, receipt):
    if type(receipt) is not MigrationReceipt:
        raise AttemptDenied('GRANT60_ROLLBACK_RECEIPT_INVALID')
    spec = reviewed_grant60(receipt.epoch_id)
    _canonical(path)
    if (receipt.policy_sha256 != digest(asdict(spec)) or type(receipt.v1_bytes) is not bytes
            or hashlib.sha256(receipt.v1_bytes).hexdigest() != spec.baseline_sha256):
        raise AttemptDenied('GRANT60_ROLLBACK_RECEIPT_INVALID')
    original = SessionAttemptGate._validate_v1_data(json.loads(receipt.v1_bytes.decode('utf-8-sig')))
    if digest(original) != spec.baseline_json_sha256:
        raise AttemptDenied('GRANT60_ROLLBACK_RECEIPT_INVALID')
    gate = ValidationAttemptGate(path)
    with gate._locked():
        raw = gate.path.read_bytes()
        data = validate_v2(gate, json.loads(raw.decode('utf-8-sig')), spec)
        if data['grant60']['consumed_attempts']:
            # Never discard a reservation, including zero usage or ambiguous sends.
            data['calls_allowed_in_this_task'] = False
            data['grant60']['enabled'] = False
            gate._write(data)
            return 'V2_DISABLED_HAS_ATTEMPTS'
        if (hashlib.sha256(raw).hexdigest() != receipt.post_sha256
                or data.get('calls_allowed_in_this_task') is not False
                or data['reserved_attempts'] or data['grant60']['enabled'] or _has_owner(data)):
            raise AttemptDenied('GRANT60_ROLLBACK_REQUIRES_PRISTINE_DISABLED_V2')
        gate._write_bytes(receipt.v1_bytes)
        return 'V1_RESTORED'
