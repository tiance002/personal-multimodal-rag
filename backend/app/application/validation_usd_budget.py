"""Explicitly authorized synthetic validation, never a product budget fallback."""
import re
import uuid
import hashlib
from dataclasses import asdict, dataclass
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.application.session_attempts import CANONICAL_LEDGER, SessionAttemptGate
from backend.app.application.usd_pricing import PEAK_RATES, input_upper_bound, quote_micro_usd
from backend.app.ports.session_attempts import AttemptDenied

SYNTHETIC_PROMPT = "Return exactly SYNTHETIC_OK. This is synthetic test data with no private content."
SCOPE = "task-2-authorized-synthetic-validation"
POLICY = {"scope": SCOPE, "token_limit": 1_000_000, "currency": "USD",
          "scale": 1_000_000, "usd_stop_limit": None, "resets": False}

# V6 is a new activity grant over the existing canonical ledger.  It does not
# change the old 53-call authorization or the V1 validation policy.
V6_ACTIVITY_ID = "RAG-V6-TOKEN-GRANT"
V6_GRANT_ID = "owner-v6-public-rag-first-round"
V6_GRANT_VERSION = 1
V6_TOKEN_LIMIT = 1_000_000
V6_BASELINE_USED_TOKENS = 65_569
V6_BASELINE_LEDGER_SHA256 = "17b922da4a2d0f9c9afda763c4ace498c1f7d3a456bd9281ebe6c91739d843ad"
V6_DEADLINE_UTC = "2026-10-10T04:25:44+00:00"
V6_SCOPE = "p7-v6-public-first-round"
V6_OUTPUT_TOKEN_CAP = 512
V6_CONTEXT_WINDOW_TOKENS = 1_048_576
V6_CAPACITY_SOURCE = "DeepSeek official /api/list-models/; owner-verified 2026-10-09"
V6_TOKENIZER_SOURCE = (
    "deepseek-ai/deepseek-recipe@8cadfede7063c896b944e7bae05daa3549ae97ea"
    ":package=0.1.1:static/tokenizers/v41/tokenizer.json:"
    "sha256=81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99"
)
V6_SPECIAL_TOKEN_POLICY = "deny-tokenizer-added-token-literals-v1"
V6_REQUEST_PROTOCOL = "openai-chat-single-user-text-thinking-disabled-no-tools-v1"
_SPECIAL_TOKEN_CANDIDATE = re.compile(
    r"<(?:(?:[|｜][^>\r\n]{1,128}[|｜])|/?think)>|[｜|]DSML[｜|]"
)


@dataclass(frozen=True)
class OwnerActivityTokenGrant:
    """Immutable, versioned task authorization; simulated grants are test-only."""

    grant_id: str
    activity_id: str
    version: int
    provider: str
    model: str
    output_token_cap: int
    token_limit: int
    baseline_used_tokens: int
    baseline_ledger_sha256: str
    deadline_utc: str | None
    scope: str
    execution_kind: str = "REAL"

    @classmethod
    def simulated(cls, *, token_limit=1_000_000, baseline_used_tokens=0,
                  deadline_utc="2026-10-10T04:25:44+00:00"):
        return cls("SIMULATED-v6-grant", "SIMULATED-v6-activity", 1,
                   "deepseek", "deepseek-flash", V6_OUTPUT_TOKEN_CAP, token_limit,
                   baseline_used_tokens, "SIMULATED", deadline_utc,
                   "SIMULATED-public-scope", "SIMULATED")


OWNER_V6_TOKEN_GRANT = OwnerActivityTokenGrant(
    grant_id=V6_GRANT_ID,
    activity_id=V6_ACTIVITY_ID,
    version=V6_GRANT_VERSION,
    provider="deepseek",
    model="deepseek-flash",
    output_token_cap=V6_OUTPUT_TOKEN_CAP,
    token_limit=V6_TOKEN_LIMIT,
    baseline_used_tokens=V6_BASELINE_USED_TOKENS,
    baseline_ledger_sha256=V6_BASELINE_LEDGER_SHA256,
    deadline_utc=V6_DEADLINE_UTC,
    scope=V6_SCOPE,
)

# Latest explicit Owner six-hour revision; original grant remains immutable.
OWNER_V6_TOKEN_GRANT_R2 = OwnerActivityTokenGrant(
    "owner-v6-public-rag-six-hour-r2",V6_ACTIVITY_ID,2,"deepseek","deepseek-flash",512,
    V6_TOKEN_LIMIT,V6_BASELINE_USED_TOKENS,V6_BASELINE_LEDGER_SHA256,
    "2026-10-10T07:44:02+00:00","p7-v6-public-six-hour-r2")

