"""Four cloud capabilities over existing ports, with independent admission/usage.

No retries, URL discovery, redirects, proxies, fallback models or content logs.
Adapters stay denied without a trusted scope AND a role-specific usage guard.
"""
import base64
import json
import hashlib
import math
import os
import socket
import ssl
import threading
import time
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from backend.app.domain.embedding_identity import EmbeddingIdentity
from backend.app.domain.model_registry import ModelSpec
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import scope_allows
from backend.app.ports.model_usage import record_provider_call
from backend.app.ports.provider_send_claims import ProviderSendClaims, claim_matches
from backend.app.ports.ranking import RerankResult
from backend.app.ports.xing_zero_reservation import (
    SiliconFlowFreePermit, SiliconFlowFreeBinding, SiliconFlowZeroReservation,
    FreeModelRequestContext, current_free_request,
    free_dispatch_contract,free_dispatch_identity_sha256,
)
from backend.app.ports.providers import (
    CaptionResult, ChatResult, EmbeddingResult, ProviderRequestNotSent,
    ProviderUnavailable, TruncatedAnswer,
)
from backend.app.adapters.models.ollama import validate_caption_image


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DiagnosticFailure(ProviderUnavailable):
    """Internal fixed codes only; callers still receive ProviderUnavailable."""
    def __init__(self, public_code, stage, code, response_diagnostics=None):
        super().__init__(public_code)
        self.public_code, self.stage, self.code = public_code, stage, code
        self.response_diagnostics = response_diagnostics


class _ReceivedResponse:
    """Decoded response plus safe transport metadata; never contains raw bytes."""
    __slots__ = ('payload', 'http_status_code', 'response_bytes', 'content_type')

    def __init__(self, payload, http_status_code, response_bytes, content_type=None):
        self.payload = payload
        self.http_status_code = http_status_code
        self.response_bytes = response_bytes
        self.content_type = content_type


def _safe_content_type(value):
    if value is None: return None
    if not isinstance(value, str): return 'other'
    mime = value.split(';', 1)[0].strip().lower()
    return mime if mime in {'application/json', 'text/plain', 'text/html',
                           'application/octet-stream'} else 'other'


def _json_type(value):
    if value is None: return 'null'
    if type(value) is bool: return 'boolean'
    if type(value) in (int, float): return 'number'
    if isinstance(value, str): return 'string'
    if isinstance(value, list): return 'array'
    if isinstance(value, dict): return 'object'
    return 'other'


def _response_diagnostics(payload=None, *, received=False, http_status_code=None,
                          response_bytes=None, content_type=None, expected_results=None):
    top_type = _json_type(payload) if received else 'unavailable'
    if isinstance(payload, dict):
        fields = {name: {'present': name in payload,
                         'type': _json_type(payload[name]) if name in payload else 'missing'}
                  for name in ('results', 'model', 'error', 'data')}
        rows = payload.get('results')
        actual_results = len(rows) if isinstance(rows, list) else None
    else:
        fields = {name: {'present': False, 'type': 'unavailable'}
                  for name in ('results', 'model', 'error', 'data')}
        actual_results = None
    document_type = 'unavailable'
    if isinstance(payload, dict) and isinstance(payload.get('results'), list) and payload['results']:
        first = payload['results'][0]
        if isinstance(first, dict):
            document_type = _json_type(first['document']) if 'document' in first else 'missing'
    return dict(http_status_code=http_status_code if type(http_status_code) is int
                    and 100 <= http_status_code <= 599 else None,
        response_bytes=response_bytes if type(response_bytes) is int
                    and 0 <= response_bytes <= 2_000_001 else None,
        content_type=_safe_content_type(content_type),
        top_level_type=top_type, fields=fields, expected_results=expected_results,
        actual_results=actual_results, first_failure_row=None,
        failure_reason=None, document_type=document_type)


def _merge_response_diagnostics(transport, detail, expected_results):
    # Structure detail wins only for fixed structural fields. Observed transport
    # metadata wins when present; an absent value never invents a status/length.
    base = transport or _response_diagnostics(expected_results=expected_results)
    detail = detail or {}
    merged = {key: detail.get(key, value) for key, value in base.items()}
    for key in ('http_status_code', 'response_bytes', 'content_type'):
        if base.get(key) is not None:
            merged[key] = base[key]
    merged['expected_results'] = expected_results
    return merged


def _transport_error_code(error):
    # urllib wraps socket/TLS exceptions in URLError.reason. Never classify
    # by text, retain the exception, or export URLs/headers/provider messages.
    for _ in range(4):
        if isinstance(error, URLError) and isinstance(error.reason, BaseException):
            error = error.reason
        else:
            break
    if isinstance(error, socket.gaierror): return 'DNS_FAILED'
    if isinstance(error, ssl.SSLCertVerificationError): return 'TLS_CERTIFICATE_FAILED'
    if isinstance(error, ssl.SSLError): return 'TLS_HANDSHAKE_FAILED'
    if isinstance(error, TimeoutError): return 'REQUEST_TIMEOUT'
    if isinstance(error, (OSError, URLError)): return 'NETWORK_CONNECTION_FAILED'
    return 'TRANSPORT_FAILED'


def _send(request, timeout):
    with build_opener(_NoRedirect(), ProxyHandler({})).open(request, timeout=timeout) as response:
        status = response.getcode()
        if type(status) is not int or not 100 <= status <= 599:
            status = None
        headers = getattr(response, 'headers', None)
        content_type = _safe_content_type(headers.get('Content-Type') if headers is not None else None)
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise _DiagnosticFailure('PROVIDER_RESPONSE_TOO_LARGE', 'response_decode', 'RESPONSE_TOO_LARGE',
            _response_diagnostics(http_status_code=status, response_bytes=len(raw), content_type=content_type))
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _DiagnosticFailure('MODEL_REQUEST_FAILED', 'response_decode', 'JSON_RESPONSE_INVALID',
            _response_diagnostics(http_status_code=status, response_bytes=len(raw), content_type=content_type)) from None
    return _ReceivedResponse(payload, status, len(raw), content_type)


