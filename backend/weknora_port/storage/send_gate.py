"""Fail-closed P1 send authority and isolated PostgreSQL monthly cost ledger.

Budget algorithm is a semantic port of readonly application/budget.py, not an
import of its runtime. No key loading, HTTP, provider calls or automatic grants.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from ..models.types import PreparedCall, ProviderError, TokenUsage
from .postgres import PostgresRetriever


@dataclass(frozen=True)
class ContentOrigin:
    kind: str
    source_sha256: str
    knowledge_base_ids: tuple[str, ...]
    verified: bool = False
    public_static: bool = False


@dataclass(frozen=True)
class SendAuthorization:
    """Server-built manifest covering the EXACT wire; never request JSON."""
    request_id: str
    owner_id: str
    provider: str
    model: str
    model_id: str
    endpoint: str
    capability: str
    wire_sha256: str
    origins: tuple[ContentOrigin, ...]
    readable_origin_kbs: frozenset[str]
    cloud_allowed_origin_kbs: frozenset[str]
    approval_source: str = ""
    authorized: bool = False
    global_outbound_allowed: bool = False
    provenance_complete: bool = False
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class MonthlyAuthority:
    account_id: str
    month: date
    monthly_budget_microunits: int
    carry_in_microunits: int | None
    carry_in_status: str
    carry_in_source: str
    approval_source: str


@dataclass(frozen=True)
class ReservationEstimate:
    microunits: int | None
    input_tokens_upper_bound: int | None
    price_source: str
    estimator_source: str
    known_zero_price: bool = False


@dataclass(frozen=True)
class ActualCost:
    microunits: int | None
    price_source: str
    known_zero_price: bool = False


class PermissionResolver(Protocol):
    async def resolve(self, call: PreparedCall) -> SendAuthorization: ...


class CostAuthority(Protocol):
    async def monthly(self, call: PreparedCall) -> MonthlyAuthority: ...
    async def estimate(self, call: PreparedCall) -> ReservationEstimate: ...
    async def actual(self, call: PreparedCall, usage: TokenUsage, price_source: str) -> ActualCost: ...


def _json(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def _object(value): return json.loads(value) if isinstance(value,str) else value


class P1SendGate:
    def __init__(self, identity_guard: PostgresRetriever, *, tenant_id: int,
                 permissions: PermissionResolver | None = None, costs: CostAuthority | None = None):
        if type(tenant_id) is not int or tenant_id<=0: raise ProviderError("MODEL_GATE_TENANT_REQUIRED")
        self.guard,self.connection,self.tenant_id=identity_guard,identity_guard.connection,tenant_id
        self.permissions,self.costs=permissions,costs

    def _call(self,call):
        self.guard.require_verified()
        if not isinstance(call,PreparedCall) or not call.context.request_id or not call.context.scope.owner_id or not call.context.scope.knowledge_base_ids:
            raise ProviderError("MODEL_TRUSTED_SCOPE_REQUIRED")
        if not re.fullmatch('[0-9a-f]{64}',call.wire_sha256) or type(call.wire_bytes) is not int or call.wire_bytes<=0:
            raise ProviderError("MODEL_WIRE_IDENTITY_REQUIRED")
        if call.context.cancelled and call.context.cancelled.is_set(): raise ProviderError("MODEL_CALL_CANCELLED")
        return (call.context.request_id,self.tenant_id,call.context.scope.owner_id)

    async def _rows(self,sql,params=()):
        self.guard.require_verified()
        return await (await self.connection.execute(sql,params)).fetchall()

    async def _one(self,sql,params=()):
        rows=await self._rows(sql,params);return rows[0] if rows else None

    def _permission(self,call,grant):
        if not isinstance(grant,SendAuthorization) or not grant.authorized or not grant.approval_source or not grant.global_outbound_allowed:
            raise ProviderError("MODEL_SEND_AUTHORIZATION_REQUIRED")
        expected=(call.context.request_id,call.context.scope.owner_id,call.provider,call.model,call.model_id,call.endpoint,call.capability,call.wire_sha256)
        actual=(grant.request_id,grant.owner_id,grant.provider,grant.model,grant.model_id,grant.endpoint,grant.capability,grant.wire_sha256)
        if actual!=expected: raise ProviderError("MODEL_SEND_IDENTITY_MISMATCH")
        if grant.expires_at is None or grant.expires_at.tzinfo is None or grant.expires_at<=datetime.now(timezone.utc):
            raise ProviderError("MODEL_SEND_AUTHORIZATION_EXPIRED")
        if not grant.provenance_complete or not isinstance(grant.origins,tuple) or not grant.origins:
            raise ProviderError("MODEL_INPUT_PROVENANCE_UNKNOWN")
        origin_kbs=set()
        for origin in grant.origins:
            if not isinstance(origin,ContentOrigin) or not origin.verified or not re.fullmatch('[0-9a-f]{64}',origin.source_sha256):
                raise ProviderError("MODEL_INPUT_PROVENANCE_UNKNOWN")
            if origin.kind not in ('question','history','memory','evidence','image','document','system'):
                raise ProviderError("MODEL_INPUT_PROVENANCE_UNKNOWN")
            if origin.public_static:
                if origin.kind!='system' or origin.knowledge_base_ids: raise ProviderError("MODEL_INPUT_PROVENANCE_UNKNOWN")
            elif not origin.knowledge_base_ids:
                raise ProviderError("MODEL_INPUT_PROVENANCE_UNKNOWN")
            origin_kbs.update(origin.knowledge_base_ids)
        required = call.context.required_origin_kbs
        if not isinstance(required, tuple) or not all(isinstance(k, str) and k for k in required):
            raise ProviderError("MODEL_INPUT_PROVENANCE_UNKNOWN")
        origin_kbs.update(required)
        checked_kbs=origin_kbs|set(call.context.scope.knowledge_base_ids)
        if not checked_kbs.issubset(grant.readable_origin_kbs): raise ProviderError("MODEL_ORIGIN_KB_ACCESS_DENIED")
        if not checked_kbs.issubset(grant.cloud_allowed_origin_kbs): raise ProviderError("MODEL_ORIGIN_KB_EGRESS_DENIED")
        if call.capability.startswith('chat'):
            if type(call.output_budget) is not int or call.output_budget<=0 or type(grant.max_output_tokens) is not int or call.output_budget>grant.max_output_tokens:
                raise ProviderError("MODEL_OUTPUT_HARD_LIMIT_DENIED")
        return tuple(sorted(origin_kbs))

    def _authority(self,authority,estimate,grant,month):
        if not isinstance(authority,MonthlyAuthority) or not authority.account_id or authority.month!=month or not authority.approval_source:
            raise ProviderError("MONTHLY_BUDGET_AUTHORITY_REQUIRED")
        if type(authority.monthly_budget_microunits) is not int or authority.monthly_budget_microunits<0:
            raise ProviderError("MONTHLY_BUDGET_AUTHORITY_REQUIRED")
        if type(authority.carry_in_microunits) is not int or authority.carry_in_microunits<0 or not authority.carry_in_source or authority.carry_in_status not in ('KNOWN','UNKNOWN'):
            raise ProviderError("MONTHLY_CARRY_IN_AUTHORITY_REQUIRED")
        if authority.carry_in_status=='UNKNOWN' and authority.carry_in_microunits==0:
            raise ProviderError("MONTHLY_CARRY_IN_UNKNOWN_NOT_ZERO")
        if not isinstance(estimate,ReservationEstimate) or type(estimate.microunits) is not int or estimate.microunits<0 or not estimate.price_source or not estimate.estimator_source:
            raise ProviderError("MODEL_COST_ESTIMATE_UNAVAILABLE")
        if estimate.microunits==0 and not estimate.known_zero_price: raise ProviderError("MODEL_ZERO_PRICE_AUTHORITY_REQUIRED")
        if type(grant.max_input_tokens) is not int or grant.max_input_tokens<=0 or type(estimate.input_tokens_upper_bound) is not int or estimate.input_tokens_upper_bound<=0 or estimate.input_tokens_upper_bound>grant.max_input_tokens:
            raise ProviderError("MODEL_INPUT_HARD_LIMIT_DENIED")

    async def _month_lock(self,account,month):
        await self.connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(f"personal-rag-budget:{account}:{month.isoformat()}",))

    async def authorize(self,call):
        request,tenant,owner=self._call(call)
        if self.permissions is None or self.costs is None: raise ProviderError("MODEL_SEND_AUTHORIZATION_REQUIRED")
        grant=await self.permissions.resolve(call)
        origin_kbs=self._permission(call,grant)
        authority,estimate=await self.costs.monthly(call),await self.costs.estimate(call)
        async with self.connection.transaction():
            clock=await self._one("SELECT (date_trunc('month',now() AT TIME ZONE 'UTC'))::date AS month")
            month=clock['month'];self._authority(authority,estimate,grant,month)
            await self._month_lock(authority.account_id,month)
            existing=await self._one("SELECT request_id FROM send_calls WHERE request_id=%s",(request,))
            if existing: raise ProviderError("MODEL_CALL_ID_REUSE_DENIED")
            await self.connection.execute("INSERT INTO send_budget_months(account_id,month,budget_microunits,carry_in_microunits,carry_in_status,carry_in_source,approval_source) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(account_id,month) DO NOTHING",(authority.account_id,month,authority.monthly_budget_microunits,authority.carry_in_microunits,authority.carry_in_status,authority.carry_in_source,authority.approval_source))
            stored=await self._one("SELECT * FROM send_budget_months WHERE account_id=%s AND month=%s FOR UPDATE",(authority.account_id,month))
            if any(stored[name]!=getattr(authority,name) for name in ('carry_in_microunits','carry_in_status','carry_in_source','approval_source')) or stored['budget_microunits']!=authority.monthly_budget_microunits:
                raise ProviderError("MONTHLY_BUDGET_AUTHORITY_CONFLICT")
            if stored['blocked_reason']: raise ProviderError("MONTHLY_BUDGET_RECONCILIATION_REQUIRED")
            used=await self._one("SELECT COALESCE(SUM(CASE WHEN state IN ('reserved','unknown') THEN reserved_microunits WHEN state='settled' THEN actual_microunits ELSE 0 END),0) AS used FROM send_calls WHERE account_id=%s AND budget_month=%s",(authority.account_id,month))
            if authority.carry_in_microunits+used['used']+estimate.microunits>authority.monthly_budget_microunits:
                raise ProviderError("MONTHLY_BUDGET_EXCEEDED")
            # Quote/lock waits cannot extend an expired authorization. The next
            # attempt also invokes the resolver afresh, including upgrades.
            self._permission(call,grant)
            await self.connection.execute("INSERT INTO send_calls(request_id,tenant_id,subject_id,session_id,account_id,budget_month,provider,model,model_id,capability,endpoint,wire_sha256,wire_bytes,output_budget,retrieval_kbs,retrieval_documents,origin_kbs,permission_source,price_source,estimator_source,reserved_microunits) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",(request,tenant,owner,call.context.session_id,authority.account_id,month,call.provider,call.model,call.model_id,call.capability,call.endpoint,call.wire_sha256,call.wire_bytes,call.output_budget,_json(call.context.scope.knowledge_base_ids),_json(call.context.scope.document_ids),_json(origin_kbs),grant.approval_source,estimate.price_source,estimate.estimator_source,estimate.microunits))

    def _same(self,call,row):
        expected=(call.provider,call.model,call.model_id,call.capability,call.endpoint,call.wire_sha256,call.wire_bytes,call.output_budget,call.context.session_id)
        values=tuple(row[k] for k in ('provider','model','model_id','capability','endpoint','wire_sha256','wire_bytes','output_budget','session_id'))
        if expected!=values or tuple(_object(row['retrieval_kbs']))!=call.context.scope.knowledge_base_ids or tuple(_object(row['retrieval_documents']))!=call.context.scope.document_ids:
            raise ProviderError("MODEL_FINISH_IDENTITY_MISMATCH",sent=True)

    async def finish(self,call,*,outcome,usage):
        # Cancellation must not prevent preserving occupancy after possible send.
        self.guard.require_verified()
        request,tenant,owner=call.context.request_id,self.tenant_id,call.context.scope.owner_id
        row=await self._one("SELECT * FROM send_calls WHERE request_id=%s AND tenant_id=%s AND subject_id=%s",(request,tenant,owner))
        if row is None: raise ProviderError("MODEL_SEND_RESERVATION_MISSING")
        self._same(call,row)
        if outcome not in ('NOT_SENT','UNKNOWN','RESPONSE_RECEIVED') or not isinstance(usage,TokenUsage): raise ProviderError("MODEL_FINISH_EVIDENCE_INVALID",sent=True)
        if row['state'] in ('settled','released'):
            if row['outcome']!=outcome or _object(row['usage'])!=asdict(usage):raise ProviderError("MODEL_FINISH_TERMINAL_CONFLICT",sent=True)
            return  # avoids even repeating authoritative cost calculation
        cost=None
        if outcome=='RESPONSE_RECEIVED' and usage.status=='REPORTED' and all(type(v) is int and v>=0 for v in (usage.prompt_tokens,usage.completion_tokens,usage.total_tokens)) and self.costs:
            try: cost=await self.costs.actual(call,usage,row['price_source'])
            except Exception: cost=None
        overrun=False
        async with self.connection.transaction():
            await self._month_lock(row['account_id'],row['budget_month'])
            current=await self._one("SELECT * FROM send_calls WHERE request_id=%s AND tenant_id=%s AND subject_id=%s FOR UPDATE",(request,tenant,owner));self._same(call,current)
            if current['state'] in ('settled','released'):
                # Immutable terminal ledger; a duplicate cannot reserve/settle again.
                if current['outcome']!=outcome or _object(current['usage'])!=asdict(usage): raise ProviderError("MODEL_FINISH_TERMINAL_CONFLICT",sent=True)
                return
            state,actual='unknown',None
            if outcome=='NOT_SENT' and current['state']=='reserved': state='released'
            elif outcome=='NOT_SENT': raise ProviderError("MODEL_UNKNOWN_CANNOT_RELEASE",sent=True)
            elif isinstance(cost,ActualCost) and type(cost.microunits) is int and cost.microunits>=0 and cost.price_source==row['price_source'] and (cost.microunits>0 or cost.known_zero_price):
                if cost.microunits<=current['reserved_microunits']: state,actual='settled',cost.microunits
                else:
                    overrun=True
                    await self.connection.execute("UPDATE send_budget_months SET blocked_reason='ACTUAL_COST_EXCEEDS_RESERVATION' WHERE account_id=%s AND month=%s",(row['account_id'],row['budget_month']))
            await self.connection.execute("UPDATE send_calls SET state=%s,actual_microunits=%s,outcome=%s,usage=%s::jsonb,finished_at=now() WHERE request_id=%s AND tenant_id=%s AND subject_id=%s",(state,actual,outcome,_json(asdict(usage)),request,tenant,owner))
        if overrun: raise ProviderError("ACTUAL_COST_EXCEEDS_RESERVATION",sent=True)
