"""One reviewed static synthetic pair batch; no registration or egress on import."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from backend.app.application.session_attempts import CANONICAL_LEDGER
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.ports.session_attempts import AttemptDenied

BATCH_ID = 'task-17-static-pair-20261001'
APPROVED_SHA256 = '9b9299a88be6374ead07436a5a19a6e3ca77fd2a499fc3517f381a9751e7c896'


def _manifest(scope_path, expected_sha256):
    try:
        raw = Path(scope_path).read_bytes()
        if expected_sha256 != APPROVED_SHA256 or hashlib.sha256(raw).hexdigest() != APPROVED_SHA256:
            raise AttemptDenied('PAIR_MANIFEST_CHANGED')
        return json.loads(raw)
    except (OSError, ValueError, TypeError):
        raise AttemptDenied('PAIR_MANIFEST_UNAVAILABLE') from None


class StaticPairValidationGate(ValidationAttemptGate):
    allowed_output_caps = (256,)

    def __init__(self, ledger_path=CANONICAL_LEDGER, *, scope_path, expected_sha256, case_id):
        self.scope_path = Path(scope_path)
        self.manifest = _manifest(scope_path, expected_sha256)
        self.case = next((c for c in self.manifest['cases'] if c['id'] == case_id), None)
        if self.case is None:
            raise AttemptDenied('PAIR_CASE_UNREGISTERED')
        super().__init__(ledger_path, model=self.manifest['model'], input_tokens_cap=self.case['input_cap'],
                         max_output_tokens=256)
        self._canonical()

    def _canonical(self):
        if self.path.resolve() != CANONICAL_LEDGER.resolve():
            raise AttemptDenied('PAIR_CANONICAL_LEDGER_REQUIRED')

    def _frozen(self):
        self._canonical()
        m = _manifest(self.scope_path, APPROVED_SHA256)
        if (m != self.manifest or self.case not in m['cases'] or self.model != m['model']
                or self.input_cap != self.case['input_cap'] or self.output_cap != m['output_cap']):
            raise AttemptDenied('PAIR_MANIFEST_CHANGED')
        return m

    def _entry(self, data):
        self._frozen()
        entry = data.get('static_pair_scopes', {}).get(BATCH_ID)
        if not isinstance(entry, dict) or entry.get('manifest') != self.manifest or entry.get('sha256') != APPROVED_SHA256:
            raise AttemptDenied('PAIR_REGISTRY_CHANGED')
        if type(entry.get('enabled')) is not bool:
            raise AttemptDenied('PAIR_REGISTRY_CHANGED')
        return entry

    def register_disabled_scope(self, scope_path, expected_sha256):
        if _manifest(scope_path, expected_sha256) != self._frozen():
            raise AttemptDenied('PAIR_MANIFEST_CHANGED')
        with self._locked():
            data = self._read()
            self._used(data)
            if data.get('calls_allowed_in_this_task') is not False or data['reserved_attempts']:
                raise AttemptDenied('PAIR_REGISTRATION_REQUIRES_DISABLED')
            scopes = data.setdefault('static_pair_scopes', {})
            if BATCH_ID in scopes:
                entry = self._entry(data)
                if entry['enabled']:
                    raise AttemptDenied('PAIR_REGISTRATION_REQUIRES_DISABLED')
                return
            identities = {c['request_id'] for c in self.manifest['cases']}
            if any(a.get('request_id') in identities for a in data['attempts']):
                raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            for entry in scopes.values():
                if any(c['request_id'] in identities for c in entry['manifest']['cases']):
                    raise AttemptDenied('SESSION_REQUEST_ALREADY_RESERVED')
            scopes[BATCH_ID] = {'sha256': APPROVED_SHA256, 'manifest': self.manifest, 'enabled': False}
            self._write(data)

    def validate_request(self, prompt, timeout_seconds, max_tokens, *, model):
        self._frozen()
        if (prompt != self.case['prompt'] or model != self.model or type(max_tokens) is not int
                or max_tokens != 256 or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 30):
            raise AttemptDenied('PAIR_REQUEST_MISMATCH')

    def _admit(self, data, request_id):
        if not self._entry(data)['enabled']:
            raise AttemptDenied('PAIR_SCOPE_DISABLED')
        if request_id != self.case['request_id']:
            raise AttemptDenied('PAIR_REQUEST_ID_MISMATCH')
        rows = [r for r in data['attempts'] if r.get('static_pair_batch') == BATCH_ID]
        if any(r.get('scope_sha256') != APPROVED_SHA256 for r in rows):
            raise AttemptDenied('PAIR_REGISTRY_CHANGED')
        if len(rows) >= 4:
            raise AttemptDenied('PAIR_ATTEMPT_LIMIT')
        used = sum(r['validation_tokens']['reserved_input'] + r['validation_tokens']['reserved_output'] for r in rows)
        if used + self.input_cap + self.output_cap > 8192:
            raise AttemptDenied('PAIR_TOKEN_BOUND')

    def _reservation_fields(self, data):
        return {'static_pair_batch': BATCH_ID, 'scope_sha256': APPROVED_SHA256,
                'case_id': self.case['id'], 'prompt_sha256': self.case['prompt_sha256'],
                'original_request_sha256': self.case['original_request_sha256'],
                'evidence_hashes': self.case['evidence_hashes'], 'retry_count': 0}

    @contextmanager
    def execution_scope(self):
        """Explicit activation only after independent review and global enablement.

        Context closure disables this batch, including on transport exceptions.
        Unknown and pending attempts keep their conservative reservations.
        """
        with self._locked():
            data = self._read()
            self._require_attempt_authorization(data)
            used = self._used(data)
            entry = self._entry(data)
            if data.get('calls_allowed_in_this_task') is not True or data['reserved_attempts'] or entry['enabled']:
                raise AttemptDenied('PAIR_ACTIVATION_DENIED')
            if data.get('validation_blocked_reason'):
                raise AttemptDenied('VALIDATION_USAGE_BOUND_EXCEEDED')
            # Whole-batch preflight is conservative; every send still reserves atomically.
            if used + sum(c['input_cap'] + 256 for c in self.manifest['cases']) > 1_000_000:
                raise AttemptDenied('VALIDATION_TOKEN_CAP')
            if data['consumed_attempts'] + 4 > data['authorized_limit']:
                raise AttemptDenied('SESSION_CALL_LIMIT')
            entry['enabled'] = True
            self._write(data)
        try:
            yield self
        finally:
            with self._locked():
                data = self._read()
                # Close even if the manifest on disk changed during execution.
                entry = data.get('static_pair_scopes', {}).get(BATCH_ID)
                if isinstance(entry, dict):
                    entry['enabled'] = False
                    self._write(data)