def _usage(response):
    raw = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        return None
    return {key: value if type(value) is int and value >= 0 else None
            for key, value in raw.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens"}}


def _rerank_usage(response):
    observed = _usage(response)
    if observed:
        return observed
    meta = response.get('meta')
    tokens = meta.get('tokens') if isinstance(meta, dict) else None
    if not isinstance(tokens, dict):
        return None
    counts = [tokens.get('input_tokens'), tokens.get('output_tokens')]
    counts = [n if type(n) is int and n >= 0 else None for n in counts]
    return dict(prompt_tokens=counts[0], completion_tokens=counts[1],
                total_tokens=sum(counts) if all(n is not None for n in counts) else None)


class EmbeddingAdmission:
    """Verified model tokenizer contract, deliberately absent by default.

    Counter counts special tokens and MUST disable tokenizer truncation. The
    pinned BGE path implements R4's explicitly bounded client policy while
    retaining UNKNOWN provider-internal non-truncation. Other counters still
    require their independently reviewed contract.
    Tests inject an explicitly SIMULATED counter, never a character estimate.
    Batch/request caps are local limits, not claims about supplier capacity.
    """
    def __init__(self, counter=None, *, tokenizer_identity="UNKNOWN", non_truncating_verified=False,
                 max_batch_items=32):
        self.counter = counter
        self.tokenizer_identity = tokenizer_identity
        self.non_truncating_verified = non_truncating_verified
        self.max_batch_items = max_batch_items
        if type(max_batch_items) is not int or not 1 <= max_batch_items <= 32:
            raise ValueError("EMBEDDING_BATCH_POLICY_INVALID")

    def count(self, texts):
        if not texts or len(texts) > self.max_batch_items or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ProviderRequestNotSent("EMBEDDING_INPUT_INVALID")
        from backend.app.adapters.models.bge_tokenizer import PinnedBgeM3Tokenizer
        bounded = (type(self.counter) is PinnedBgeM3Tokenizer
                   and self.tokenizer_identity == self.counter.identity)
        if (self.counter is None or self.tokenizer_identity == "UNKNOWN"
                or (not self.non_truncating_verified and not bounded)):
            raise ProviderRequestNotSent("EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN")
        counts = [self.counter(t) for t in texts]
        limit = self.counter.max_input_tokens if bounded else 8192
        if any(type(n) is not int or not 0 < n <= limit for n in counts):
            raise ProviderRequestNotSent("EMBEDDING_TOKEN_LIMIT_EXCEEDED")
        return sum(counts)

    @classmethod
    def from_bge_m3_file(cls, path):
        """Owner-authorized bounded inputs; never claim provider proof.

        Default construction stays closed. Only a hash-verified official
        tokenizer admits this narrower client limit with explicit UNKNOWN
        supplier behavior, as permitted by P4_PRE-R4.
        """
        from backend.app.adapters.models.bge_tokenizer import PinnedBgeM3Tokenizer
        counter = PinnedBgeM3Tokenizer(path)
        return cls(counter, tokenizer_identity=counter.identity,
                   non_truncating_verified=False)


XING_PURPOSES = frozenset({'answer', 'understand', 'rewrite', 'expansion', 'calibration'})
XING_CALIBRATION_REQUEST_ID = 'v6-xing-public-smoke-r1-001'
XING_CALIBRATION_SCOPE = 'PUBLIC-XING-SMOKE-PROPOSAL'
XING_CALIBRATION_DEADLINE = '2026-10-10T04:25:44Z'
XING_CALIBRATION_WIRE_SHA256 = '5d5037c8c94d0364bc650d82a5412c345af539133bf5542b5f1fc892f4855399'
XING_PRICE_SOURCE_SHA256 = '59c932ec3e6c602d753299cee3ce5a11dc03addcc4b177b768b33d016241b530'
XING_OWNER_CALIBRATION_REQUEST_ID = 'v6-xing-calibration-20261009-r1'


@dataclass(frozen=True)
class XingCalibrationIdentity:
    run_id: str
    request_id: str
    scope: str = XING_CALIBRATION_SCOPE
    arm: str = 'CHEAP_ONLY'

    def validate(self):
        if (type(self) is not XingCalibrationIdentity or type(self.run_id) is not str
                or type(self.request_id) is not str or self.scope != XING_CALIBRATION_SCOPE
                or self.arm != 'CHEAP_ONLY'):
            raise ProviderRequestNotSent('XING_CALIBRATION_IDENTITY_INVALID')
        if (self.run_id, self.request_id) == ('v6-xing-public-smoke-r1', XING_CALIBRATION_REQUEST_ID):
            return  # preserved offline proposal/template contract
        from uuid import UUID
        try:
            valid_run = str(UUID(self.run_id)) == self.run_id
        except (ValueError, AttributeError):
            valid_run = False
        if self.request_id != XING_OWNER_CALIBRATION_REQUEST_ID or not valid_run:
            raise ProviderRequestNotSent('XING_CALIBRATION_IDENTITY_INVALID')


LEGACY_XING_CALIBRATION_IDENTITY = XingCalibrationIdentity('v6-xing-public-smoke-r1', XING_CALIBRATION_REQUEST_ID)


@dataclass(frozen=True)
class XingZeroPriceContract:
    """Frozen exact-model quote, not verified billing or hosted capacity."""
    source_sha256: str = XING_PRICE_SOURCE_SHA256
    source_url: str = 'https://www.siliconflow.cn/models'
    provider: str = 'siliconflow'
    model: str = 'XingChenAGI/Xing4.0-29B'
    currency: str = 'CNY'
    prompt_per_million: int = 0
    completion_per_million: int = 0

    @classmethod
    def from_frozen_html(cls, path):
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != XING_PRICE_SOURCE_SHA256:
            raise ValueError('XING_PRICE_SOURCE_UNVERIFIED')
        return cls()

    def validate(self):
        if (type(self) is not XingZeroPriceContract
                or self.source_sha256 != XING_PRICE_SOURCE_SHA256
                or self.source_url != 'https://www.siliconflow.cn/models'
                or (self.provider, self.model, self.currency) !=
                    ('siliconflow', 'XingChenAGI/Xing4.0-29B', 'CNY')
                or type(self.prompt_per_million) is not int or self.prompt_per_million != 0
                or type(self.completion_per_million) is not int or self.completion_per_million != 0):
            raise ProviderRequestNotSent('XING_PRICE_SOURCE_UNVERIFIED')


@dataclass(frozen=True)
class _XingCalibrationTicket:
    identity_sha256: str
    identity: object = field(repr=False)
    reservation_id: str
    provider_claim: object = field(repr=False)


class XingCohortCalibrationClaimStore:
    """Reuse the normal cohort provider-child claim, never initialize a DB/Run.

    Explicit composition registers this fixed Run in the existing center file.
    The cohort callback owns its atomic provider child (and calibration parent).
    General helper wiring is outside this one-request calibration contract.
    """
    run_id = 'v6-xing-public-smoke-r1'
    qid = XING_CALIBRATION_REQUEST_ID
    arm = 'CHEAP_ONLY'

    def __init__(self, provider_send_claim, *, identity=LEGACY_XING_CALIBRATION_IDENTITY):
        if not isinstance(provider_send_claim, ProviderSendClaims):
            raise ValueError('XING_EXISTING_COHORT_STATE_REQUIRED')
        self.callback = provider_send_claim
        if type(identity) is not XingCalibrationIdentity:
            raise ValueError('XING_CALIBRATION_IDENTITY_INVALID')
        identity.validate()
        self._identity = identity
        self._issued = {}

    @property
    def identity(self):
        return self._identity

    def claim_dispatch(self, identity, reservation_id, *, now_utc):
        self.identity.validate()
        if identity.get('request_id') != self.identity.request_id:
            raise ProviderRequestNotSent('XING_CALIBRATION_IDENTITY_INVALID')
        identity = dict(identity, calibration_run_id=self.identity.run_id,
                        calibration_qid=self.identity.request_id, calibration_arm=self.identity.arm)
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        claim = self.callback(request_id=self.identity.request_id, purpose='calibration',
            provider='siliconflow', model='XingChenAGI/Xing4.0-29B', role='chat_cheap',
            request_identity_sha256=digest, call_ordinal=1)
        if (not claim_matches(claim, request_id=self.identity.request_id, purpose='calibration',
                provider='siliconflow', model='XingChenAGI/Xing4.0-29B', role='chat_cheap', identity_sha256=digest)
                or (claim.run_id, claim.qid, claim.arm) != (self.identity.run_id, self.identity.request_id, self.identity.arm)):
            raise ProviderRequestNotSent('XING_DURABLE_CLAIM_INVALID')
        ticket = _XingCalibrationTicket(digest, dict(identity), str(reservation_id), claim)
        self._issued[digest] = ticket
        return ticket

    def validate_claim(self, ticket, *, now_utc):
        if type(ticket) is not _XingCalibrationTicket or self._issued.get(ticket.identity_sha256) is not ticket:
            raise ProviderRequestNotSent('XING_DURABLE_CLAIM_INVALID')
        if (not claim_matches(ticket.provider_claim, request_id=self.identity.request_id,
                purpose='calibration', provider='siliconflow', model='XingChenAGI/Xing4.0-29B',
                role='chat_cheap', identity_sha256=ticket.identity_sha256)
                or (ticket.provider_claim.run_id, ticket.provider_claim.qid, ticket.provider_claim.arm) !=
                    (self.identity.run_id, self.identity.request_id, self.identity.arm)):
            raise ProviderRequestNotSent('XING_DURABLE_CLAIM_INVALID')

    def finish_dispatch(self, ticket, receipt):
        if type(ticket) is not _XingCalibrationTicket or self._issued.get(ticket.identity_sha256) is not ticket:
            raise ProviderUnavailable('XING_DURABLE_CLAIM_INVALID')
        state = ('blocked' if receipt['status'] == 'not_sent' else
                 'simulated' if receipt['status'] == 'ok' and receipt['execution_kind'] == 'SIMULATED' else
                 'succeeded' if receipt['status'] == 'ok' else 'unknown')
        self.callback.settle(request_id=self.identity.request_id,
            status=state, result={'calibration_dispatch': dict(receipt),
                'dispatch_contract': ticket.identity, 'budget_reservation_id': ticket.reservation_id,
                'dispatch_identity_sha256': ticket.identity_sha256})


@dataclass(frozen=True)
class XingCalibrationPermit:
    price_contract: XingZeroPriceContract
    claim_store: XingCohortCalibrationClaimStore

    def validate(self, grant, estimate, request_id, timeout_seconds):
        if (type(self) is not XingCalibrationPermit or type(self.price_contract) is not XingZeroPriceContract
                or type(self.claim_store) is not XingCohortCalibrationClaimStore):
            raise ProviderRequestNotSent('XING_CALIBRATION_AUTHORITY_INVALID')
        self.price_contract.validate()
        identity = self.claim_store.identity
        identity.validate()
        if (grant.allowed_purposes != ('calibration',) or type(grant.scope) is not str
                or grant.scope != XING_CALIBRATION_SCOPE or grant.scope_id != XING_CALIBRATION_SCOPE
                or grant.deadline_utc != XING_CALIBRATION_DEADLINE
                or request_id != identity.request_id or timeout_seconds != 10
                or identity.request_id == XING_OWNER_CALIBRATION_REQUEST_ID
                    and grant.grant_id != 'owner-v6-xing-calibration-20261009-r1'
                or estimate.wire_request_sha256 != XING_CALIBRATION_WIRE_SHA256
                or not 0 < estimate.reservation_bound <= 32 or estimate.output_reservation != 32):
            raise ProviderRequestNotSent('XING_CALIBRATION_CONTRACT_MISMATCH')


@dataclass(frozen=True)
class XingChatGrant:
    """Explicit scoped SF authorization; no DeepSeek quota or hosted proof."""
    grant_id: str
    scope_id: str
    scope: object
    deadline_utc: str | None
    allowed_purposes: tuple[str, ...] = ('answer',)
    execution_kind: str = 'REAL'


@dataclass(frozen=True)
class SimulatedXingSend:
    """Fixture response only; never selected by normal factory startup."""
    send: object = field(repr=False)
    execution_kind = 'SIMULATED'


class XingAuthorizedTransport:
    """Scoped, once-only wrapper on the normal CloudChat transport boundary.

    Ordinary REAL admission stays closed. Explicit fixed calibration uses the
    normal cohort durable claim and a frozen quote, without hosted-bound claims.
    """
    @staticmethod
    def validate_grant(grant):
        """Pure grant shape check; transport construction still requires its permit."""
        if (type(grant) is not XingChatGrant
                or not isinstance(grant.grant_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', grant.grant_id)
                or type(grant.scope_id) is not str or not grant.scope_id.strip()
                or type(grant.scope) not in (str, Scope)
                or type(grant.scope) is str and grant.scope != grant.scope_id
                or type(grant.scope) is Scope and (type(grant.scope.knowledge_base_ids) is not frozenset
                    or type(grant.scope.document_ids) is not frozenset)
                or type(grant.scope) is Scope and not grant.scope.knowledge_base_ids
                or type(grant.scope) is Scope and any(type(x) is not str or not x
                    for x in grant.scope.knowledge_base_ids | grant.scope.document_ids)
                or type(grant.allowed_purposes) is not tuple or not grant.allowed_purposes
                or any(type(p) is not str or p not in XING_PURPOSES for p in grant.allowed_purposes)
                or len(set(grant.allowed_purposes)) != len(grant.allowed_purposes)
                or grant.execution_kind not in ('REAL', 'SIMULATED')):
            raise ValueError('XING_GRANT_INVALID')

    def __init__(self, grant, *, sender=None, clock=None, calibration_permit=None, send_claim_callback=None,
                 free_model_permit=None):
        self.validate_grant(grant)
        try:
            if grant.deadline_utc is None:
                from backend.app.domain.model_registry import ModelRegistry
                if (type(free_model_permit) is not SiliconFlowFreePermit
                        or free_model_permit.owner_activity_id is None
                        or free_model_permit.grant_id!=grant.grant_id or free_model_permit.scope!=grant.scope
                        or free_model_permit.deadline_utc is not None):
                    raise ValueError()
                free_model_permit.validate(ModelRegistry.frozen_defaults().select('chat_cheap'))
                deadline=None
            else:
                deadline = datetime.fromisoformat(grant.deadline_utc.replace('Z', '+00:00'))
                if deadline.tzinfo is None:
                    raise ValueError()
        except (AttributeError, TypeError, ValueError):
            raise ValueError('XING_GRANT_INVALID') from None
        if ((grant.execution_kind == 'SIMULATED') != (type(sender) is SimulatedXingSend)
                or sender is not None and type(sender) is not SimulatedXingSend):
            raise ValueError('XING_EXECUTION_KIND_MISMATCH')
        self.grant, self.deadline = grant, deadline
        self.sender, self.clock = sender, clock or (lambda: datetime.now(timezone.utc))
        self.execution_kind = grant.execution_kind
        if calibration_permit is not None and type(calibration_permit) is not XingCalibrationPermit:
            raise ValueError('XING_CALIBRATION_AUTHORITY_INVALID')
        self.calibration_permit = calibration_permit
        if send_claim_callback is None and calibration_permit is not None:
            send_claim_callback = calibration_permit.claim_store.callback
        if send_claim_callback is not None and not isinstance(send_claim_callback, ProviderSendClaims):
            raise ValueError('XING_SEND_CLAIM_CALLBACK_INVALID')
        if (calibration_permit is not None
                and send_claim_callback is not calibration_permit.claim_store.callback):
            raise ValueError('XING_SEND_CLAIM_CALLBACK_INVALID')
        self.send_claim_callback = send_claim_callback
        if free_model_permit is not None:
            from backend.app.domain.model_registry import ModelRegistry
            if calibration_permit is not None or type(free_model_permit) is not SiliconFlowFreePermit:
                raise ValueError('SF_FREE_PERMIT_INVALID')
            self.free_model_permit = free_model_permit
            free_model_permit.validate(ModelRegistry.frozen_defaults().select('chat_cheap'),self)
            if free_model_permit.execution_kind != self.execution_kind or send_claim_callback is None:
                raise ValueError('SF_FREE_PERMIT_INVALID')
        else:
            self.free_model_permit = None
        self._context = ContextVar('xing_authorized_request', default=None)
        self._calibration_claim = ContextVar('xing_calibration_claim', default=None)
        self._provider_claim = ContextVar('xing_provider_claim', default=None)
        self._lock = threading.Lock()
        self.attempts = {}
        self.blocked = False

    def check_scope(self, *, scope, purpose, timeout_seconds):
        if (self.blocked or type(scope) is not type(self.grant.scope) or scope != self.grant.scope
                or type(purpose) is not str or purpose not in self.grant.allowed_purposes):
            raise ProviderRequestNotSent('XING_SCOPE_OR_PURPOSE_DENIED')
        now = self.clock()
        deadline = self.free_model_permit.owner_deadline() if self.free_model_permit is not None else self.deadline
        lease = self.free_model_permit.execution_lease if self.free_model_permit is not None else None
        if (not isinstance(now, datetime) or now.tzinfo is None
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120
                or lease is not None and now<lease.not_before
                or deadline is not None and now.timestamp() + timeout_seconds > deadline.timestamp()):
            raise ProviderRequestNotSent('XING_DEADLINE_OR_TIMEOUT_INVALID')

    def validate_capacity_profile(self,estimate):
        estimate.validate_profile(self.grant)
        if (self.free_model_permit is not None and self.free_model_permit.owner_activity_id is not None
                and estimate.capacity_profile.execution_lease is not self.free_model_permit.execution_lease):
            raise ProviderRequestNotSent('XING_EXECUTION_LEASE_PROFILE_MISMATCH')
        if self.execution_kind=='REAL':
            from backend.app.domain.model_registry import ModelRegistry
            if type(self.free_model_permit) is not SiliconFlowFreePermit or self.send_claim_callback is None:
                raise ProviderRequestNotSent('SF_FREE_PERMIT_REQUIRED')
            self.free_model_permit.validate(ModelRegistry.frozen_defaults().select('chat_cheap'),self)

    @contextmanager
    def authorize(self, estimate, *, scope, purpose, request_id, timeout_seconds):
        from backend.app.adapters.models.siliconflow_xing_capacity import XingCapacityEstimate
        self.check_scope(scope=scope, purpose=purpose, timeout_seconds=timeout_seconds)
        if (type(estimate) is not XingCapacityEstimate
                or estimate.scope_sha256 != hashlib.sha256(self.grant.scope_id.encode('utf-8')).hexdigest()
                or not estimate.fits_local_policy
                or type(request_id) is not str
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id)):
            raise ProviderRequestNotSent('XING_REQUEST_BINDING_INVALID')
        # Local execution proves the pinned recipe, not hosted token/CoT limits.
        # No bool or manually altered estimate can open a REAL calibration send.
        if purpose == 'calibration':
            if self.calibration_permit is None:
                raise ProviderRequestNotSent('XING_CALIBRATION_AUTHORITY_REQUIRED')
            self.calibration_permit.validate(self.grant, estimate, request_id, timeout_seconds)
        elif self.grant.execution_kind != 'SIMULATED':
            self.validate_capacity_profile(estimate)
        elif estimate.capacity_profile is not None:
            estimate.validate_profile(self.grant)
        token = self._context.set((estimate, scope, purpose, request_id, timeout_seconds))
        claim_token = self._calibration_claim.set(None)
        provider_claim_token = self._provider_claim.set(None)
        try:
            yield
        finally:
            self._provider_claim.reset(provider_claim_token)
            self._calibration_claim.reset(claim_token)
            self._context.reset(token)

    def validate_request(self, request, timeout):
        context = self._context.get()
        if context is None:
            raise ProviderRequestNotSent('XING_AUTHORIZED_CONTEXT_REQUIRED')
        estimate, scope, purpose, request_id, authorized_timeout = context
        self.check_scope(scope=scope, purpose=purpose, timeout_seconds=timeout)
        if request.full_url != 'https://api.siliconflow.cn/v1/chat/completions' or timeout != authorized_timeout:
            raise ProviderRequestNotSent('XING_TRANSPORT_ENDPOINT_OR_TIMEOUT_MISMATCH')
        estimate.assert_wire_bytes(request.data)
        if purpose!='calibration' and (self.execution_kind=='REAL' or estimate.capacity_profile is not None):
            self.validate_capacity_profile(estimate)
        with self._lock:
            if request_id in self.attempts:
                raise ProviderRequestNotSent('XING_REQUEST_ALREADY_CONSUMED')

    def __call__(self, request, timeout):
        self.validate_request(request, timeout)
        estimate, scope, purpose, request_id, _ = self._context.get()
        if purpose == 'calibration':
            self.calibration_permit.validate(self.grant, estimate, request_id, timeout)
            self.calibration_permit.claim_store.validate_claim(self._calibration_claim.get(),
                now_utc=self.clock().isoformat())
        elif self.send_claim_callback is not None and self._provider_claim.get() is None:
            raise ProviderRequestNotSent('XING_DURABLE_CLAIM_INVALID')
        with self._lock:
            if request_id in self.attempts:
                raise ProviderRequestNotSent('XING_REQUEST_ALREADY_CONSUMED')
            row = dict(execution_kind=self.execution_kind, request_id=request_id, purpose=purpose,
                wire_request_sha256=estimate.wire_request_sha256, scope_sha256=estimate.scope_sha256,
                send_status='UNKNOWN', response_actual_model='UNKNOWN', usage_actual=None,
                reasoning_present='UNKNOWN', reasoning_tokens='UNKNOWN')
            self.attempts[request_id] = row  # consumed before any uncertain send
        try:
            if self.send_claim_callback is not None:
                try:
                    self.send_claim_callback.check_before_send(request_id=request_id)
                except Exception:
                    raise ProviderRequestNotSent('XING_DURABLE_SEND_CHECK_DENIED') from None
            if purpose!='calibration' and (self.execution_kind=='REAL' or estimate.capacity_profile is not None):
                self.validate_capacity_profile(estimate)
            self.check_scope(scope=scope,purpose=purpose,timeout_seconds=timeout)
            response = (self.sender.send(request, timeout) if self.execution_kind == 'SIMULATED'
                        else _send(request, timeout))
            payload = response.payload if isinstance(response, _ReceivedResponse) else response
            if isinstance(payload, dict):
                row['send_status'] = 'RESPONSE_RECEIVED'
                row['response_actual_model'] = payload.get('model', 'UNKNOWN')
                row['usage_actual'] = _usage(payload)
                choices = payload.get('choices')
                message = choices[0].get('message', {}) if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                row['reasoning_present'] = bool(message.get('reasoning_content')) if isinstance(message, dict) else 'UNKNOWN'
                usage = payload.get('usage')
                details = usage.get('completion_tokens_details') if isinstance(usage, dict) else None
                count = details.get('reasoning_tokens') if isinstance(details, dict) else None
                if type(count) is int and count >= 0:
                    row['reasoning_tokens'] = count
            return response
        except ProviderRequestNotSent:
            row['send_status'] = 'NOT_SENT'
            raise


class XingChatAdmission:
    def __init__(self, *, grant, counter, transport):
        from backend.app.adapters.models.siliconflow_xing_capacity import SiliconFlowXingCapacityCounter
        if (type(grant) is not XingChatGrant or type(counter) is not SiliconFlowXingCapacityCounter
                or type(transport) is not XingAuthorizedTransport or transport.grant is not grant):
            raise ValueError('XING_BINDING_INVALID')
        self.grant, self.counter, self.transport = grant, counter, transport


class _XingRequestPolicy:
    def __init__(self, estimate, transport, purpose):
        if type(purpose) is not str or purpose not in XING_PURPOSES:
            raise ProviderRequestNotSent('XING_SCOPE_OR_PURPOSE_DENIED')
        self.estimate, self.transport = estimate, transport
        self.purpose = purpose
        self.usage_stage = {'understand': 'query_understand', 'answer': 'answer',
                           'rewrite': 'query', 'expansion': 'query', 'calibration': 'answer'}[purpose]
        self.ticket = None
        self.provider_claim = None

    @property
    def request_id(self): return self.transport._context.get()[3]
    @property
    def scope(self): return self.transport._context.get()[1]
    @property
    def timeout(self): return self.transport._context.get()[4]
    @property
    def wire_bytes(self): return self.estimate.wire_bytes
    @property
    def input_tokens(self): return self.estimate.reservation_bound
    @property
    def output_tokens(self): return self.estimate.output_reservation
    @property
    def item_count(self): return 1
    @property
    def input_profile_sha256(self): return hashlib.sha256(self.estimate.renderer_identity.encode()).hexdigest()

    def validate_active_context(self):
        context = self.transport._context.get()
        if context is None or context[0] is not self.estimate or context[2] != self.purpose:
            raise ValueError('SF_ZERO_CONTEXT_REQUIRED')
        self.transport.check_scope(scope=self.scope,purpose=self.purpose,timeout_seconds=self.timeout)
        # Zero pricing cannot supply the missing hosted counter/thinking proof.
        if self.transport.execution_kind != 'SIMULATED':
            self.transport.validate_capacity_profile(self.estimate)
        elif self.estimate.capacity_profile is not None:
            self.estimate.validate_profile(self.transport.grant)

    def claim_dispatch(self, request, timeout, reservation):
        if self.purpose != 'calibration' and self.transport.send_claim_callback is None:
            return
        estimate, scope, purpose, request_id, _ = self.transport._context.get()
        permit = self.transport.calibration_permit
        if self.purpose == 'calibration':
            permit.validate(self.transport.grant, estimate, request_id, timeout)
        identity = dict(grant_id=self.transport.grant.grant_id, request_id=request_id, purpose=purpose,
            execution_kind=self.transport.execution_kind,
            provider='siliconflow', model=estimate.model, scope_sha256=estimate.scope_sha256,
            wire_request_sha256=estimate.wire_request_sha256, measurement_sha256=estimate.measurement_sha256,
            local_input_bound=estimate.reservation_bound, output_bound=estimate.output_reservation,
            timeout_seconds=timeout, deadline_utc=self.transport.grant.deadline_utc)
        if estimate.capacity_profile is not None:identity['capacity_profile_sha256']=estimate.capacity_profile.manifest_sha256
        if self.purpose == 'calibration':
            identity['price_source_sha256'] = permit.price_contract.source_sha256
        self.dispatch_contract, self.reservation_id = identity, str(reservation)
        try:
            if self.purpose == 'calibration':
                self.ticket = permit.claim_store.claim_dispatch(identity, reservation,
                    now_utc=self.transport.clock().isoformat())
                self.transport._calibration_claim.set(self.ticket)
            else:
                digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
                claim = self.transport.send_claim_callback(request_id=request_id, purpose=purpose,
                    provider='siliconflow', model=estimate.model, role='chat_cheap',
                    request_identity_sha256=digest, call_ordinal=1)
                if not claim_matches(claim, request_id=request_id, purpose=purpose,
                        provider='siliconflow', model=estimate.model, role='chat_cheap', identity_sha256=digest):
                    raise ProviderRequestNotSent('XING_DURABLE_CLAIM_INVALID')
                self.provider_claim = claim
                self.transport._provider_claim.set(claim)
        except Exception:
            raise ProviderRequestNotSent('XING_DURABLE_CLAIM_DENIED') from None

    def finish_dispatch(self, receipt):
        if self.ticket is not None:
            self.transport.calibration_permit.claim_store.finish_dispatch(self.ticket, receipt)
        elif self.provider_claim is not None:
            state = ('blocked' if receipt['status'] == 'not_sent' else
                     'simulated' if receipt['status'] == 'ok' and receipt['execution_kind'] == 'SIMULATED' else
                     'succeeded' if receipt['status'] == 'ok' else 'unknown')
            self.transport.send_claim_callback.settle(request_id=self.provider_claim.request_id,
                status=state, result={'provider_dispatch': dict(receipt),
                    'dispatch_contract': self.dispatch_contract, 'budget_reservation_id': self.reservation_id})

    def validate_request(self, request, timeout):
        self.estimate.assert_wire_bytes(request.data)
        self.transport.validate_request(request, timeout)

    def zero_price_authority(self, request, timeout):
        from backend.app.ports.xing_zero_reservation import XingCalibrationZeroReservation
        if self.transport.free_model_permit is not None:
            return SiliconFlowZeroReservation(self.transport.free_model_permit,self.transport,self,
                request.data,self.input_tokens+self.output_tokens)
        if self.purpose != 'calibration':
            raise ProviderRequestNotSent('XING_ZERO_CALIBRATION_ONLY')
        return XingCalibrationZeroReservation(self.transport.calibration_permit,
            self.transport, self.estimate, request.data, timeout)

    def validate_response(self, response, usage):
        if response.get('model') != self.estimate.model:
            raise ProviderUnavailable('XING_RESPONSE_MODEL_UNVERIFIED')
        if (not isinstance(usage, dict) or type(usage.get('prompt_tokens')) is not int
                or type(usage.get('completion_tokens')) is not int
                or type(usage.get('total_tokens')) is not int
                or usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens']):
            raise ProviderUnavailable('XING_RESPONSE_USAGE_UNVERIFIED')
        if self.purpose == 'calibration':
            # This single sample observes hosted divergence/thinking; it does
            # not assert a hosted bound or promote production_ready.
            return
        if (usage['prompt_tokens'] > self.estimate.reservation_bound
                or usage['completion_tokens'] > self.estimate.output_reservation):
            self.transport.blocked = True
            raise ProviderUnavailable('XING_RESPONSE_LOCAL_BOUND_EXCEEDED')
        choices = response.get('choices', [])
        message = choices[0].get('message', {}) if choices and isinstance(choices[0], dict) else {}
        raw_usage = response.get('usage', {})
        details = raw_usage.get('completion_tokens_details', {}) if isinstance(raw_usage, dict) else {}
        reasoning = details.get('reasoning_tokens') if isinstance(details, dict) else None
        if message.get('reasoning_content') or (reasoning is not None and (type(reasoning) is not int or reasoning != 0)):
            raise ProviderUnavailable('XING_THINKING_DISABLE_UNVERIFIED')

    def receipt_fields(self):
        _, _, _, request_id, _ = self.transport._context.get()
        observed = self.transport.attempts.get(request_id, {})
        fields = dict(generation_purpose=self.purpose, usage_stage=self.usage_stage,
            execution_kind=self.transport.execution_kind,
            send_status=observed.get('send_status', 'NOT_SENT'),
            response_actual_model=observed.get('response_actual_model', 'UNKNOWN'),
            wire_request_sha256=self.estimate.wire_request_sha256,
            scope_sha256=self.estimate.scope_sha256, measurement_sha256=self.estimate.measurement_sha256,
            local_input_token_bound=self.estimate.reservation_bound,
            local_output_token_bound=self.estimate.output_reservation,
            hosted_revision='UNKNOWN', hosted_input_token_bound=None,
            hosted_total_token_bound=None, production_ready=False)
        if self.estimate.capacity_profile is not None:
            fields.update(capacity_profile_sha256=self.estimate.capacity_profile.manifest_sha256,
                production_ready=self.estimate.production_ready,capacity_readiness_basis='OWNER_SCOPED_CONDITIONAL')
        if self.transport.free_model_permit is not None:
            fields.update(price_source_sha256=self.transport.free_model_permit.price_contract.source_sha256,
                quoted_currency='CNY',quoted_token_cost_micro_cny=0,actual_charge_verified=False,
                actual_charge='UNKNOWN',request_window_blocked=self.transport.blocked,
                frozen_plan_sha256=self.transport.free_model_permit.frozen_plan_sha256)
        if self.purpose == 'calibration':
            _, _, _, request_id, _ = self.transport._context.get()
            observed = self.transport.attempts.get(request_id, {})
            quote = self.transport.calibration_permit.price_contract
            fields.update(execution_kind=self.transport.execution_kind,
                price_source_sha256=quote.source_sha256, price_source_url=quote.source_url,
                quoted_currency='CNY', quoted_prompt_per_million=0, quoted_completion_per_million=0,
                quoted_token_cost_micro_cny=0, actual_charge_verified=False, actual_charge='UNKNOWN',
                send_status=observed.get('send_status', 'NOT_SENT'),
                response_actual_model=observed.get('response_actual_model', 'UNKNOWN'),
                reasoning_present=observed.get('reasoning_present', 'UNKNOWN'),
                reasoning_tokens=observed.get('reasoning_tokens', 'UNKNOWN'))
            usage = observed.get('usage_actual')
            fields.update(observed_input_over_local_bound=(usage['prompt_tokens'] > self.estimate.reservation_bound)
                if isinstance(usage, dict) and type(usage.get('prompt_tokens')) is int else 'UNKNOWN',
                observed_output_over_local_reservation=(usage['completion_tokens'] > self.estimate.output_reservation)
                if isinstance(usage, dict) and type(usage.get('completion_tokens')) is int else 'UNKNOWN')
        return fields


@dataclass(frozen=True)
class V6XingPlannedCall:
    request_id: str
    qid: str
    arm: str
    purpose: str
    input_token_bound: int
    output_token_bound: int


@dataclass(frozen=True)
class V6XingRequestWindow:
    """Explicit finite cohort plan; never grants hosted or monthly admission.

    Each QID has at most two cheap answers, plus explicitly listed helpers.
    Calibration is a separate one-send authority and cannot enter this window.
    """
    run_id: str
    grant_id: str
    scope: Scope
    deadline_utc: str
    cohort_sha256: str
    source_manifest_sha256: str
    calls: tuple[V6XingPlannedCall, ...]
    frozen_plan_sha256: str
    provider: str = 'siliconflow'
    model: str = 'XingChenAGI/Xing4.0-29B'
    role: str = 'chat_cheap'

    @property
    def fingerprint(self):
        payload = dict(run_id=self.run_id, grant_id=self.grant_id,
            scope=dict(kb=sorted(self.scope.knowledge_base_ids), docs=sorted(self.scope.document_ids)),
            deadline_utc=self.deadline_utc, cohort_sha256=self.cohort_sha256,
            source_manifest_sha256=self.source_manifest_sha256, provider=self.provider,
            model=self.model, role=self.role,
            calls=[dict(request_id=c.request_id, qid=c.qid, arm=c.arm, purpose=c.purpose,
                input_token_bound=c.input_token_bound, output_token_bound=c.output_token_bound) for c in self.calls])
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode('utf-8')).hexdigest()

    @property
    def planned_token_cap(self):
        return sum(c.input_token_bound + c.output_token_bound for c in self.calls)

    def validate(self, spec, transport):
        try:
            valid = (type(self) is V6XingRequestWindow and type(self.scope) is Scope
                and type(self.calls) is tuple and 0 < len(self.calls) <= 264
                and all(type(c) is V6XingPlannedCall for c in self.calls)
                and all(type(v) is str and v for v in (self.run_id, self.grant_id))
                and all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
                    for v in (self.cohort_sha256, self.source_manifest_sha256, self.frozen_plan_sha256))
                and self.frozen_plan_sha256 == self.fingerprint
                and (self.provider, self.model, self.role) ==
                    ('siliconflow', 'XingChenAGI/Xing4.0-29B', 'chat_cheap')
                and (spec.provider, spec.model_id, spec.role) == (self.provider, self.model, self.role)
                and type(transport) is XingAuthorizedTransport and transport.calibration_permit is None
                and isinstance(transport.send_claim_callback, ProviderSendClaims)
                and transport.grant.grant_id == self.grant_id and transport.grant.scope == self.scope
                and type(transport.grant.scope) is Scope and transport.grant.deadline_utc == self.deadline_utc
                and self.deadline_utc == XING_CALIBRATION_DEADLINE
                and len({c.qid for c in self.calls}) <= 24
                and len({c.request_id for c in self.calls}) == len(self.calls)
                and len({(c.qid, c.arm, c.purpose) for c in self.calls}) == len(self.calls)
                and all(type(c.qid) is str and c.qid and type(c.request_id) is str and c.request_id
                    and c.arm in ('CHEAP_ONLY', 'DYNAMIC', 'EXPENSIVE_ONLY')
                    and c.purpose in ('answer', 'understand', 'rewrite', 'expansion')
                    and c.purpose in transport.grant.allowed_purposes
                    and type(c.input_token_bound) is int and c.input_token_bound > 0
                    and type(c.output_token_bound) is int and 1 <= c.output_token_bound <= 512
                    and c.input_token_bound + c.output_token_bound <= 262144
                    and not (c.purpose == 'answer' and c.arm == 'EXPENSIVE_ONLY') for c in self.calls))
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('V6_XING_REQUEST_WINDOW_INVALID')

    def planned_call(self, spec, policy, timeout):
        if type(policy) is not _XingRequestPolicy:
            raise ProviderRequestNotSent('V6_XING_WINDOW_SCOPED_REQUEST_REQUIRED')
        self.validate(spec, policy.transport)
        context = policy.transport._context.get()
        if context is None:
            raise ProviderRequestNotSent('V6_XING_WINDOW_SCOPED_REQUEST_REQUIRED')
        _, scope, purpose, request_id, _ = context
        policy.transport.check_scope(scope=scope, purpose=purpose, timeout_seconds=timeout)
        call = next((c for c in self.calls if c.request_id == request_id and c.purpose == purpose), None)
        if scope != self.scope or call is None:
            raise ProviderRequestNotSent('V6_XING_WINDOW_CALL_NOT_PLANNED')
        if (policy.estimate.reservation_bound > call.input_token_bound
                or policy.estimate.output_reservation > call.output_token_bound):
            raise ProviderRequestNotSent('V6_XING_WINDOW_TOKEN_BOUND_EXCEEDED')
        return call

    def validate_claim(self, call, claim):
        if (claim is None or (claim.run_id, claim.qid, claim.arm, claim.request_id, claim.purpose)
                != (self.run_id, call.qid, call.arm, call.request_id, call.purpose)):
            raise ProviderRequestNotSent('V6_XING_WINDOW_NATIVE_CLAIM_MISMATCH')