# Owner cancelled the activity-wide cutoff, not request/lease/budget limits.
# This is a separate closed grant; neither historical deadline is extended.
V6_OWNER_ACTIVITY_R3_SOURCE = "Sentinel_77a5fceb1db081918fecb2da7202a274@2026-10-10T08:46:54Z"
OWNER_V6_TOKEN_GRANT_R3 = OwnerActivityTokenGrant(
    "owner-v6-public-rag-r3", "RAG-V6-TOKEN-GRANT-R3", 3, "deepseek", "deepseek-flash", 512,
    V6_TOKEN_LIMIT, V6_BASELINE_USED_TOKENS, V6_BASELINE_LEDGER_SHA256, None, "p7-v6-public-r3")


@dataclass(frozen=True)
class V6ExecutionLease:
    """One explicit finite Run lease and its separate, caller-approved Owner end."""
    lease_id: str
    not_before_utc: str
    deadline_utc: str
    owner_deadline_utc: str | None

    def __post_init__(self):
        self.validate()

    def validate(self):
        try:
            if (type(self) is not V6ExecutionLease or type(self.lease_id) is not str
                    or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',self.lease_id)):
                raise ValueError()
            start=self._utc(self.not_before_utc);end=self._utc(self.deadline_utc)
            if not 0<(end-start).total_seconds()<=3600:
                raise ValueError()
            if self.owner_deadline_utc is not None and end>self._utc(self.owner_deadline_utc):
                raise ValueError()
        except (TypeError,ValueError,AttributeError):
            raise ValueError('V6_EXECUTION_LEASE_INVALID') from None

    @staticmethod
    def _utc(value):
        if type(value) is not str:raise ValueError()
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds()!=0:raise ValueError()
        return parsed

    @property
    def not_before(self):
        self.validate();return self._utc(self.not_before_utc)

    @property
    def deadline(self):
        self.validate();return self._utc(self.deadline_utc)


@dataclass(frozen=True)
class TokenRequestBudget:
    """Trusted preflight result for the exact request protocol, supplied by caller."""

    activity_id: str
    scope: str
    provider: str
    model: str
    input_token_bound: int
    output_token_bound: int
    safety_tokens: int
    window_tokens: int
    capacity_source: str
    tokenizer_source: str
    prompt_text: str
    special_token_literals: tuple[str, ...]
    wire_request_sha256: str
    protocol_id: str


def _record_token_occupancy(rows, *, allow_released=False):
    """Count settled usage once; outstanding/unknown attempts retain full bounds."""
    total = 0
    valid_states = {
        "reserved": {"reserved"},
        "ok": {"settled", "unknown"},
        "unknown": {"unknown"},
        "truncated": {"unknown"},
    }
    if allow_released:
        valid_states["not_sent"] = {"released"}
    for row in rows:
        tokens = row.get("validation_tokens")
        status = row.get("status")
        state = tokens.get("state") if isinstance(tokens, dict) else None
        if (not isinstance(row, dict) or not isinstance(status, str) or status not in valid_states
                or not isinstance(tokens, dict) or not isinstance(state, str)
                or state not in valid_states[status]
                or any(type(tokens.get(k)) is not int or tokens[k] < 0
                       for k in ("reserved_input", "reserved_output"))):
            raise AttemptDenied("V6_TOKEN_RECORD_INVALID")
        ri, ro = tokens["reserved_input"], tokens["reserved_output"]
        if not 0 < ri <= V6_TOKEN_LIMIT or not 0 < ro <= V6_TOKEN_LIMIT:
            raise AttemptDenied("V6_TOKEN_RECORD_INVALID")
        if tokens["state"] == "released":
            if status != "not_sent":
                raise AttemptDenied("V6_TOKEN_RECORD_INVALID")
            continue
        if tokens["state"] == "settled":
            used_input, used_output = tokens.get("input"), tokens.get("output")
            if (type(used_input) is not int or type(used_output) is not int
                    or not 0 <= used_input <= ri or not 0 <= used_output <= ro):
                raise AttemptDenied("V6_TOKEN_RECORD_INVALID")
            total += used_input + used_output
        else:
            total += ri + ro
    return total


class V6OwnerActivityGate(SessionAttemptGate):
    """Atomic V6 activity token gate sharing, but not reinterpreting, the V1 ledger."""

    allowed_output_caps = (V6_OUTPUT_TOKEN_CAP,)

    def __init__(self, ledger_path, *, grant=OWNER_V6_TOKEN_GRANT, clock=None,authority=None,execution_lease=None):
        super().__init__(ledger_path)
        if type(grant) is not OwnerActivityTokenGrant:
            raise AttemptDenied("V6_OWNER_GRANT_INVALID")
        from backend.app.ports.deepseek_gate_types import V6LedgerAuthority
        if authority is not None:
            if type(authority) is not V6LedgerAuthority:
                raise AttemptDenied('V6_AUTHORITY_MANIFEST_INVALID')
            authority.revalidate()
            if (self.path.resolve()!=Path(authority.ledger_path).resolve()
                    or authority.execution_kind!=grant.execution_kind
                    or self.path.resolve()==CANONICAL_LEDGER.resolve()
                    or authority.source_baseline_sha256!=grant.baseline_ledger_sha256):
                raise AttemptDenied('V6_AUTHORITY_PATH_OR_GRANT_MISMATCH')
        self.authority=authority
        if authority is not None and authority.marker_sha256 is not None:
            self.expected_authority_marker=(authority.marker_sha256,authority.authority_id,authority.authority_epoch)
        if grant.execution_kind == "REAL":
            if (grant is not OWNER_V6_TOKEN_GRANT and grant is not OWNER_V6_TOKEN_GRANT_R2 and grant is not OWNER_V6_TOKEN_GRANT_R3
                    or authority is None and self.path.resolve() != CANONICAL_LEDGER.resolve()):
                raise AttemptDenied("V6_REAL_GRANT_PATH_OR_IDENTITY_INVALID")
            if grant in (OWNER_V6_TOKEN_GRANT_R2, OWNER_V6_TOKEN_GRANT_R3) and (authority is None or authority.marker_sha256 is None):
                raise AttemptDenied('V6_REVISION_AUTHORITY_REQUIRED')
        elif grant.execution_kind != "SIMULATED":
            raise AttemptDenied("V6_OWNER_GRANT_INVALID")
        elif self.path.resolve() == CANONICAL_LEDGER.resolve():
            raise AttemptDenied("V6_SIMULATED_GRANT_CANNOT_USE_CANONICAL_LEDGER")
        if (grant.version not in (V6_GRANT_VERSION,2,3) or type(grant.version) is not int or grant.provider != "deepseek"
                or grant.model != "deepseek-flash" or grant.output_token_cap != V6_OUTPUT_TOKEN_CAP
                or type(grant.token_limit) is not int
                or not 0 < grant.token_limit <= V6_TOKEN_LIMIT
                or type(grant.baseline_used_tokens) is not int
                or not 0 <= grant.baseline_used_tokens < grant.token_limit
                or not (isinstance(grant.deadline_utc, str) and grant.deadline_utc
                        or grant.deadline_utc is None and grant.version == 3)
                or not isinstance(grant.scope, str) or not grant.scope):
            raise AttemptDenied("V6_OWNER_GRANT_INVALID")
        try:
            self.deadline = (None if grant.deadline_utc is None else
                datetime.fromisoformat(grant.deadline_utc.replace("Z", "+00:00")))
            if self.deadline is not None and self.deadline.tzinfo is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise AttemptDenied("V6_OWNER_GRANT_INVALID") from None
        self.grant = grant
        self.execution_lease=execution_lease
        self.not_before=None
        if grant.version==3:
            if type(execution_lease) is not V6ExecutionLease:
                raise AttemptDenied('V6_EXECUTION_LEASE_REQUIRED')
            execution_lease.validate()
            self.not_before=execution_lease.not_before
            self.deadline=execution_lease.deadline if self.deadline is None else min(self.deadline,execution_lease.deadline)
        elif execution_lease is not None:
            raise AttemptDenied('V6_EXECUTION_LEASE_NOT_SUPPORTED')
        self.allowed_output_caps = (grant.output_token_cap,)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def check_authority_active(self):
        """Recheck full pinned authority at the shared dispatch/lease boundary."""
        if self.authority is not None:
            self.authority.revalidate()
        super().check_authority_active()

    @staticmethod
    def legacy_state_sha256(data):
        """All retained legacy fields, including audit metadata, remain pinned.

        Migration may project text only after Owner review. It must supply the
        digest of that exact projected legacy state; this function neither
        decides which text to remove nor rewrites any field.
        """
        import json
        legacy={k:v for k,v in data.items() if k not in ('v6_owner_activity',)}
        return hashlib.sha256(json.dumps(legacy,sort_keys=True,separators=(',',':'),
            ensure_ascii=False,allow_nan=False).encode()).hexdigest()

    def _read(self):
        data=super()._read()
        if self.authority is not None:
            self.authority.revalidate()
            if (self.legacy_state_sha256(data)!=self.authority.legacy_state_sha256
                    or data.get('calls_allowed_in_this_task') is not False):
                raise AttemptDenied('V6_MIGRATED_LEGACY_HISTORY_MISMATCH')
            activity=data.get('v6_owner_activity')
            initial=self.authority.initial_v6_attempts
            if initial and (not isinstance(activity,dict) or not isinstance(activity.get('attempts'),list)
                    or any(row not in activity['attempts'] for row in initial)):
                raise AttemptDenied('V6_MIGRATED_ACTIVITY_HISTORY_MISMATCH')
            if self._legacy_used(data)!=self.grant.baseline_used_tokens:
                raise AttemptDenied('V6_BASELINE_USAGE_MISMATCH')
        return data

    @staticmethod
    def _legacy_used(data):
        return _record_token_occupancy(data["attempts"])

    def _activity(self, data):
        activity = data.get("v6_owner_activity")
        if (not isinstance(activity, dict)
                or activity.get("grant") != asdict(self.grant)
                or activity.get("schema_version") != 1
                or not isinstance(activity.get("attempts"), list)
                or activity.get("blocked_reason") is not None):
            if isinstance(activity, dict) and activity.get("blocked_reason"):
                raise AttemptDenied("V6_ACTIVITY_BLOCKED")
            raise AttemptDenied("V6_OWNER_GRANT_NOT_ACTIVE")
        seen_attempts, seen_requests = set(), set()
        history=activity.get('grant_history',[])
        if (type(history) is not list or any(type(g) is not dict for g in history)
                or len({g.get('grant_id') for g in history})!=len(history)):
            raise AttemptDenied('V6_ACTIVITY_INVALID')
        real_history = {1: ([],), 2: ([], [asdict(OWNER_V6_TOKEN_GRANT)]),
            3: ([], [asdict(OWNER_V6_TOKEN_GRANT)], [asdict(OWNER_V6_TOKEN_GRANT_R2)],
                [asdict(OWNER_V6_TOKEN_GRANT), asdict(OWNER_V6_TOKEN_GRANT_R2)])}
        if self.grant.execution_kind=='REAL' and history not in real_history[self.grant.version]:
            raise AttemptDenied('V6_ACTIVITY_INVALID')
        for row in activity["attempts"]:
            try:
                row_grant=next((g for g in [asdict(self.grant),*history] if g['grant_id']==row.get('grant_id')),None)
                if row_grant is None:raise ValueError()
                tokens = row["validation_tokens"]
                budget = row["request_budget"]
                request_id = row["request_id"]
                attempt_id = row["attempt_id"]
                valid = (
                    isinstance(row, dict)
                    and row.get("activity_id") == row_grant['activity_id']
                    and isinstance(attempt_id, str) and attempt_id
                    and attempt_id not in seen_attempts
                    and isinstance(request_id, str)
                    and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id)
                    and request_id not in seen_requests
                    and isinstance(budget, dict)
                    and budget.get("activity_id") == row_grant['activity_id']
                    and budget.get("scope") == row_grant['scope']
                    and budget.get("provider") == row_grant['provider']
                    and budget.get("model") == row_grant['model']
                    and budget.get("capacity_source") == V6_CAPACITY_SOURCE
                    and budget.get("tokenizer_source") == V6_TOKENIZER_SOURCE
                    and budget.get("protocol_id") == V6_REQUEST_PROTOCOL
                    and budget.get("special_token_policy") == V6_SPECIAL_TOKEN_POLICY
                    and type(budget.get("input_token_bound")) is int
                    and budget["input_token_bound"] > 0
                    and type(budget.get("safety_tokens")) is int
                    and budget["safety_tokens"] >= 0
                    and type(budget.get("output_token_bound")) is int
                    and budget["output_token_bound"] == row_grant['output_token_cap']
                    and budget.get("window_tokens") == V6_CONTEXT_WINDOW_TOKENS
                    and type(budget.get("window_tokens")) is int
                    and re.fullmatch(r"[0-9a-f]{64}", budget.get("wire_request_sha256", ""))
                    and re.fullmatch(r"[0-9a-f]{64}", budget.get("prompt_sha256", ""))
                    and budget["input_token_bound"] + budget["safety_tokens"] + budget["output_token_bound"] <= budget["window_tokens"]
                    and tokens["reserved_input"] == budget["input_token_bound"] + budget["safety_tokens"]
                    and tokens["reserved_output"] == budget["output_token_bound"]
                    and row.get("status") in ("reserved", "ok", "unknown", "truncated", "not_sent")
                )
            except (KeyError, TypeError,ValueError):
                valid = False
            if not valid:
                raise AttemptDenied("V6_ACTIVITY_INVALID")
            seen_attempts.add(attempt_id)
            seen_requests.add(request_id)
        return activity

    def activate_revision(self,previous_grant):
        """Explicit once-only revision, preserving every occupied/UNKNOWN row."""
        real_pair = ((self.grant is OWNER_V6_TOKEN_GRANT_R2 and previous_grant is OWNER_V6_TOKEN_GRANT)
            or (self.grant is OWNER_V6_TOKEN_GRANT_R3 and
                (previous_grant is OWNER_V6_TOKEN_GRANT or previous_grant is OWNER_V6_TOKEN_GRANT_R2)))
        if (type(previous_grant) is not OwnerActivityTokenGrant
                or (self.grant.version,previous_grant.version) not in ((2,1),(3,1),(3,2))
                or previous_grant.token_limit!=self.grant.token_limit
                or previous_grant.baseline_used_tokens!=self.grant.baseline_used_tokens
                or previous_grant.baseline_ledger_sha256!=self.grant.baseline_ledger_sha256
                or previous_grant.provider!=self.grant.provider or previous_grant.model!=self.grant.model
                or previous_grant.output_token_cap!=self.grant.output_token_cap
                or previous_grant.execution_kind!=self.grant.execution_kind
                or self.grant.execution_kind=='REAL' and not real_pair):
            raise AttemptDenied('V6_GRANT_REVISION_INVALID')
        with self._locked():
            data=self._read();activity=data.get('v6_owner_activity')
            if isinstance(activity,dict) and activity.get('grant')==asdict(self.grant):
                self._activity(data);return
            if not isinstance(activity,dict) or activity.get('grant')!=asdict(previous_grant):
                raise AttemptDenied('V6_GRANT_REVISION_INVALID')
            # Validate all old rows against their original immutable grant.
            previous=V6OwnerActivityGate(self.path,grant=previous_grant,authority=self.authority,clock=self.clock)
            previous._activity(data)
            if any(row['status']=='reserved' for row in activity['attempts']):
                raise AttemptDenied('V6_GRANT_REVISION_PENDING_ATTEMPT')
            activity['grant_history']=[*activity.get('grant_history',[]),asdict(previous_grant)]
            activity['grant']=asdict(self.grant)
            activity['revised_utc']=self._now().isoformat()
            self._activity(data);self._write(data)

    def initialize_grant(self):
        """Persist the exact grant once; never resets or rewrites V1 counters/history."""
        with self._locked():
            data = self._read()
            existing = data.get("v6_owner_activity")
            if existing is not None:
                self._activity(data)
                return
            initial = self._legacy_used(data)
            if initial != self.grant.baseline_used_tokens:
                raise AttemptDenied("V6_BASELINE_USAGE_MISMATCH")
            if self.grant.execution_kind == "REAL":
                try:
                    digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
                except OSError:
                    raise AttemptDenied("SESSION_LEDGER_UNAVAILABLE") from None
                if ((self.authority is None and digest != self.grant.baseline_ledger_sha256)
                        or data["reserved_attempts"] != 0):
                    raise AttemptDenied("V6_BASELINE_SNAPSHOT_MISMATCH")
            data["v6_owner_activity"] = {
                "schema_version": 1,
                "grant": asdict(self.grant),
                "attempts": [],
                "blocked_reason": None,
                "created_utc": self._now().isoformat(),
            }
            self._write(data)

    def _now(self):
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise AttemptDenied("V6_CLOCK_INVALID")
        return now.astimezone(timezone.utc)

    def _used(self, data, activity=None):
        activity = activity or self._activity(data)
        return self._legacy_used(data) + _record_token_occupancy(activity["attempts"], allow_released=True)

    def validate_request(self, prompt, timeout_seconds, max_tokens, *, model):
        del prompt  # Public prompt identity is frozen by the caller's request budget.
        now = self._now()
        if (model != self.grant.model or type(max_tokens) is not int
                or max_tokens not in self.allowed_output_caps
                or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 120
                or self.not_before is not None and now<self.not_before
                or self.deadline is not None and now.timestamp() + timeout_seconds > self.deadline.timestamp()):
            raise AttemptDenied("V6_REQUEST_BOUND_INVALID")

    def _validate_budget(self, budget):
        if type(budget) is not TokenRequestBudget:
            raise AttemptDenied("V6_REQUEST_BUDGET_REQUIRED")
        if (budget.activity_id != self.grant.activity_id or budget.scope != self.grant.scope
                or budget.provider != self.grant.provider or budget.model != self.grant.model
                or type(budget.input_token_bound) is not int or budget.input_token_bound <= 0
                or type(budget.output_token_bound) is not int
                or budget.output_token_bound != self.grant.output_token_cap
                or type(budget.safety_tokens) is not int or budget.safety_tokens < 0
                or type(budget.window_tokens) is not int or budget.window_tokens <= 0
                or budget.window_tokens != V6_CONTEXT_WINDOW_TOKENS
                or budget.capacity_source != V6_CAPACITY_SOURCE
                or budget.tokenizer_source != V6_TOKENIZER_SOURCE
                or budget.protocol_id != V6_REQUEST_PROTOCOL
                or not isinstance(budget.wire_request_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", budget.wire_request_sha256)
                or not isinstance(budget.prompt_text, str)
                or not isinstance(budget.special_token_literals, tuple)
                or not budget.special_token_literals
                or any(not isinstance(token, str) or not token for token in budget.special_token_literals)
                or not {"<｜User｜>", "<think>", "｜DSML｜"}.issubset(set(budget.special_token_literals))
                or _SPECIAL_TOKEN_CANDIDATE.search(budget.prompt_text)
                or any(token in budget.prompt_text for token in budget.special_token_literals)
                or budget.input_token_bound + budget.output_token_bound + budget.safety_tokens > budget.window_tokens):
            raise AttemptDenied("V6_REQUEST_BUDGET_INVALID")

    def reserve(self, *, request_id=None, budget=None):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
            raise AttemptDenied("SESSION_REQUEST_ID_INVALID")
        self._validate_budget(budget)
        with self._locked():
            now = self._now()
            if self.not_before is not None and now<self.not_before:
                raise AttemptDenied('V6_EXECUTION_LEASE_NOT_STARTED')
            if self.deadline is not None and now >= self.deadline:
                raise AttemptDenied("V6_ACTIVITY_DEADLINE_EXPIRED")
            data = self._read()
            activity = self._activity(data)
            if any(row.get("request_id") == request_id
                   for row in data["attempts"] + activity["attempts"]):
                raise AttemptDenied("SESSION_REQUEST_ALREADY_RESERVED")
            used = self._used(data, activity)
            reserve_input = budget.input_token_bound + budget.safety_tokens
            reserve_total = reserve_input + budget.output_token_bound
            if used + reserve_total > self.grant.token_limit:
                raise AttemptDenied("V6_TOKEN_CAP")
            attempt_id = uuid.uuid4().hex
            budget_record = self._budget_record(budget)
            activity["attempts"].append({
                "attempt_id": attempt_id,
                "activity_id": self.grant.activity_id,
                "grant_id": self.grant.grant_id,
                "request_id": request_id,
                "status": "reserved",
                "reserved_utc": now.isoformat(),
                "request_budget": budget_record,
                "validation_tokens": {
                    "state": "reserved",
                    "reserved_input": reserve_input,
                    "reserved_output": budget.output_token_bound,
                    "safety_tokens": budget.safety_tokens,
                },
            })
            self._write(data)
            return attempt_id

    @staticmethod
    def _budget_record(budget):
        record = asdict(budget)
        prompt_text = record.pop('prompt_text')
        record.pop('special_token_literals')
        record['prompt_sha256'] = hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()
        record['special_token_policy'] = V6_SPECIAL_TOKEN_POLICY
        return record

    def token_receipt(self, attempt_id, *, request_id, budget):
        """Read back this gate's exact canonical row; does not reserve or settle."""
        self._validate_budget(budget)
        with self._locked():
            data = self._read()
            activity = self._activity(data)
            row = next((item for item in activity['attempts'] if item.get('attempt_id') == attempt_id), None)
            if (row is None or row.get('request_id') != request_id
                    or row.get('grant_id') != self.grant.grant_id
                    or row.get('activity_id') != self.grant.activity_id
                    or row.get('request_budget') != self._budget_record(budget)):
                raise AttemptDenied('V6_TOKEN_RECEIPT_BINDING_INVALID')
            return json.loads(json.dumps(row))

    def _release_not_sent(self, data, activity, row):
        if row.get("status") != "reserved":
            return False
        row["status"] = "not_sent"
        row["finished_utc"] = self._now().isoformat()
        row["validation_tokens"]["state"] = "released"
        row["validation_tokens"].pop("input", None)
        row["validation_tokens"].pop("output", None)
        self._write(data)
        return True

    def validate_before_send(self, attempt_id):
        with self._locked():
            data = self._read()
            activity = self._activity(data)
            row = next((item for item in activity["attempts"]
                        if item.get("attempt_id") == attempt_id), None)
            if row is None:
                raise AttemptDenied("SESSION_ATTEMPT_UNKNOWN")
            if row.get("status") != "reserved":
                raise AttemptDenied("SESSION_ATTEMPT_NOT_RESERVED")
            if self.not_before is not None and self._now()<self.not_before:
                self._release_not_sent(data,activity,row)
                raise AttemptDenied('V6_EXECUTION_LEASE_NOT_STARTED')
            if self.deadline is not None and self._now() >= self.deadline:
                self._release_not_sent(data, activity, row)
                raise AttemptDenied("V6_ACTIVITY_DEADLINE_EXPIRED")
            if self.grant.execution_kind != "REAL":
                raise AttemptDenied("V6_SIMULATED_SEND_DISABLED")

    def finish(self, attempt_id, status):
        if status == "not_sent":
            with self._locked():
                data = self._read()
                activity = self._activity(data)
                row = next((item for item in activity["attempts"]
                            if item.get("attempt_id") == attempt_id), None)
                if row is None:
                    raise AttemptDenied("SESSION_ATTEMPT_UNKNOWN")
                self._release_not_sent(data, activity, row)
            return
        self.finish_with_usage(attempt_id, status, {})

    def finish_with_usage(self, attempt_id, status, usage):
        if status not in ("ok", "unknown", "truncated"):
            raise AttemptDenied("SESSION_ATTEMPT_STATUS_INVALID")
        with self._locked():
            data = self._read()
            activity = self._activity(data)
            row = next((item for item in activity["attempts"]
                        if item.get("attempt_id") == attempt_id), None)
            if row is None:
                raise AttemptDenied("SESSION_ATTEMPT_UNKNOWN")
            if row.get("status") != "reserved":
                return
            tokens = row["validation_tokens"]
            row["status"] = status
            row["finished_utc"] = self._now().isoformat()
            tokens["state"] = "unknown"
            if isinstance(usage, dict) and status == "ok":
                input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
                total_tokens = usage.get("total_tokens")
                exceeded = ((type(input_tokens) is int and input_tokens > tokens["reserved_input"])
                            or (type(output_tokens) is int and output_tokens > tokens["reserved_output"])
                            or (type(total_tokens) is int and total_tokens > tokens["reserved_input"] + tokens["reserved_output"]))
                valid = (type(input_tokens) is int and type(output_tokens) is int
                         and input_tokens >= 0 and output_tokens >= 0
                         and input_tokens <= tokens["reserved_input"]
                         and output_tokens <= tokens["reserved_output"]
                         and (total_tokens is None or type(total_tokens) is int
                              and total_tokens == input_tokens + output_tokens))
                if exceeded:
                    activity["blocked_reason"] = "V6_USAGE_BOUND_EXCEEDED"
                if valid:
                    tokens.update(state="settled", input=input_tokens, output=output_tokens)
            self._write(data)
            if status == "ok" and tokens["state"] != "settled":
                raise AttemptDenied("V6_USAGE_UNVERIFIED")


class ValidationAttemptGate(SessionAttemptGate):
    allowed_output_caps = (512,)
    def __init__(self, ledger_path, *, model="deepseek-flash", scope=SCOPE,
                 input_tokens_cap=None, max_output_tokens=512):
        super().__init__(ledger_path)
        self.input_cap = input_upper_bound(SYNTHETIC_PROMPT) if input_tokens_cap is None else input_tokens_cap
        self.output_cap = max_output_tokens
        if (scope != SCOPE or model not in PEAK_RATES or type(self.input_cap) is not int
                or type(max_output_tokens) is not int
                or not input_upper_bound(SYNTHETIC_PROMPT) <= self.input_cap <= 1_000_000 - max_output_tokens
                or max_output_tokens not in self.allowed_output_caps):
            raise AttemptDenied("VALIDATION_BOUND_INVALID")
        self.model = model

    def validate_request(self, prompt, timeout_seconds, max_tokens, *, model):
        if (prompt != SYNTHETIC_PROMPT or model != self.model
                or type(max_tokens) is not int or max_tokens != self.output_cap
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30
                or input_upper_bound(prompt) > self.input_cap):
            raise AttemptDenied("VALIDATION_REQUEST_BOUND_INVALID")

    def _used(self, data):
        policy = data.get("validation_policy")
        if (not isinstance(policy, dict) or policy != POLICY
                or any(type(policy.get(k)) is not type(v) for k, v in POLICY.items())):
            raise AttemptDenied("VALIDATION_POLICY_UNAVAILABLE")
        total = 0
        for row in data["attempts"]:
            tokens, cost = row.get("validation_tokens"), row.get("validation_cost")
            if not isinstance(tokens, dict) or not isinstance(cost, dict):
                raise AttemptDenied("LEGACY_ATTEMPT_COST_OR_TOKENS_UNDEFINED")
            if (cost.get("currency") != "USD" or type(cost.get("scale")) is not int
                    or cost.get("scale") != 1_000_000
                    # Only a successful attempt with verified usage may settle.
                    # An ok attempt with unverified usage still occupies its bound.
                    or (row.get("status"), tokens.get("state")) not in (
                        ("reserved", "reserved"), ("ok", "settled"), ("ok", "unknown"),
                        ("unknown", "unknown"), ("truncated", "unknown"),
                    )
                    or tokens.get("state") not in ("reserved", "unknown", "settled")
                    or any(type(tokens.get(k)) is not int or tokens[k] < 0
                           for k in ("reserved_input", "reserved_output"))
                    or type(tokens["reserved_output"]) is not int
                    # Preserve legacy bounds and the observed 1280 generation cap.
                    # New requests still use their own gate's allowed_output_caps.
                    or not (0 < tokens["reserved_output"] <= 1024 or tokens["reserved_output"] == 1280)
                    or not 0 < tokens["reserved_input"] <= 1_000_000 - tokens["reserved_output"]):
                raise AttemptDenied("VALIDATION_RECORD_INVALID")
            if tokens["state"] == "settled":
                a,b = tokens.get("input"),tokens.get("output")
                if (type(a) is not int or type(b) is not int or not 0 <= a <= tokens["reserved_input"]
                        or not 0 <= b <= tokens["reserved_output"]):
                    raise AttemptDenied("VALIDATION_RECORD_INVALID")
                total += a+b
            else:
                total += tokens["reserved_input"]+tokens["reserved_output"]
        if total > 1_000_000:
            raise AttemptDenied("VALIDATION_RECORD_INVALID")
        return total

    def initialize_disabled_policy(self):
        # Explicit setup only; never enables or resets the shared formal ledger.
        with self._locked():
            data=self._read()
            if data.get("calls_allowed_in_this_task") is not False:
                raise AttemptDenied("VALIDATION_INITIALIZE_REQUIRES_DISABLED")
            if data["attempts"] and data.get("validation_policy") != POLICY:
                raise AttemptDenied("LEGACY_ATTEMPT_COST_OR_TOKENS_UNDEFINED")
            if "validation_policy" in data and data["validation_policy"] != POLICY:
                raise AttemptDenied("VALIDATION_POLICY_INVALID")
            data["validation_policy"]=dict(POLICY)
            self._used(data)
            self._write(data)

    def reserve(self, *, request_id=None):
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}",request_id):
            raise AttemptDenied("SESSION_REQUEST_ID_INVALID")
        with self._locked():
            data=self._read()
            if data.get("calls_allowed_in_this_task") is not True:
                raise AttemptDenied("SESSION_CALLS_DISABLED")
            self._require_attempt_authorization(data)
            if data.get("validation_blocked_reason"):
                raise AttemptDenied("VALIDATION_USAGE_BOUND_EXCEEDED")
            used=self._used(data)
            self._admit(data,request_id)
            if any(a.get("request_id")==request_id for a in data["attempts"]):
                raise AttemptDenied("SESSION_REQUEST_ALREADY_RESERVED")
            self._require_attempt_capacity(data)
            if used+self.input_cap+self.output_cap>1_000_000:
                raise AttemptDenied("VALIDATION_TOKEN_CAP")
            attempt=uuid.uuid4().hex
            data["consumed_attempts"]+=1
            data["reserved_attempts"]+=1
            rates=PEAK_RATES[self.model]
            data["attempts"].append({"attempt_id":attempt,"status":"reserved","request_id":request_id,
                "reserved_utc":datetime.now(timezone.utc).isoformat(),
                "validation_tokens":{"reserved_input":self.input_cap,"reserved_output":self.output_cap,"state":"reserved"},
                "validation_cost":{"currency":"USD","scale":1_000_000,"model":self.model,
                    "reserved_upper_bound_micro_usd":quote_micro_usd(rates,input_tokens=self.input_cap,output_tokens=self.output_cap),
                    "price_source":rates.source,"price_verified_utc":rates.verified_utc,
                    "peak_input_miss_usd_per_million":str(rates.input_miss),
                    "peak_input_hit_usd_per_million":str(rates.input_hit),
                    "peak_output_usd_per_million":str(rates.output),
                    "state":"unknown_charge","actual_charge_verified":False}})
            data["attempts"][-1].update(self._reservation_fields(data))
            self._record_reserved_attempt(data)
            self._write(data)
            return attempt

    def _admit(self, data, request_id):
        pass

    def _reservation_fields(self, data):
        return {}

    def _record_reserved_attempt(self, data):
        pass

    def finish(self, attempt_id, status):
        self.finish_with_usage(attempt_id,status,{})

    def finish_with_usage(self, attempt_id, status, usage):
        if status not in ("ok","unknown","truncated"):
            raise AttemptDenied("SESSION_ATTEMPT_STATUS_INVALID")
        with self._locked():
            data=self._read();self._used(data)
            row=next((a for a in data["attempts"] if a["attempt_id"]==attempt_id),None)
            if row is None:raise AttemptDenied("SESSION_ATTEMPT_UNKNOWN")
            if row["status"]!="reserved":return
            row["status"]=status;row["finished_utc"]=datetime.now(timezone.utc).isoformat()
            data["reserved_attempts"]-=1
            tokens=row["validation_tokens"];tokens["state"]="unknown"
            if isinstance(usage,dict) and status=="ok":
                a,b=usage.get("prompt_tokens"),usage.get("completion_tokens")
                total=usage.get("total_tokens")
                if ((type(a) is int and a>tokens["reserved_input"])
                        or (type(b) is int and b>tokens["reserved_output"])
                        or (type(total) is int and total>tokens["reserved_input"]+tokens["reserved_output"])):
                    data["validation_blocked_reason"]="VALIDATION_USAGE_BOUND_EXCEEDED"

                valid=(type(a) is int and type(b) is int and a>=0 and b>=0
                       and a<=tokens["reserved_input"] and b<=tokens["reserved_output"]
                       and (total is None or type(total) is int and total==a+b))
                if valid:
                    tokens.update(state="settled",input=a,output=b)
                    cached=usage.get("prompt_cache_hit_tokens",0)
                    if type(cached) is not int or not 0<=cached<=a:cached=0
                    cost=row["validation_cost"]
                    cost.update(state="usage_observed_charge_unverified",
                        observed_usage_peak_upper_bound_micro_usd=quote_micro_usd(PEAK_RATES[self.model],
                            input_tokens=a,output_tokens=b,cached_input_tokens=cached))
            self._write(data)
            if status=="ok" and tokens["state"]!="settled":
                raise AttemptDenied("VALIDATION_USAGE_UNVERIFIED")
