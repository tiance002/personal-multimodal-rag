"""Fixed Owner calibration authority, never a general zero-cost switch."""
from dataclasses import dataclass, field
import hashlib

OWNER_CALIBRATION_RUN_ID = 'b82329fe-b0ee-493c-9498-0aefdb5fc22e'


def validate_zero_permit(permit, spec):
    if type(permit) is SiliconFlowFreePermit:
        permit.validate(spec)
        return
    from backend.app.adapters.models.cloud import (
        XingCalibrationPermit, XING_OWNER_CALIBRATION_REQUEST_ID,
        XingCohortCalibrationClaimStore, XingZeroPriceContract,
    )
    if (type(permit) is not XingCalibrationPermit
            or type(permit.price_contract) is not XingZeroPriceContract
            or type(permit.claim_store) is not XingCohortCalibrationClaimStore):
        raise ValueError('XING_ZERO_CALIBRATION_PERMIT_REQUIRED')
    permit.price_contract.validate()
    identity = permit.claim_store.identity
    identity.validate()
    if ((identity.run_id, identity.request_id) !=
            (OWNER_CALIBRATION_RUN_ID, XING_OWNER_CALIBRATION_REQUEST_ID)
            or (spec.provider, spec.model_id, spec.role) !=
            ('siliconflow', 'XingChenAGI/Xing4.0-29B', 'chat_cheap')):
        raise ValueError('XING_ZERO_CALIBRATION_IDENTITY_MISMATCH')


@dataclass(frozen=True)
class XingCalibrationZeroReservation:
    permit: object = field(repr=False)
    transport: object = field(repr=False)
    estimate: object = field(repr=False)
    wire_bytes: bytes = field(repr=False)
    timeout_seconds: int = 10

    def validate(self, *, run_id, provider, model_name, capability, planned_tokens=None):
        from backend.app.adapters.models.cloud import XingAuthorizedTransport, XING_CALIBRATION_WIRE_SHA256
        from backend.app.adapters.models.siliconflow_xing_capacity import XingCapacityEstimate, RENDERER_IDENTITY, OFFICIAL_CONTEXT_WINDOW
        from backend.app.domain.model_registry import ModelRegistry
        if (type(self) is not XingCalibrationZeroReservation
                or type(self.transport) is not XingAuthorizedTransport
                or type(self.estimate) is not XingCapacityEstimate
                or type(self.wire_bytes) is not bytes
                or (self.estimate.provider, self.estimate.model) != (provider, model_name)
                or self.estimate.renderer_identity != RENDERER_IDENTITY
                or self.estimate.context_window != OFFICIAL_CONTEXT_WINDOW
                or type(self.estimate.reservation_bound) is not int
                or type(self.estimate.output_reservation) is not int
                or hashlib.sha256(self.wire_bytes).hexdigest() != XING_CALIBRATION_WIRE_SHA256
                or self.estimate.wire_request_sha256 != XING_CALIBRATION_WIRE_SHA256
                or not self.estimate.fits_local_policy
                or self.transport.calibration_permit is not self.permit
                or (provider, model_name, capability) !=
                    ('siliconflow', 'XingChenAGI/Xing4.0-29B', 'chat_cheap')):
            raise ValueError('XING_ZERO_CALIBRATION_AUTHORITY_INVALID')
        validate_zero_permit(self.permit, ModelRegistry.frozen_defaults().select('chat_cheap'))
        identity = self.permit.claim_store.identity
        if self.estimate.scope_sha256 != hashlib.sha256(identity.scope.encode()).hexdigest():
            raise ValueError('XING_ZERO_CALIBRATION_SCOPE_MISMATCH')
        # The calibration center owns the Run; model_calls may have no product
        # Run FK. Its reservation ID is durably linked by the existing claim.
        if run_id is not None and run_id != identity.run_id:
            raise ValueError('XING_ZERO_CALIBRATION_RUN_MISMATCH')
        context = self.transport._context.get()
        if (context is None or context != (self.estimate, identity.scope, 'calibration',
                                          identity.request_id, self.timeout_seconds)):
            raise ValueError('XING_ZERO_CALIBRATION_CONTEXT_REQUIRED')
        self.estimate.assert_wire_bytes(self.wire_bytes)
        if planned_tokens is not None and planned_tokens != self.estimate.local_total_reservation_bound:
            raise ValueError('XING_ZERO_CALIBRATION_TOKEN_MISMATCH')
        self.transport.check_scope(scope=identity.scope, purpose='calibration',
                                   timeout_seconds=self.timeout_seconds)
        self.permit.validate(self.transport.grant, self.estimate, identity.request_id, self.timeout_seconds)