class SiliconFlowAuthorizedTransport:
    """Explicit finite Embedding/Rerank plan over the existing normal sender.

    The durable ProviderSendClaims callback owns once-only identity across
    adapter rebuilds. Ordinary startup never constructs this authorization.
    """
    def __init__(self,permit,*,send_claim_callback,sender=None,clock=None):
        from backend.app.domain.model_registry import ModelRegistry
        if type(permit) is not SiliconFlowFreePermit or permit.role not in ('embedding','rerank'):
            raise ValueError('SF_FREE_PERMIT_INVALID')
        permit.validate(ModelRegistry.frozen_defaults().select(permit.role))
        if (not isinstance(send_claim_callback,ProviderSendClaims)
                or (permit.execution_kind == 'SIMULATED') != (type(sender) is SimulatedXingSend)
                or sender is not None and type(sender) is not SimulatedXingSend):
            raise ValueError('SF_FREE_TRANSPORT_INVALID')
        self.permit,self.send_claim_callback,self.sender = permit,send_claim_callback,sender
        self.execution_kind = permit.execution_kind
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self._context = ContextVar('sf_free_authorized_request',default=None)
        self._lock = threading.Lock()
        self.attempts = {}
        self.blocked = False

    def check_scope(self,*,scope,purpose,timeout_seconds):
        from backend.app.ports.model_access import request_scope
        now = self.clock()
        try:
            deadline = self.permit.owner_deadline()
        except (AttributeError,TypeError,ValueError):
            raise ProviderRequestNotSent('SF_FREE_SCOPE_DEADLINE_OR_WINDOW_DENIED') from None
        if (self.blocked or type(scope) is not Scope or scope != self.permit.scope
                or request_scope(self.permit.role) != scope
                or purpose not in {c.purpose for c in self.permit.calls}
                or not isinstance(now,datetime) or now.tzinfo is None
                or type(timeout_seconds) is not int or not 0 < timeout_seconds <= 120
                or self.permit.execution_lease is not None and now<self.permit.execution_lease.not_before
                or deadline is not None and now.timestamp()+timeout_seconds > deadline.timestamp()):
            raise ProviderRequestNotSent('SF_FREE_SCOPE_DEADLINE_OR_WINDOW_DENIED')

    @contextmanager
    def authorize(self,policy):
        if type(policy) is not _FreeModelRequestPolicy or policy.transport is not self:
            raise ProviderRequestNotSent('SF_FREE_REQUEST_POLICY_REQUIRED')
        self.check_scope(scope=policy.scope,purpose=policy.purpose,timeout_seconds=policy.timeout)
        token = self._context.set(policy)
        try: yield
        finally: self._context.reset(token)

    def validate_request(self,request,timeout):
        policy = self._context.get()
        if type(policy) is not _FreeModelRequestPolicy:
            raise ProviderRequestNotSent('SF_FREE_AUTHORIZED_CONTEXT_REQUIRED')
        policy.validate_active_context()
        path = '/embeddings' if self.permit.role == 'embedding' else '/rerank'
        if (request.full_url != 'https://api.siliconflow.cn/v1'+path
                or request.get_method() != 'POST' or timeout != policy.timeout
                or request.data != policy.wire_bytes):
            raise ProviderRequestNotSent('SF_FREE_FINAL_WIRE_MISMATCH')
        with self._lock:
            if policy.request_id in self.attempts:
                raise ProviderRequestNotSent('SF_FREE_REQUEST_ALREADY_CONSUMED')

    def __call__(self,request,timeout):
        self.validate_request(request,timeout)
        policy = self._context.get()
        if policy.provider_claim is None:
            raise ProviderRequestNotSent('SF_FREE_DURABLE_CLAIM_REQUIRED')
        with self._lock:
            if policy.request_id in self.attempts:
                raise ProviderRequestNotSent('SF_FREE_REQUEST_ALREADY_CONSUMED')
            self.attempts[policy.request_id] = dict(send_status='UNKNOWN')
        try:
            self.send_claim_callback.check_before_send(request_id=policy.request_id)
            policy.validate_active_context()
            self.check_scope(scope=policy.scope,purpose=policy.purpose,timeout_seconds=timeout)
        except Exception:
            self.attempts[policy.request_id]['send_status']='NOT_SENT'
            raise ProviderRequestNotSent('SF_FREE_DURABLE_SEND_CHECK_DENIED') from None
        try:
            response = self.sender.send(request,timeout) if self.execution_kind == 'SIMULATED' else _send(request,timeout)
            self.attempts[policy.request_id]['send_status']='RESPONSE_RECEIVED'
            return response
        except ProviderRequestNotSent:
            self.attempts[policy.request_id]['send_status']='NOT_SENT'
            raise