# The old fixed calibration types above retain their identity and stricter
# deadline. This extension is explicitly injected for the three Owner V6 models.
from datetime import datetime, timezone
import base64
import json
import re
from dataclasses import asdict
from backend.app.domain.scope import Scope

FREE_PRICE_SHA256 = '59c932ec3e6c602d753299cee3ce5a11dc03addcc4b177b768b33d016241b530'
FREE_OWNER_DEADLINE = '2026-10-10T07:44:02Z'
FREE_MODEL_PURPOSES = {
    ('BAAI/bge-m3', 'embedding'): frozenset({'index_embedding', 'query_embedding'}),
    ('BAAI/bge-reranker-v2-m3', 'rerank'): frozenset({'rerank'}),
    ('XingChenAGI/Xing4.0-29B', 'chat_cheap'): frozenset({'answer', 'understand', 'rewrite', 'expansion'}),
}


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


@dataclass(frozen=True)
class SiliconFlowZeroPriceContract:
    model: str
    source_sha256: str = FREE_PRICE_SHA256
    source_url: str = 'https://www.siliconflow.cn/models'
    currency: str = 'CNY'
    prompt_per_million: int = 0
    completion_per_million: int = 0

    @classmethod
    def from_frozen_html(cls, path, model):
        from pathlib import Path
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != FREE_PRICE_SHA256:
            raise ValueError('SF_FREE_PRICE_SOURCE_UNVERIFIED')
        result = cls(model)
        result.validate()
        return result

    def validate(self):
        if (type(self) is not SiliconFlowZeroPriceContract
                or self.model not in {m for m, _ in FREE_MODEL_PURPOSES}
                or self.source_sha256 != FREE_PRICE_SHA256
                or self.source_url != 'https://www.siliconflow.cn/models' or self.currency != 'CNY'
                or type(self.prompt_per_million) is not int or self.prompt_per_million != 0
                or type(self.completion_per_million) is not int or self.completion_per_million != 0):
            raise ValueError('SF_FREE_PRICE_SOURCE_UNVERIFIED')


_PUBLIC_QID_IDENTITY_PREFIX = 'rag-public-qid-v1-'


def public_qid_identity(qid: str) -> str:
    """Injective, reversible internal view; never rewrite the public/Gold QID.

    Retain the existing opaque ASCII grammar and admit only MIRACL's additional
    decimal document#passage shape. Encode every QID, including strings that
    resemble encoded IDs, so aliases, separators and leading zeroes cannot merge.
    """
    if (type(qid) is not str or not 1 <= len(qid) <= 160
            or not (re.fullmatch('[A-Za-z0-9_.:/-]+', qid)
                    or re.fullmatch('[0-9]+#[0-9]+', qid))):
        raise ValueError('PUBLIC_QID_INVALID')
    return _PUBLIC_QID_IDENTITY_PREFIX + base64.urlsafe_b64encode(qid.encode('ascii')).decode('ascii').rstrip('=')


def public_qid_from_identity(value: str) -> str:
    """Decode only the canonical spelling; no padding/percent/Unicode aliases."""
    try:
        if (type(value) is not str or not re.fullmatch(
                re.escape(_PUBLIC_QID_IDENTITY_PREFIX) + '[A-Za-z0-9_-]{2,214}', value)):
            raise ValueError('invalid identity shape')
        encoded = value[len(_PUBLIC_QID_IDENTITY_PREFIX):]
        qid = base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                               altchars=b'-_', validate=True).decode('ascii')
        if public_qid_identity(qid) != value:
            raise ValueError('noncanonical identity')
        return qid
    except (TypeError, ValueError) as exc:
        raise ValueError('PUBLIC_QID_IDENTITY_INVALID') from exc


@dataclass(frozen=True)
class FreeModelPlannedCall:
    request_id: str
    qid: str
    arm: str
    purpose: str
    batch_id: str
    input_profile_sha256: str
    wire_sha256: str
    input_token_bound: int
    output_token_bound: int
    item_count: int
    timeout_seconds: int
    call_ordinal: int = 1
    document_version_ids: tuple[str,...] = ()
    chunk_ids: tuple[str,...] = ()
    input_sha256: tuple[str,...] = ()
    candidate_order: tuple[str,...] = ()
    query_sha256: str | None = None

    @property
    def qid_identity(self) -> str:
        # Derived, not a dataclass field: historical fingerprints, persisted
        # original QIDs and exact native claim comparisons remain unchanged.
        return public_qid_identity(self.qid)


@dataclass(frozen=True)
class SiliconFlowFreePermit:
    grant_id: str
    run_id: str
    scope: Scope
    deadline_utc: str | None
    source_manifest_sha256: str
    authority_manifest_sha256: str
    model: str
    role: str
    price_contract: SiliconFlowZeroPriceContract
    calls: tuple[FreeModelPlannedCall, ...]
    token_cap: int
    frozen_plan_sha256: str
    execution_kind: str = 'REAL'
    provider: str = 'siliconflow'
    owner_activity_id: str | None = None
    execution_lease: object | None = None

    @property
    def fingerprint(self):
        payload = dict(grant_id=self.grant_id, run_id=self.run_id,
            scope=dict(kb=sorted(self.scope.knowledge_base_ids),docs=sorted(self.scope.document_ids)),
            deadline_utc=self.deadline_utc,source_manifest_sha256=self.source_manifest_sha256,
            authority_manifest_sha256=self.authority_manifest_sha256,model=self.model,role=self.role,
            price_contract=asdict(self.price_contract),calls=[asdict(c) for c in self.calls],
            token_cap=self.token_cap,execution_kind=self.execution_kind,provider=self.provider)
        if self.owner_activity_id is not None:
            from backend.app.application.validation_usd_budget import V6_OWNER_ACTIVITY_R3_SOURCE
            payload.update(owner_activity_id=self.owner_activity_id,owner_policy_source=V6_OWNER_ACTIVITY_R3_SOURCE)
            payload['execution_lease']=asdict(self.execution_lease) if self.execution_lease is not None else None
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()

    @property
    def planned_token_cap(self):
        return self.token_cap

    def owner_deadline(self):
        """Only explicit R3 metadata has no Owner cutoff; legacy remains R2."""
        if self.owner_activity_id is not None:
            from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT_R3,V6ExecutionLease
            if (type(self.owner_activity_id) is not str or self.owner_activity_id!=OWNER_V6_TOKEN_GRANT_R3.grant_id
                    or type(self.grant_id) is not str or not self.grant_id.startswith(self.owner_activity_id+':')
                    or self.deadline_utc is not None or type(self.execution_lease) is not V6ExecutionLease):
                raise ValueError('SF_FREE_OWNER_ACTIVITY_INVALID')
            self.execution_lease.validate()
            return self.execution_lease.deadline
        if self.execution_lease is not None:
            raise ValueError('SF_FREE_OWNER_ACTIVITY_INVALID')
        deadline=datetime.fromisoformat(self.deadline_utc.replace('Z','+00:00'))
        if deadline.tzinfo is None or deadline>datetime.fromisoformat(FREE_OWNER_DEADLINE.replace('Z','+00:00')):
            raise ValueError('SF_FREE_OWNER_ACTIVITY_INVALID')
        return deadline

    @property
    def owner_total_deadline_utc(self):
        self.owner_deadline()
        return self.execution_lease.owner_deadline_utc if self.owner_activity_id is not None else FREE_OWNER_DEADLINE

    def validate(self, spec, transport=None):
        try:
            self.price_contract.validate()
            deadline = self.owner_deadline()
            purposes = FREE_MODEL_PURPOSES.get((self.model,self.role), frozenset())
            valid = (type(self) is SiliconFlowFreePermit and type(self.price_contract) is SiliconFlowZeroPriceContract
                and self.provider == 'siliconflow' and (spec.provider,spec.model_id,spec.role) == (self.provider,self.model,self.role)
                and self.price_contract.model == self.model and bool(purposes)
                and type(self.scope) is Scope and type(self.scope.knowledge_base_ids) is frozenset
                and type(self.scope.document_ids) is frozenset and bool(self.scope.knowledge_base_ids)
                and all(type(v) is str and v for v in self.scope.knowledge_base_ids | self.scope.document_ids)
                and all(type(v) is str and re.fullmatch('[A-Za-z0-9_.:/-]{1,160}',v) for v in (self.grant_id,self.run_id))
                and (deadline is None or deadline.tzinfo is not None)
                and self.execution_kind in ('REAL','SIMULATED')
                and _sha(self.source_manifest_sha256) and _sha(self.authority_manifest_sha256)
                and _sha(self.frozen_plan_sha256) and self.frozen_plan_sha256 == self.fingerprint
                and type(self.calls) is tuple and bool(self.calls)
                and len({c.request_id for c in self.calls}) == len(self.calls)
                and len({(c.qid,c.arm,c.purpose,c.call_ordinal) for c in self.calls}) == len(self.calls)
                and all(type(c) is FreeModelPlannedCall and c.purpose in purposes
                    and bool(c.qid_identity)
                    and all(type(v) is str and re.fullmatch('[A-Za-z0-9_.:/-]{1,160}',v) for v in (c.request_id,c.batch_id))
                    and c.arm in ('CHEAP_ONLY','DYNAMIC','EXPENSIVE_ONLY','INDEXING','SHARED_RETRIEVAL')
                    and (c.arm=='INDEXING' if c.purpose=='index_embedding' else c.arm!='INDEXING')
                    and (self.role!='chat_cheap' or c.arm in ('CHEAP_ONLY','DYNAMIC','EXPENSIVE_ONLY'))
                    and not (self.role == 'chat_cheap' and c.purpose == 'answer' and c.arm == 'EXPENSIVE_ONLY')
                    and _sha(c.input_profile_sha256) and _sha(c.wire_sha256)
                    and type(c.input_token_bound) is int and c.input_token_bound > 0
                    and type(c.output_token_bound) is int and (1 <= c.output_token_bound <= 512 if self.role == 'chat_cheap' else c.output_token_bound == 0)
                    and type(c.item_count) is int and 1 <= c.item_count <= (1 if self.role == 'chat_cheap' else 32)
                    and type(c.call_ordinal) is int and 1 <= c.call_ordinal <= len(self.calls)
                    and (c.call_ordinal == 1 or c.purpose in ('index_embedding','query_embedding','rerank'))
                    and type(c.timeout_seconds) is int and 1 <= c.timeout_seconds <= 120 for c in self.calls)
                and type(self.token_cap) is int and self.token_cap == sum(c.input_token_bound+c.output_token_bound for c in self.calls))
            if transport is not None:
                from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport, XingAuthorizedTransport
                valid = valid and (type(transport) is SiliconFlowAuthorizedTransport and transport.permit is self
                    or type(transport) is XingAuthorizedTransport and transport.free_model_permit is self
                        and self.role == 'chat_cheap' and transport.grant.scope == self.scope
                        and transport.grant.grant_id == self.grant_id and transport.grant.deadline_utc == self.deadline_utc)
        except (AttributeError,TypeError,ValueError):
            valid = False
        if not valid:
            raise ValueError('SF_FREE_PERMIT_INVALID')

    def planned_call(self, spec, policy, timeout):
        self.validate(spec,policy.transport)
        call = next((c for c in self.calls if c.request_id == policy.request_id),None)
        if (call is None or call.purpose != policy.purpose or timeout != call.timeout_seconds
                or policy.scope != self.scope or call.input_profile_sha256 != policy.input_profile_sha256
                or call.wire_sha256 != hashlib.sha256(policy.wire_bytes).hexdigest()
                or call.item_count != policy.item_count
                or policy.input_tokens > call.input_token_bound or policy.output_tokens > call.output_token_bound):
            raise ValueError('SF_FREE_REQUEST_NOT_PLANNED')
        return call

    def validate_claim(self, call, claim):
        if (claim is None or (claim.run_id,claim.qid,claim.arm,claim.request_id,claim.purpose,claim.call_ordinal)
                != (self.run_id,call.qid,call.arm,call.request_id,call.purpose,call.call_ordinal)):
            raise ValueError('SF_FREE_NATIVE_CLAIM_MISMATCH')