class _FreeModelRequestPolicy:
    def __init__(self,binding,context,request,*,input_tokens,input_profile_sha256,item_count,timeout,
                 profile_owner,lease_check=None,document_version_ids=(),chunk_ids=(),candidate_order=()):
        if (type(binding) is not SiliconFlowFreeBinding or type(context) is not FreeModelRequestContext
                or context.run_id != binding.permit.run_id or context.scope != binding.permit.scope
                or type(input_tokens) is not int or input_tokens <= 0):
            raise ProviderRequestNotSent('SF_FREE_REQUEST_CONTEXT_INVALID')
        self.binding,self.context,self.transport = binding,context,binding.authorized_transport
        self.scope,self.purpose,self.request_id = context.scope,context.purpose,context.request_id
        self.input_tokens,self.output_tokens = input_tokens,0
        self.wire_bytes = request.data
        self.input_profile_sha256,self.item_count,self.timeout = input_profile_sha256,item_count,timeout
        self.profile_owner = profile_owner
        self.actual_document_version_ids=tuple(document_version_ids)
        self.actual_chunk_ids=tuple(chunk_ids)
        self.actual_candidate_order=tuple(candidate_order)
        self.provider_claim = None
        self.reservation_id = None
        self.result_summary = None
        self.usage_stage = None
        if context.purpose == 'index_embedding' and not callable(lease_check):
            raise ProviderRequestNotSent('SF_FREE_INDEX_FENCE_REQUIRED')
        self.lease_check = lease_check
        self.call = next((c for c in binding.permit.calls if c.request_id == context.request_id),None)
        if self.call is None or self.call.batch_id != context.batch_id:
            raise ProviderRequestNotSent('SF_FREE_BATCH_NOT_PLANNED')

    def validate_active_context(self):
        if self.transport._context.get() is not self:
            raise ValueError('SF_ZERO_CONTEXT_REQUIRED')
        self.transport.check_scope(scope=self.scope,purpose=self.purpose,timeout_seconds=self.timeout)
        payload = json.loads(self.wire_bytes)
        if self.binding.permit.role == 'embedding':
            if type(self.profile_owner) is not SiliconFlowEmbedding or self.profile_owner.admission is not self.binding.admission:
                raise ValueError('SF_FREE_MEASUREMENT_SOURCE_INVALID')
            inputs = payload.get('input')
            measured = self.binding.admission.count(inputs)
            items = len(inputs)
        else:
            if type(self.profile_owner) is not RerankAdmission or self.profile_owner is not self.binding.admission:
                raise ValueError('SF_FREE_MEASUREMENT_SOURCE_INVALID')
            measured = self.binding.admission.count(payload.get('query'),payload.get('documents'))
            items = len(payload['documents'])
        if (payload.get('model') != self.binding.permit.model or measured != self.input_tokens
                or items != self.item_count or self.profile_owner.input_profile_sha256 != self.input_profile_sha256):
            raise ValueError('SF_FREE_ACTUAL_WIRE_MEASUREMENT_MISMATCH')
        plan=self.context.batch_plan
        if plan is None and self.transport.execution_kind=='REAL':
            raise ProviderRequestNotSent('SF_FREE_NATIVE_BATCH_PLAN_REQUIRED')
        if plan is not None:
            plan.validate()
            native=plan.calls[self.call.call_ordinal-1]
            texts=payload['input'] if self.binding.permit.role=='embedding' else payload['documents']
            hashes=tuple(hashlib.sha256(t.encode()).hexdigest() for t in texts)
            expected_scope=free_dispatch_contract(self.binding.permit,self.call)['scope_sha256']
            if (plan.purpose!=self.purpose or plan.provider!='siliconflow' or plan.model!=self.binding.permit.model
                    or plan.scope_sha256!=expected_scope or native.batch_ordinal!=self.call.call_ordinal
                    or native.wire_request_sha256!=hashlib.sha256(self.wire_bytes).hexdigest()
                    or native.request_identity_sha256!=free_dispatch_identity_sha256(self.binding.permit,self.call)
                    or native.input_sha256!=hashes or native.input_sha256!=self.call.input_sha256
                    or native.document_version_ids!=self.call.document_version_ids or native.chunk_ids!=self.call.chunk_ids
                    or native.candidate_order!=self.call.candidate_order
                    or native.document_version_ids!=self.actual_document_version_ids
                    or native.chunk_ids!=self.actual_chunk_ids or native.candidate_order!=self.actual_candidate_order
                    or self.purpose!='index_embedding' and (plan.query_sha256!=self.call.query_sha256
                        or plan.query_sha256!=hashlib.sha256((payload['input'][0] if self.purpose=='query_embedding' else payload['query']).encode()).hexdigest())):
                raise ProviderRequestNotSent('SF_FREE_NATIVE_BATCH_PLAN_MISMATCH')
            if self.binding.permit.role=='embedding' and (plan.embedding_identity_sha256!=self.profile_owner.identity.fingerprint
                    or plan.index_profile_sha256!=hashlib.sha256(self.profile_owner.identity.chunking_index_identity.encode()).hexdigest()):
                raise ProviderRequestNotSent('SF_FREE_NATIVE_EMBEDDING_PROFILE_MISMATCH')
        from backend.app.ports.run_lifecycle import current_execution
        execution = current_execution.get()
        if execution is not None and execution[2].is_set():
            raise ProviderRequestNotSent('RUN_LEASE_LOST')
        if self.lease_check is not None and self.lease_check() is not True:
            raise ProviderRequestNotSent('INGESTION_LEASE_LOST')

    def validate_request(self,request,timeout):
        self.transport.validate_request(request,timeout)

    def zero_price_authority(self,request,timeout):
        return SiliconFlowZeroReservation(self.binding.permit,self.transport,self,request.data,self.input_tokens)

    def claim_dispatch(self,request,timeout,reservation):
        from dataclasses import asdict
        spec = self.binding.permit
        identity = free_dispatch_contract(spec,self.call)
        self.dispatch_contract = identity
        self.reservation_id = str(reservation)
        self.identity_sha256 = hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        try:
            claim = self.transport.send_claim_callback(request_id=self.request_id,purpose=self.purpose,
                provider='siliconflow',model=spec.model,role=spec.role,
                request_identity_sha256=self.identity_sha256,call_ordinal=self.call.call_ordinal)
            if not claim_matches(claim,request_id=self.request_id,purpose=self.purpose,
                    provider='siliconflow',model=spec.model,role=spec.role,identity_sha256=self.identity_sha256,
                    call_ordinal=self.call.call_ordinal):
                raise ValueError()
            spec.validate_claim(self.call,claim)
            self.provider_claim = claim
        except Exception:
            raise ProviderRequestNotSent('SF_FREE_DURABLE_CLAIM_DENIED') from None

    def validate_response(self,response,usage):
        if response.get('model') != self.binding.permit.model:
            raise ProviderUnavailable('SF_FREE_RESPONSE_MODEL_UNVERIFIED')
        if (not isinstance(usage,dict) or type(usage.get('prompt_tokens')) is not int
                or type(usage.get('total_tokens')) is not int
                or usage.get('completion_tokens',0) not in (0,None)
                or usage['total_tokens'] != usage['prompt_tokens']):
            raise ProviderUnavailable('SF_FREE_USAGE_UNVERIFIED')
        raw = response.get('usage')
        if isinstance(raw,dict) and 'completion_tokens' in raw and (
                type(raw['completion_tokens']) is not int or raw['completion_tokens'] != 0):
            raise ProviderUnavailable('SF_FREE_USAGE_UNVERIFIED')
        if usage['prompt_tokens'] > self.input_tokens:
            self.transport.blocked = True
            raise _DiagnosticFailure('SF_FREE_TOKEN_BOUND_EXCEEDED','usage_parse','SF_FREE_TOKEN_BOUND_EXCEEDED')

    def receipt_fields(self):
        permit = self.binding.permit
        return dict(generation_purpose=self.purpose,execution_kind=permit.execution_kind,
            request_id=self.request_id,run_id=permit.run_id,batch_id=self.context.batch_id,
            budget_reservation_id=self.reservation_id,result_summary=self.result_summary,
            wire_request_sha256=hashlib.sha256(self.wire_bytes).hexdigest(),input_profile_sha256=self.input_profile_sha256,
            frozen_plan_sha256=permit.frozen_plan_sha256,grant_id=permit.grant_id,
            price_source_sha256=permit.price_contract.source_sha256,quoted_currency='CNY',quoted_token_cost_micro_cny=0,
            actual_charge_verified=False,actual_charge='UNKNOWN',local_input_token_bound=self.input_tokens,
            send_status=self.transport.attempts.get(self.request_id,{}).get('send_status','NOT_SENT'),
            request_window_blocked=self.transport.blocked,hosted_revision='UNKNOWN')

    def capture_result(self,value,usage):
        from io import BytesIO
        from backend.app.adapters.storage import ContentAddressedStorage
        storage=self.binding.result_storage
        if type(storage) is not ContentAddressedStorage:
            raise ProviderUnavailable('SF_FREE_RESULT_STORAGE_REQUIRED')
        payload = value if self.binding.permit.role=='embedding' else dict(
            ranked_chunk_ids=[h.chunk_id for h in value[0]],scores=list(value[1]))
        permit=self.binding.permit
        result=dict(schema_version=1,run_id=permit.run_id,request_id=self.request_id,call_ordinal=self.call.call_ordinal,
            purpose=self.purpose,model=permit.model,wire_sha256=hashlib.sha256(self.wire_bytes).hexdigest(),
            input_profile_sha256=self.input_profile_sha256,source_manifest_sha256=permit.source_manifest_sha256,
            authority_manifest_sha256=permit.authority_manifest_sha256,budget_reservation_id=self.reservation_id,
            dispatch_identity_sha256=self.identity_sha256,usage_actual=usage,value=payload)
        raw=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        stored=storage.put_stream(BytesIO(raw))
        if hashlib.sha256(storage.read(stored.storage_key)).hexdigest()!=stored.sha256:
            raise ProviderUnavailable('SF_FREE_RESULT_ARTIFACT_UNVERIFIED')
        self.result_summary=dict(result_sha256=stored.sha256,result_ref='sha256:'+stored.sha256,item_count=self.item_count)

    def finish_dispatch(self,receipt):
        if self.provider_claim is None: return
        state = ('blocked' if receipt['status']=='not_sent' else 'simulated'
            if receipt['status']=='ok' and self.transport.execution_kind=='SIMULATED'
            else 'succeeded' if receipt['status']=='ok' else 'unknown')
        self.transport.send_claim_callback.settle(request_id=self.request_id,status=state,
            result=self.result_summary,error_code=receipt['error_code'] if receipt['error_code']!='NONE' else None)