@dataclass(frozen=True)
class SiliconFlowZeroReservation:
    permit: SiliconFlowFreePermit = field(repr=False)
    transport: object = field(repr=False)
    policy: object = field(repr=False)
    wire_bytes: bytes = field(repr=False)
    planned_tokens: int

    def validate(self, *, run_id, provider, model_name, capability, planned_tokens=None):
        from backend.app.domain.model_registry import ModelRegistry
        from backend.app.adapters.models.cloud import _FreeModelRequestPolicy, _XingRequestPolicy
        if (type(self) is not SiliconFlowZeroReservation or type(self.permit) is not SiliconFlowFreePermit
                or type(self.policy) not in (_FreeModelRequestPolicy,_XingRequestPolicy)
                or self.policy.transport is not self.transport
                or type(self.wire_bytes) is not bytes or self.wire_bytes != self.policy.wire_bytes
                or (provider,model_name,capability) != (self.permit.provider,self.permit.model,self.permit.role)
                or run_id is not None and run_id != self.permit.run_id
                or type(self.planned_tokens) is not int or self.planned_tokens <= 0
                or self.planned_tokens != self.policy.input_tokens+self.policy.output_tokens
                or planned_tokens is not None and (type(planned_tokens) is not int or planned_tokens != self.planned_tokens)):
            raise ValueError('SF_ZERO_REQUEST_AUTHORITY_INVALID')
        spec = ModelRegistry.frozen_defaults().select(capability)
        call = self.permit.planned_call(spec,self.policy,self.policy.timeout)
        self.policy.validate_active_context()
        if call.input_token_bound+call.output_token_bound < self.planned_tokens:
            raise ValueError('SF_ZERO_TOKEN_BOUND_EXCEEDED')


@dataclass(frozen=True)
class SiliconFlowFreeBinding:
    permit: SiliconFlowFreePermit
    authorized_transport: object = field(repr=False)
    admission: object = field(repr=False)
    result_storage: object | None = field(default=None,repr=False)

    def read_result(self,summary,*,request_id):
        """Verify the actual saved result before any recovery/reuse."""
        from backend.app.ports.providers import ProviderUnavailable
        try:
            call=next(c for c in self.permit.calls if c.request_id==request_id)
            if (type(summary) is not dict or set(summary)!={'result_sha256','result_ref','item_count'}
                    or not _sha(summary['result_sha256']) or summary['result_ref']!='sha256:'+summary['result_sha256']
                    or type(summary['item_count']) is not int or summary['item_count']!=call.item_count):
                raise ValueError()
            digest=summary['result_sha256']
            raw=self.result_storage.read(f'objects/{digest[:2]}/{digest}')
            if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError()
            result=json.loads(raw)
            if (result['schema_version']!=1 or result['run_id']!=self.permit.run_id
                    or result['request_id']!=call.request_id or result['call_ordinal']!=call.call_ordinal
                    or result['purpose']!=call.purpose or result['wire_sha256']!=call.wire_sha256
                    or result['input_profile_sha256']!=call.input_profile_sha256
                    or result['model']!=self.permit.model or result['source_manifest_sha256']!=self.permit.source_manifest_sha256
                    or result['authority_manifest_sha256']!=self.permit.authority_manifest_sha256):
                raise ValueError()
            return result
        except (OSError,ValueError,TypeError,KeyError,AttributeError,StopIteration):
            raise ProviderUnavailable('SF_FREE_RESULT_ARTIFACT_UNVERIFIED') from None


from contextlib import contextmanager
from contextvars import ContextVar


@dataclass(frozen=True)
class FreeModelRequestContext:
    run_id: str
    request_id: str
    purpose: str
    batch_id: str
    scope: Scope
    batch_plan: object | None = field(default=None,repr=False)


_FREE_REQUESTS = ContextVar('sf_free_model_requests',default=())


@contextmanager
def free_model_requests(contexts):
    if (type(contexts) is not tuple or not contexts
            or any(type(c) is not FreeModelRequestContext for c in contexts)
            or len({c.request_id for c in contexts}) != len(contexts)):
        raise ValueError('SF_FREE_REQUEST_CONTEXT_INVALID')
    token = _FREE_REQUESTS.set(contexts)
    try:
        yield
    finally:
        _FREE_REQUESTS.reset(token)


def current_free_request(purpose, *, batch_id=None):
    matches = [c for c in _FREE_REQUESTS.get() if c.purpose == purpose
               and (batch_id is None or c.batch_id == batch_id)]
    if len(matches) != 1:
        raise ValueError('SF_FREE_EXPLICIT_REQUEST_CONTEXT_REQUIRED')
    return matches[0]


def free_dispatch_contract(permit,call):
    """The native plan freezes this exact identity; no body/key is retained."""
    return dict(grant_id=permit.grant_id,run_id=permit.run_id,source_manifest_sha256=permit.source_manifest_sha256,
        authority_manifest_sha256=permit.authority_manifest_sha256,frozen_plan_sha256=permit.frozen_plan_sha256,
        price_source_sha256=permit.price_contract.source_sha256,scope_sha256=hashlib.sha256(json.dumps(
            dict(kb=sorted(permit.scope.knowledge_base_ids),docs=sorted(permit.scope.document_ids)),sort_keys=True).encode()).hexdigest(),
        call=asdict(call),deadline_utc=permit.deadline_utc,execution_kind=permit.execution_kind)


def free_dispatch_identity_sha256(permit,call):
    return hashlib.sha256(json.dumps(free_dispatch_contract(permit,call),sort_keys=True,separators=(',',':')).encode()).hexdigest()