class SimulatedEmbeddingCounter:
    """Explicit fixture; only usable by a SIMULATED free binding."""
    def __init__(self,count): self.count = count
    def __call__(self,text): return self.count(text)


class SimulatedRerankCounter:
    """Explicit pair-count fixture; never a REAL tokenizer proof."""
    def __init__(self,count): self.count = count
    def __call__(self,query,document): return self.count(query,document)


class RerankAdmission:
    def __init__(self,counter=None,*,tokenizer_identity='UNKNOWN',execution_kind='REAL'):
        self.counter,self.tokenizer_identity,self.execution_kind = counter,tokenizer_identity,execution_kind

    @property
    def input_profile_sha256(self):
        return hashlib.sha256(json.dumps(dict(model='BAAI/bge-reranker-v2-m3',
            tokenizer=self.tokenizer_identity,recipe='query-document-pair-special-tokens-no-truncation/v1',
            max_pair_tokens=7680,max_items=32),sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_frozen_file(cls,path,*,revision,sha256,source_manifest_sha256):
        from importlib.metadata import version
        from tokenizers import Tokenizer
        if (not re.fullmatch('[0-9a-f]{40}',revision) or not re.fullmatch('[0-9a-f]{64}',source_manifest_sha256)
                or hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha256
                or version('tokenizers')!='0.23.2'):
            raise ValueError('RERANK_TOKENIZER_SOURCE_UNVERIFIED')
        tokenizer = Tokenizer.from_file(str(path))
        tokenizer.no_truncation(); tokenizer.no_padding()
        result = cls(lambda q,d:len(tokenizer.encode(q,d,add_special_tokens=True).ids),
            tokenizer_identity=f'BAAI/bge-reranker-v2-m3@{revision}:sha256:{sha256}:tokenizers/0.23.2')
        result._frozen_source_manifest_sha256 = source_manifest_sha256
        result._frozen_tokenizer = tokenizer
        return result

    def count(self,question,documents):
        if (not isinstance(question,str) or not question.strip() or not documents or len(documents)>32
                or any(not isinstance(t,str) or not t.strip() for t in documents)):
            raise ProviderRequestNotSent('RERANK_INPUT_INVALID')
        if (self.counter is None or self.tokenizer_identity=='UNKNOWN'
                or self.execution_kind=='REAL' and not hasattr(self,'_frozen_tokenizer')
                or self.execution_kind=='SIMULATED' and type(self.counter) is not SimulatedRerankCounter):
            raise ProviderRequestNotSent('RERANK_TOKENIZER_UNVERIFIED')
        counts = ([len(self._frozen_tokenizer.encode(question,d,add_special_tokens=True).ids) for d in documents]
                  if self.execution_kind=='REAL' else [self.counter(question,d) for d in documents])
        if any(type(c) is not int or not 0 < c <= 7680 for c in counts):
            raise ProviderRequestNotSent('RERANK_PAIR_TOKEN_LIMIT_EXCEEDED')
        return sum(counts)


class CloudAdapter:
    provider_kind = "cloud"

    def __init__(self, spec: ModelSpec, *, enabled=False, usage_guard=None, transport=None,
                 max_requests=10, max_planned_tokens=20000, request_window=None):
        if type(max_requests) is not int or not 1 <= max_requests <= 10:
            raise ValueError("PROVIDER_REQUEST_CAP_INVALID")
        if type(max_planned_tokens) is not int or not 1 <= max_planned_tokens <= 20000:
            raise ValueError("PROVIDER_TOKEN_CAP_INVALID")
        self.spec, self.enabled, self.usage_guard = spec, enabled, usage_guard
        self.provider_name = spec.provider
        self.transport = transport or _send
        self.max_requests, self.max_planned_tokens = max_requests, max_planned_tokens
        if request_window is not None:
            if type(request_window) not in (V6XingRequestWindow,SiliconFlowFreePermit):
                raise ValueError('V6_XING_REQUEST_WINDOW_INVALID')
            request_window.validate(spec, self.transport)
        self.request_window = request_window
        self._window_consumed = set()
        self.requests = self.planned_tokens = 0
        self.receipts = []
        self._lock = threading.Lock()

    def preflight(self, timeout_seconds):
        if not self.enabled or not self.spec.enabled or not scope_allows(self.spec.role):
            raise ProviderRequestNotSent("MODEL_ROLE_EGRESS_DENIED")
        if self.usage_guard is None:
            raise ProviderRequestNotSent("MODEL_USAGE_GUARD_REQUIRED")
        if not os.environ.get(self.spec.api_key_env, "").strip():
            raise ProviderRequestNotSent("MODEL_CREDENTIAL_MISSING")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ProviderRequestNotSent("MODEL_TIMEOUT_INVALID")

    def _prepare_request(self,path,payload):
        # ModelSpec validates exact base URL, provider, model and key-env pair.
        return Request(self.spec.base_url + path, method="POST", headers={
            "Authorization": "Bearer " + os.environ[self.spec.api_key_env],
            "Content-Type": "application/json"}, data=json.dumps(payload).encode())

    def _request(self, path, payload, timeout, validate, planned_tokens=None, *, usage_parser=_usage,
                 response_diagnostics=False, expected_results=None, request_policy=None,prepared_request=None):
        self.preflight(timeout)
        request = prepared_request or self._prepare_request(path,payload)
        if (request.full_url != self.spec.base_url+path or request.get_method() != 'POST'
                or request.data != json.dumps(payload).encode()):
            raise ProviderRequestNotSent('MODEL_FINAL_WIRE_MISMATCH')
        if len(request.data) > 2_000_000:
            raise ProviderRequestNotSent("MODEL_REQUEST_TOO_LARGE")
        if request_policy is not None:
            request_policy.validate_request(request, timeout)
        planned_call = None
        with self._lock:
            limit = self.max_requests
            token_limit = self.max_planned_tokens
            if self.request_window is not None:
                planned_call = self.request_window.planned_call(self.spec, request_policy, timeout)
                if planned_call.request_id in self._window_consumed:
                    raise ProviderRequestNotSent('V6_XING_WINDOW_ALREADY_CONSUMED')
                limit = len(self.request_window.calls)
                token_limit = self.request_window.planned_token_cap
            if self.requests >= limit or (planned_tokens is not None
                    and self.planned_tokens + planned_tokens > token_limit):
                raise ProviderRequestNotSent("MODEL_REQUEST_BUDGET_EXCEEDED")
            try:
                reserve_kwargs = {}
                from backend.app.application.provider_usage import BudgetUsageGuard
                if type(self.usage_guard) is BudgetUsageGuard and self.usage_guard.estimate_microunits == 0:
                    if type(request_policy) not in (_XingRequestPolicy,_FreeModelRequestPolicy):
                        raise ProviderRequestNotSent('XING_ZERO_CALIBRATION_ONLY')
                    reserve_kwargs['zero_price_authority'] = request_policy.zero_price_authority(request, timeout)
                reservation = self.usage_guard.reserve(model_key=self.spec.model_key,
                    role=self.spec.role, model_id=self.spec.model_id, planned_tokens=planned_tokens, **reserve_kwargs)
            except Exception:
                raise ProviderRequestNotSent("MODEL_BUDGET_DENIED") from None
            self.requests += 1
            self.planned_tokens += planned_tokens or 0
            if planned_call is not None:
                self._window_consumed.add(planned_call.request_id)
        started = time.perf_counter()
        response, status, observed_usage = None, "error", None
        safe_response_diagnostics = None
        stage, error_code, sent = 'transport', 'NONE', True
        try:
            if request_policy is not None:
                request_policy.validate_request(request, timeout)
                request_policy.claim_dispatch(request, timeout, reservation)
                if planned_call is not None:
                    self.request_window.validate_claim(planned_call, request_policy.provider_claim)
                    request_policy.validate_request(request, timeout)
            response = self.transport(request, timeout)
            if isinstance(response, _ReceivedResponse):
                safe_response_diagnostics = _response_diagnostics(response.payload, received=True,
                    http_status_code=response.http_status_code, response_bytes=response.response_bytes,
                    content_type=response.content_type,
                    expected_results=expected_results) if response_diagnostics else None
                response = response.payload
            elif response_diagnostics:
                safe_response_diagnostics = _response_diagnostics(response, received=True,
                    expected_results=expected_results)
            stage = 'response_validation'
            if not isinstance(response, dict):
                raise _DiagnosticFailure('MODEL_RESPONSE_INVALID', stage, 'RESPONSE_STRUCTURE_INVALID',
                    safe_response_diagnostics)
            reported = response.get("model")
            if reported is not None and reported != self.spec.model_id:
                raise _DiagnosticFailure('MODEL_RESPONSE_IDENTITY_MISMATCH', stage, 'MODEL_IDENTITY_MISMATCH',
                    safe_response_diagnostics)
            value = validate(response)
            stage = 'usage_parse'
            observed_usage = usage_parser(response)
            if request_policy is not None:
                request_policy.validate_response(response, observed_usage)
                if type(request_policy) is _FreeModelRequestPolicy:
                    request_policy.capture_result(value,observed_usage)
            status = "ok"
            stage = 'complete'
            return value, observed_usage, (time.perf_counter()-started)*1000
        except HTTPError as exc:
            number = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None
            stage = 'http_response'
            if response_diagnostics:
                safe_response_diagnostics = _response_diagnostics(http_status_code=number,
                    content_type=exc.headers.get('Content-Type') if exc.headers is not None else None,
                    expected_results=expected_results)
            error_code = ('HTTP_5XX' if number is not None and number >= 500 else
                          'HTTP_' + str(number) if number in {401, 403, 404, 429} else 'HTTP_OTHER')
            status = 'http_' + str(number) if number is not None else 'error'
            raise ProviderUnavailable('MODEL_HTTP_' + str(number) if number is not None
                                      else 'MODEL_REQUEST_FAILED') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            stage, error_code = 'response_decode', 'JSON_RESPONSE_INVALID'
            if response_diagnostics:
                safe_response_diagnostics = _response_diagnostics(expected_results=expected_results)
            raise ProviderUnavailable('MODEL_REQUEST_FAILED') from None
        except _DiagnosticFailure as exc:
            stage, error_code = exc.stage, exc.code
            if response_diagnostics:
                safe_response_diagnostics = _merge_response_diagnostics(
                    safe_response_diagnostics, exc.response_diagnostics, expected_results)
            raise ProviderUnavailable(exc.public_code) from None
        except ProviderRequestNotSent:
            # A trusted transport may reject its local allowlist before I/O.
            # Release only explicitly proven not-sent reservations.
            stage, error_code, status, sent = 'admission', 'REQUEST_NOT_SENT', 'not_sent', False
            raise
        except TruncatedAnswer:
            status = "truncated"
            error_code = 'RESPONSE_TRUNCATED'
            raise
        except ProviderUnavailable:
            error_code = 'PROVIDER_UNAVAILABLE' if stage == 'transport' else 'RESPONSE_VALIDATION_FAILED'
            raise
        except Exception as exc:
            error_code = (_transport_error_code(exc) if stage == 'transport' else
                          'USAGE_PARSE_FAILED' if stage == 'usage_parse' else 'RESPONSE_VALIDATION_FAILED')
            raise ProviderUnavailable("MODEL_REQUEST_FAILED") from None
        finally:
            receipt = dict(model_key=self.spec.model_key, role=self.spec.role,
                provider=self.spec.provider, model_id=self.spec.model_id, status=status,
                latency_ms=(time.perf_counter()-started)*1000, planned_tokens=planned_tokens,
                usage_actual=observed_usage, settlement='UNKNOWN',
                diagnostic_schema='provider-diagnostic/v1', diagnostic_stage=stage, error_code=error_code)
            if response_diagnostics:
                receipt['response_diagnostics'] = (safe_response_diagnostics or
                    _response_diagnostics(expected_results=expected_results))
            if request_policy is not None:
                receipt.update(request_policy.receipt_fields())
            settlement_failed = False
            try:
                self.usage_guard.settle(reservation, observed_usage, sent=sent)
                if not sent:
                    receipt['settlement'] = 'NOT_SENT'
            except Exception:
                # Keep the primary safe classification if settlement also fails.
                if error_code != 'NONE':
                    receipt.update(request_diagnostic_stage=stage, request_error_code=error_code)
                receipt.update(status='error', diagnostic_stage='usage_settlement',
                               error_code='USAGE_SETTLEMENT_FAILED')
                settlement_failed = True
            # No data, key, provider exception strings, or imagined zero cost.
            self.receipts.append(receipt)
            dispatch_finalization_failed = False
            if request_policy is not None:
                try:
                    request_policy.finish_dispatch(receipt)
                except Exception:
                    receipt.update(status='error', dispatch_persistence='UNKNOWN')
                    dispatch_finalization_failed = True
            try:
                record_provider_call(model_key=self.spec.model_key, capability=self.spec.role,
                    provider=self.spec.provider, model=self.spec.model_id, status=receipt['status'],
                    usage=observed_usage, latency_ms=receipt['latency_ms'],
                    **({'stage': request_policy.usage_stage} if request_policy is not None else {}))
            except Exception:
                if self.spec.role == 'rerank':
                    # Local diagnostics must not retroactively alter rerank settlement.
                    receipt['telemetry_error_code'] = 'USAGE_CAPTURE_FAILED'
                else:
                    raise
            if settlement_failed:
                raise ProviderUnavailable("MODEL_USAGE_SETTLEMENT_FAILED") from None
            if dispatch_finalization_failed:
                raise ProviderUnavailable('XING_DURABLE_FINALIZATION_FAILED') from None


class SiliconFlowEmbedding(CloudAdapter):
    def __init__(self, spec, *, chunking_index_identity, admission=None, free_binding=None, **kwargs):
        if spec.role != "embedding" or spec.provider != "siliconflow":
            raise ValueError("EMBEDDING_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)
        self.embedding_model, self.embedding_dimension = spec.model_id, spec.dimension
        self.resolved_revision = spec.resolved_revision
        self.embedding_input_semantics_version = spec.input_semantics_version
        self.identity = EmbeddingIdentity(spec.provider, spec.model_id, spec.resolved_revision,
            spec.dimension, "cosine", chunking_index_identity, spec.input_semantics_version)
        self.admission = admission or EmbeddingAdmission()
        self.free_binding = free_binding
        if free_binding is not None:
            self._check_free_binding()
            from backend.app.adapters.models.bge_tokenizer import PinnedBgeM3Tokenizer
            if (self.transport.execution_kind == 'REAL' and type(self.admission.counter) is not PinnedBgeM3Tokenizer
                    or self.transport.execution_kind == 'SIMULATED' and type(self.admission.counter) is not SimulatedEmbeddingCounter):
                raise ValueError('SF_FREE_EMBEDDING_COUNTER_INVALID')

    def _check_free_binding(self):
        from backend.app.application.provider_usage import BudgetUsageGuard
        binding = self.free_binding
        if (type(binding) is not SiliconFlowFreeBinding or type(self.admission) is not EmbeddingAdmission
                or binding.admission is not self.admission
                or binding.authorized_transport is not self.transport or binding.permit is not self.request_window
                or type(self.usage_guard) is not BudgetUsageGuard or self.usage_guard.estimate_microunits != 0
                or self.usage_guard.zero_price_permit is not binding.permit):
            raise ValueError('SF_FREE_BINDING_INVALID')

    @property
    def input_profile_sha256(self):
        return hashlib.sha256(json.dumps(dict(embedding_identity=self.identity.fingerprint,
            tokenizer=self.admission.tokenizer_identity,max_items=self.admission.max_batch_items,
            recipe='full-embedding-input-special-tokens-no-truncation/v1'),sort_keys=True).encode()).hexdigest()

    def embed(self, texts, timeout_seconds, *, purpose='query_embedding',batch_id=None,lease_check=None,
              document_version_ids=(),chunk_ids=()):
        self.preflight(timeout_seconds)
        inputs = list(texts)
        planned = self.admission.count(inputs)

        def validate(response):
            rows = response.get("data")
            if response.get("model") != self.spec.model_id or not isinstance(rows, list) or len(rows) != len(inputs):
                raise ProviderUnavailable("EMBEDDING_RESPONSE_INVALID")
            vectors = [None] * len(inputs)
            for row in rows:
                index, vector = row.get("index"), row.get("embedding")
                if (type(index) is not int or not 0 <= index < len(inputs) or vectors[index] is not None
                        or not isinstance(vector, list) or len(vector) != self.embedding_dimension):
                    raise ProviderUnavailable("EMBEDDING_MAPPING_INVALID")
                for number in vector:
                    try:
                        valid = type(number) in (int, float) and math.isfinite(number) and abs(number) <= 3.4028234663852886e38
                    except (OverflowError, TypeError):
                        valid = False
                    if not valid:
                        raise ProviderUnavailable("EMBEDDING_VALUE_INVALID")
                if not any(vector):
                    raise ProviderUnavailable("EMBEDDING_ZERO_VECTOR")
                vectors[index] = vector
            return vectors

        payload = {"model":self.spec.model_id,"input":inputs,"encoding_format":"float"}
        if self.free_binding is None:
            vectors,usage,latency = self._request('/embeddings',payload,timeout_seconds,validate,planned)
        else:
            self._check_free_binding()
            try: context = current_free_request(purpose,batch_id=batch_id)
            except ValueError: raise ProviderRequestNotSent('SF_FREE_EXPLICIT_REQUEST_CONTEXT_REQUIRED') from None
            request = self._prepare_request('/embeddings',payload)
            policy = _FreeModelRequestPolicy(self.free_binding,context,request,input_tokens=planned,
                input_profile_sha256=self.input_profile_sha256,item_count=len(inputs),timeout=timeout_seconds,
                profile_owner=self,lease_check=lease_check,
                document_version_ids=document_version_ids,chunk_ids=chunk_ids)
            with self.transport.authorize(policy):
                vectors,usage,latency = self._request('/embeddings',payload,timeout_seconds,validate,planned,
                    request_policy=policy,prepared_request=request)
        return EmbeddingResult(vectors, self.spec.model_id, self.embedding_dimension, latency,
                               identity_fingerprint=self.identity.fingerprint, usage_actual=usage)


class CloudChat(CloudAdapter):
    def __init__(self, spec, *, xing_admission=None, xing_binding=None, **kwargs):
        if spec.role not in {"chat_cheap", "chat_expensive"}:
            raise ValueError("CHAT_CAPABILITY_REQUIRED")
        if xing_binding is not None and getattr(xing_binding, 'request_window', None) is not None:
            if 'request_window' in kwargs:
                raise ValueError('V6_XING_REQUEST_WINDOW_INVALID')
            kwargs['request_window'] = xing_binding.request_window
        super().__init__(spec, **kwargs)
        if (xing_admission is None) != (xing_binding is None):
            raise ValueError('XING_BINDING_INVALID')
        if xing_admission is not None:
            if (type(xing_admission) is not XingChatAdmission or spec.role != 'chat_cheap'
                    or (spec.provider, spec.model_id) != ('siliconflow', 'XingChenAGI/Xing4.0-29B')
                    or self.transport is not xing_admission.transport):
                raise ValueError('XING_BINDING_INVALID')
        self.xing_admission, self.xing_binding = xing_admission, xing_binding
        if xing_admission is not None:
            self.execution_kind = xing_admission.grant.execution_kind

    def check_xing_binding(self, binding):
        if (binding is not self.xing_binding or type(self.xing_admission) is not XingChatAdmission
                or binding.grant is not self.xing_admission.grant
                or binding.capacity_counter is not self.xing_admission.counter
                or binding.authorized_transport is not self.transport
                or binding.request_window is not self.request_window
                or type(self.transport) is not XingAuthorizedTransport
                or self.transport.grant is not self.xing_admission.grant
                or self.usage_guard is None):
            raise ProviderRequestNotSent('XING_BINDING_INVALID')

    @staticmethod
    def render_xing_payload(model, messages, max_tokens):
        """Pure renderer shared by preparation and the guarded send path."""
        return dict(model=model, messages=list(messages), max_tokens=max_tokens,
                    stream=False, enable_thinking=False)

    def generate_with_xing_scope(self, messages, *, timeout_seconds, max_tokens=512,
                                 scope, purpose, request_id):
        """Same _request/UsageGuard path, with final wire and scoped authority."""
        self.check_xing_binding(self.xing_binding)
        self.preflight(timeout_seconds)
        if purpose == 'calibration':
            from backend.app.application.provider_usage import BudgetUsageGuard
            if (type(self.usage_guard) is not BudgetUsageGuard
                    or type(self.usage_guard.estimate_microunits) is not int
                    or self.usage_guard.estimate_microunits < 0
                    or self.usage_guard.estimate_microunits == 0
                        and self.usage_guard.zero_price_permit is not self.transport.calibration_permit):
                raise ProviderRequestNotSent('XING_CALIBRATION_MONTHLY_GUARD_REQUIRED')
        admission = self.xing_admission
        admission.transport.check_scope(scope=scope, purpose=purpose, timeout_seconds=timeout_seconds)
        payload = self.render_xing_payload(self.spec.model_id, messages, max_tokens)
        request = self._prepare_request('/chat/completions',payload)
        wire_bytes = request.data
        estimate = admission.counter.measure(self.spec.provider, self.spec.model_id, payload,
            scope=admission.grant.scope_id, wire_bytes=wire_bytes)
        estimate.assert_wire_bytes(wire_bytes)
        with admission.transport.authorize(estimate, scope=scope, purpose=purpose,
                request_id=request_id, timeout_seconds=timeout_seconds):
            observed, usage, latency = self._request('/chat/completions', payload, timeout_seconds,
                self._validate, estimate.local_total_reservation_bound,
                request_policy=_XingRequestPolicy(estimate, admission.transport, purpose),prepared_request=request)
        return ChatResult(observed[0], self.spec.model_id, observed[1], 'stop', usage, latency)

    @staticmethod
    def _validate(response):
        choice = response["choices"][0]
        message, finish = choice["message"], choice["finish_reason"]
        text = message.get("content")
        if message.get("refusal") or not isinstance(text, str) or not text.strip():
            raise ProviderUnavailable("CHAT_REFUSED_OR_INVALID")
        if finish == "length":
            raise TruncatedAnswer(text)
        if finish != "stop":
            raise ProviderUnavailable("CHAT_FINISH_INVALID")
        return text, response.get("model", "UNKNOWN")

    def generate(self, messages, *, timeout_seconds, max_tokens=512):
        if self.xing_admission is not None:
            raise ProviderRequestNotSent('XING_EXPLICIT_SCOPE_REQUIRED')
        if self.spec.provider == 'siliconflow' and self.transport is _send:
            raise ProviderRequestNotSent('XING_BINDING_REQUIRED')
        if (type(max_tokens) is not int or not 1 <= max_tokens <= 1280 or not messages
                or any(set(m) != {"role", "content"} or m["role"] not in {"system", "user", "assistant"}
                       or not isinstance(m["content"], str) for m in messages)):
            raise ProviderRequestNotSent("CHAT_INPUT_INVALID")
        payload = dict(model=self.spec.model_id, messages=list(messages), max_tokens=max_tokens, stream=False)
        if self.spec.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        observed, usage, latency = self._request("/chat/completions", payload, timeout_seconds, self._validate)
        return ChatResult(observed[0], self.spec.model_id, observed[1], "stop", usage, latency)

    def answer(self, prompt, timeout_seconds):
        return self.generate([{"role": "user", "content": prompt}], timeout_seconds=timeout_seconds).text


class SiliconFlowRerank(CloudAdapter):
    def __init__(self, spec, *, free_binding=None, **kwargs):
        if spec.role != "rerank" or spec.provider != "siliconflow":
            raise ValueError("RERANK_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)
        self.last_result = None
        self.circuit_open = False
        self.free_binding = free_binding
        if free_binding is not None:
            from backend.app.application.provider_usage import BudgetUsageGuard
            if (type(free_binding) is not SiliconFlowFreeBinding or type(free_binding.admission) is not RerankAdmission
                    or free_binding.authorized_transport is not self.transport or free_binding.permit is not self.request_window
                    or free_binding.admission.execution_kind != self.transport.execution_kind
                    or self.transport.execution_kind == 'REAL' and getattr(free_binding.admission,
                        '_frozen_source_manifest_sha256',None) != free_binding.permit.source_manifest_sha256
                    or type(self.usage_guard) is not BudgetUsageGuard or self.usage_guard.estimate_microunits != 0
                    or self.usage_guard.zero_price_permit is not free_binding.permit):
                raise ValueError('SF_FREE_BINDING_INVALID')

    @staticmethod
    def render_payload(model, question, documents):
        return dict(model=model, query=question, documents=list(documents),
                    top_n=len(documents), return_documents=True)

    def rank(self, question, hits, chunks):
        self.last_result = None
        if self.circuit_open:
            raise ProviderRequestNotSent('RERANK_CIRCUIT_OPEN')
        hits = tuple(hits)
        if not question or not hits or len({h.chunk_id for h in hits}) != len(hits) or any(h.chunk_id not in chunks for h in hits):
            raise ProviderRequestNotSent("RERANK_INPUT_INVALID")
        documents = [chunks[h.chunk_id].content for h in hits]

        def validate(response):
            diagnostics = _response_diagnostics(response, received=True, expected_results=len(hits))
            def fail(public_code, safe_code, reason, row=None, document_type='unavailable'):
                details = dict(diagnostics, first_failure_row=row,
                               failure_reason=reason, document_type=document_type)
                raise _DiagnosticFailure(public_code, 'rerank_validation', safe_code, details)
            if 'results' not in response:
                fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULTS_MISSING')
            rows = response['results']
            if not isinstance(rows, list):
                fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULTS_NOT_ARRAY')
            if len(rows) != len(hits):
                fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULT_COUNT_MISMATCH')
            seen, result, scores = set(), [], []
            for row_position, row in enumerate(rows):
                if not isinstance(row, dict):
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULT_ROW_NOT_OBJECT', row_position)
                document_type = _json_type(row['document']) if 'document' in row else 'missing'
                if 'document' not in row:
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'DOCUMENT_MISSING',
                         row_position, document_type)
                if not isinstance(row['document'], dict):
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'DOCUMENT_TYPE_INVALID',
                         row_position, document_type)
                if not isinstance(row['document'].get('text'), str):
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'DOCUMENT_TEXT_INVALID',
                         row_position, document_type)
                index, score = row.get("index"), row.get("relevance_score")
                if type(index) is not int:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'INDEX_NOT_INTEGER', row_position, document_type)
                if index in seen:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'INDEX_DUPLICATE', row_position, document_type)
                if not 0 <= index < len(hits):
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'INDEX_OUT_OF_RANGE', row_position, document_type)
                if row['document']['text'] != documents[index]:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'DOCUMENT_TEXT_MISMATCH',
                         row_position, document_type)
                try:
                    valid_score = type(score) in (int, float) and math.isfinite(score)
                except (OverflowError, TypeError):
                    valid_score = False
                if not valid_score:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_SCORE_INVALID', 'SCORE_INVALID',
                         row_position, document_type)
                seen.add(index)
                # Preserve original rank, scores, sources and chunk identity.
                result.append(hits[index])
                scores.append(float(score))
            return tuple(result), tuple(scores)

        try:
            payload = self.render_payload(self.spec.model_id, question, documents)
            if self.free_binding is None:
                result,usage,latency = self._request('/rerank',payload,30,validate,
                    usage_parser=_rerank_usage,response_diagnostics=True,expected_results=len(hits))
            else:
                self.preflight(30)
                try: context = current_free_request('rerank')
                except ValueError: raise ProviderRequestNotSent('SF_FREE_EXPLICIT_REQUEST_CONTEXT_REQUIRED') from None
                planned = self.free_binding.admission.count(question,documents)
                request = self._prepare_request('/rerank',payload)
                policy = _FreeModelRequestPolicy(self.free_binding,context,request,input_tokens=planned,
                    input_profile_sha256=self.free_binding.admission.input_profile_sha256,item_count=len(hits),timeout=30,
                    profile_owner=self.free_binding.admission,
                    document_version_ids=tuple(str(chunks[h.chunk_id].version_id) for h in hits),
                    chunk_ids=tuple(h.chunk_id for h in hits),candidate_order=tuple(h.chunk_id for h in hits))
                with self.transport.authorize(policy):
                    result,usage,latency = self._request('/rerank',payload,30,validate,planned,
                        usage_parser=_rerank_usage,response_diagnostics=True,expected_results=len(hits),
                        request_policy=policy,prepared_request=request)
        except ProviderRequestNotSent:
            # A local permission/credential/budget denial sent nothing and must
            # not poison unrelated authorized KB requests on this instance.
            raise
        except ProviderUnavailable:
            # No automatic retry/probe after an uncertain or rejected send.
            self.circuit_open = True
            raise
        self.last_result = RerankResult(result[0], result[1], latency, usage)
        return self.last_result.hits


class DeepSeekVision(CloudAdapter):
    def __init__(self, spec, **kwargs):
        if spec.role != "vision" or spec.provider != "deepseek":
            raise ValueError("VISION_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)

    def caption_preflight(self, timeout_seconds):
        self.preflight(timeout_seconds)

    def caption_image(self, image_bytes, timeout_seconds):
        digest = validate_caption_image(image_bytes)
        # Actual bytes validated above, never forward caller-supplied URLs/MIME.
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(image_bytes)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(image.format)
        if mime is None:
            raise ProviderRequestNotSent("VISION_IMAGE_FORMAT_UNSUPPORTED")
        payload = dict(model=self.spec.model_id, max_tokens=512, stream=False,
            thinking={"type": "disabled"}, messages=[{"role": "user", "content": [
                {"type": "text", "text": "Describe this figure briefly. Do not infer precise table values."},
                {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," +
                    base64.b64encode(image_bytes).decode(), "detail": "original"}}]}])
        observed, usage, latency = self._request("/chat/completions", payload, timeout_seconds, CloudChat._validate)
        return CaptionResult(observed[0], self.spec.model_id, observed[1], None, digest,
            "stop", usage, latency, prompt_version="deepseek-figure-caption/v1")
