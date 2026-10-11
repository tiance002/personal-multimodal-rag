"""Explicitly authorized, capped DeepSeek text answer requests; no implicit retry."""
from __future__ import annotations

import json
import hashlib
import time
import threading
from weakref import WeakValueDictionary
from dataclasses import dataclass, field
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from backend.app.adapters.models.usage import call_stage, record_call
from backend.app.ports.deepseek_gate_types import DeepSeekGateTypes
from backend.app.ports.session_attempts import AttemptDenied, AttemptGate
from backend.app.ports.providers import ProviderUnavailable, ProviderRequestNotSent, TruncatedAnswer
from backend.app.ports.provider_send_claims import ProviderSendClaims, claim_matches

ENDPOINT = 'https://api.deepseek.com/chat/completions'


@dataclass(frozen=True)
class SimulatedDeepSeekTransport:
    """Explicit offline fixture transport; never selected by product startup."""
    send: object = field(repr=False)
    execution_kind = 'SIMULATED'


@dataclass(frozen=True)
class V6PreparedRequest:
    wire_bytes: bytes = field(repr=False)
    budget: object = field(repr=False)
    grant: object = field(repr=False)
    gateway: object = field(repr=False)
    capacity_estimate: object | None = field(default=None,repr=False)


def _wire_payload(model, prompt, max_tokens):
    # The capacity counter consumes this exact adapter protocol/field sequence.
    body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
            'thinking': {'type': 'disabled'}, 'max_tokens': max_tokens,
            'temperature': 0, 'stream': False}
    return json.dumps(body).encode('utf-8')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeekGateway:
    provider_kind = 'cloud'
    provider_name = 'deepseek'

    def __init__(self, *, api_key: str, model: str = 'deepseek-flash',
                 cloud_enabled: bool = False, attempt_gate: AttemptGate, receipt_sink=None,
                 gate_types: DeepSeekGateTypes | None = None, transport=None, send_claim_callback=None):
        self._api_key = api_key
        self.chat_model = model
        self.cloud_enabled = cloud_enabled
        self.attempt_gate = attempt_gate
        self.receipt_sink = receipt_sink
        self.gate_types = gate_types
        if transport is not None and type(transport) is not SimulatedDeepSeekTransport:
            raise ValueError('DEEPSEEK_TRANSPORT_INJECTION_UNSUPPORTED')
        self._simulated_transport = transport
        self.execution_kind = 'SIMULATED' if transport is not None else 'REAL'
        if send_claim_callback is not None and (not isinstance(send_claim_callback, ProviderSendClaims)
                or type(gate_types) is not DeepSeekGateTypes
                or type(attempt_gate) is not gate_types.v6_owner_activity):
            raise ValueError('V6_SEND_CLAIM_CALLBACK_INVALID')
        self.send_claim_callback = send_claim_callback
        self._issued_v6 = WeakValueDictionary()
        self._v6_lock = threading.Lock()

    @property
    def transport(self):
        """This gateway owns the final text-send boundary and its injected port."""
        return self

    def prepare_v6_request(self, prompt, timeout_seconds, max_tokens, *, grant, scope, capacity_counter):
        """Preflight for explicit evaluation callers, before either reservation."""
        if (type(self.gate_types) is not DeepSeekGateTypes
                or type(self.attempt_gate) is not self.gate_types.v6_owner_activity
                or grant is not self.attempt_gate.grant or scope != grant.scope
                or self.chat_model != grant.model):
            raise ProviderRequestNotSent('V6_GENERATION_BINDING_INVALID')
        simulated = grant.execution_kind == 'SIMULATED'
        if simulated != (type(self._simulated_transport) is SimulatedDeepSeekTransport):
            raise ProviderRequestNotSent('V6_EXECUTION_KIND_MISMATCH')
        from backend.app.adapters.models.v6_text_capacity import (
            CapacityCounterUnavailable, DeepseekV41TextCapacityCounter,
            DeepseekV41RecipeRenderer, TextCapacityEstimate,
        )
        if simulated:
            if getattr(capacity_counter, 'execution_kind', None) != 'SIMULATED':
                raise ProviderRequestNotSent('V6_SIMULATED_COUNTER_REQUIRED')
        elif (type(capacity_counter) is not DeepseekV41TextCapacityCounter
              or type(capacity_counter.renderer) is not DeepseekV41RecipeRenderer):
            raise ProviderRequestNotSent('V6_PRODUCTION_COUNTER_UNVERIFIED')
        try:
            self.attempt_gate.validate_request(prompt, timeout_seconds, max_tokens, model=self.chat_model)
            wire_bytes = _wire_payload(self.chat_model, prompt, max_tokens)
            estimate = capacity_counter.measure(self.provider_name, self.chat_model,
                                                json.loads(wire_bytes), scope=scope)
            if type(estimate) is not TextCapacityEstimate:
                raise CapacityCounterUnavailable('V6_CAPACITY_ESTIMATE_INVALID')
            if not simulated and estimate.production_ready is not True:
                raise CapacityCounterUnavailable('V6_PRODUCTION_COUNTER_UNVERIFIED')
            if not simulated or estimate.capacity_profile is not None:
                capacity_counter.validate_estimate(estimate,grant=grant,scope=scope,wire_bytes=wire_bytes)
            budget = estimate.to_token_request_budget(grant=grant)
            if (estimate.prompt_text != prompt or budget.prompt_text != prompt
                    or budget.wire_request_sha256 != hashlib.sha256(wire_bytes).hexdigest()):
                raise CapacityCounterUnavailable('V6_WIRE_REQUEST_BINDING_MISMATCH')
            self.attempt_gate._validate_budget(budget)
            if not simulated and not isinstance(self.send_claim_callback, ProviderSendClaims):
                raise ProviderRequestNotSent('V6_DURABLE_CLAIM_REQUIRED')
        except (AttemptDenied, CapacityCounterUnavailable) as exc:
            raise ProviderRequestNotSent(str(exc)) from None
        prepared = V6PreparedRequest(wire_bytes, budget, grant, self,estimate)
        with self._v6_lock:
            self._issued_v6[id(prepared)] = prepared
        return prepared

    def answer_with_v6_scope(self, prompt, timeout_seconds, max_tokens, *, prepared_request,
                              scope, request_id, cloud_authorized=False, token_budget_request=None):
        if (type(prepared_request) is not V6PreparedRequest
                or prepared_request.gateway is not self
                or scope != prepared_request.grant.scope):
            raise ProviderRequestNotSent('V6_GENERATION_BINDING_INVALID')
        return self.answer_with_budget(prompt, timeout_seconds, max_tokens,
            cloud_authorized=cloud_authorized, request_id=request_id, _v6_request=prepared_request,
            _token_budget_request=token_budget_request)

    def answer(self, prompt: str, timeout_seconds: float) -> str:
        # Generic local-provider interface cannot authorize external egress.
        raise ProviderRequestNotSent('CLOUD_EGRESS_DISABLED')

    def answer_with_product_scope(self, prompt, timeout_seconds, max_tokens, *,
                                  run_id, scope, question, cloud_authorized=False, request_id=None):
        return self.answer_with_budget(prompt, timeout_seconds, max_tokens,
            cloud_authorized=cloud_authorized, request_id=request_id,
            _product_context=(run_id, scope, question))

    def answer_with_budget(self, prompt: str, timeout_seconds: float, max_tokens: int,
                           *, cloud_authorized: bool = False, request_id: str | None = None,
                           _product_context=None, _v6_request=None, _token_budget_request=None) -> str:
        if not self.cloud_enabled or not cloud_authorized:
            raise ProviderRequestNotSent('CLOUD_EGRESS_DISABLED')
        if not self._api_key.strip():
            raise ProviderRequestNotSent('DEEPSEEK_CREDENTIAL_MISSING')
        # Closed server-side gate types: wrappers/subclasses cannot drop the
        # product context and durable receipt checks through legacy duck typing.
        if type(self.gate_types) is not DeepSeekGateTypes:
            raise ProviderRequestNotSent('DEEPSEEK_GATE_POLICY_MISSING')
        if type(self.attempt_gate) not in self.gate_types.supported:
            raise ProviderRequestNotSent('DEEPSEEK_GATE_TYPE_UNSUPPORTED')
        product = type(self.attempt_gate) is self.gate_types.product
        v6 = type(self.attempt_gate) is self.gate_types.v6_owner_activity
        if self._simulated_transport is not None and not v6:
            raise ProviderRequestNotSent('V6_EXECUTION_KIND_MISMATCH')
        if v6 and (type(_v6_request) is not V6PreparedRequest
                   or _v6_request.gateway is not self
                   or _v6_request.grant is not self.attempt_gate.grant):
            raise ProviderRequestNotSent('V6_REQUEST_BUDGET_REQUIRED')
        if product and type(self.receipt_sink) is not self.gate_types.product_receipt:
            raise ProviderRequestNotSent('PRODUCT_DURABLE_RECEIPT_REQUIRED')
        pair = type(self.attempt_gate) is self.gate_types.static_pair
        if (max_tokens not in (512, 896, 1280) and not (pair and max_tokens == 256)) or not 0 < timeout_seconds <= 120:
            raise ProviderRequestNotSent('DEEPSEEK_REQUEST_LIMIT_INVALID')
        if self.chat_model not in ('deepseek-flash', 'deepseek-v4-pro'):
            raise ProviderRequestNotSent('DEEPSEEK_MODEL_UNSUPPORTED')
        if product:
            try:
                if not isinstance(_product_context, tuple) or len(_product_context) != 3:
                    raise AttemptDenied('PRODUCT_REQUEST_SCOPE_REQUIRED')
                run_id, scope, question = _product_context
                self.attempt_gate.validate_product_scope(run_id=run_id, scope=scope, question=question)
            except AttemptDenied as exc:
                raise ProviderRequestNotSent(str(exc)) from None
        validator = getattr(self.attempt_gate, 'validate_request', None)
        if validator is not None:
            try:
                validator(prompt, timeout_seconds, max_tokens, model=self.chat_model)
            except AttemptDenied as exc:
                raise ProviderRequestNotSent(str(exc)) from None
        # Request construction precedes quota reservation; no secret appears in errors.
        wire_bytes = _wire_payload(self.chat_model, prompt, max_tokens)
        if v6 and (_v6_request.wire_bytes != wire_bytes
                   or _v6_request.budget.prompt_text != prompt
                   or _v6_request.budget.wire_request_sha256 != hashlib.sha256(wire_bytes).hexdigest()):
            raise ProviderRequestNotSent('V6_WIRE_REQUEST_BINDING_MISMATCH')
        request = Request(ENDPOINT, method='POST', headers={
            'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json'},
            data=wire_bytes)
        if product:
            try:
                self.receipt_sink.prepare(gate=self.attempt_gate, request_id=request_id,
                    wire_request_payload=request.data)
            except Exception:
                raise ProviderRequestNotSent('PRODUCT_RECEIPT_PATH_UNAVAILABLE') from None
        if v6:
            self._validate_v6_profile(_v6_request,request.data)
            from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT_R3
            if self.attempt_gate.grant is OWNER_V6_TOKEN_GRANT_R3 and _token_budget_request is None:
                raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_BINDING_REQUIRED')
            if _token_budget_request is not None:
                from backend.app.ports.v6_token_budget import V6TokenBudgetRequest
                from backend.app.application.provider_usage import CanonicalTokenUsageGuard
                if (type(_token_budget_request) is not V6TokenBudgetRequest
                        or type(_token_budget_request.usage_guard) is not CanonicalTokenUsageGuard
                        or _token_budget_request.usage_guard.gateway is not self):
                    raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_BINDING_REQUIRED')
                _token_budget_request.usage_guard.validate_request(_token_budget_request,
                    prepared=_v6_request, request_id=request_id)
            with self._v6_lock:
                if self._issued_v6.pop(id(_v6_request), None) is not _v6_request:
                    raise ProviderRequestNotSent('V6_PREPARED_REQUEST_NOT_ISSUED')
        try:
            attempt = (self.attempt_gate.reserve(request_id=request_id, budget=_v6_request.budget)
                       if v6 else self.attempt_gate.reserve(request_id=request_id))
        except AttemptDenied as exc:
            raise ProviderRequestNotSent(str(exc)) from None
        started = time.perf_counter()
        status, response, finish = 'error', None, 'unknown'
        content, reason = None, None
        provider_claim, dispatch_contract = None, None
        try:
            if _token_budget_request is not None:
                _token_budget_request.usage_guard.link_reserved(_token_budget_request, attempt_id=attempt)
            if v6 and self.send_claim_callback is not None:
                budget, grant = _v6_request.budget, _v6_request.grant
                dispatch_contract = dict(request_id=request_id, purpose='answer', provider=self.provider_name,
                    model=self.chat_model, scope=grant.scope, activity_id=budget.activity_id,
                    execution_kind=self.execution_kind, deadline_utc=grant.deadline_utc,
                    wire_request_sha256=hashlib.sha256(request.data).hexdigest(),
                    input_token_bound=budget.input_token_bound, output_token_bound=budget.output_token_bound,
                    safety_tokens=budget.safety_tokens, window_tokens=budget.window_tokens,
                    tokenizer_source=budget.tokenizer_source, capacity_source=budget.capacity_source,
                    timeout_seconds=timeout_seconds)
                profile=getattr(_v6_request.capacity_estimate,'capacity_profile',None)
                if profile is not None:dispatch_contract['capacity_profile_sha256']=profile.manifest_sha256
                identity = hashlib.sha256(json.dumps(dispatch_contract, sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
                try:
                    claim = self.send_claim_callback(request_id=request_id, purpose='answer',
                        provider=self.provider_name, model=self.chat_model, role='chat_expensive',
                        request_identity_sha256=identity, call_ordinal=1)
                    if not claim_matches(claim, request_id=request_id, purpose='answer',
                            provider=self.provider_name, model=self.chat_model,
                            role='chat_expensive', identity_sha256=identity):
                        raise ValueError('V6_DURABLE_CLAIM_INVALID')
                    provider_claim = claim
                except Exception:
                    raise ProviderRequestNotSent('V6_DURABLE_CLAIM_DENIED') from None
            opener = None
            if self._simulated_transport is None:
                opener = build_opener(_NoRedirect(), ProxyHandler({}))
            # PG may block on its connection/transaction/row lock. All native
            # window and cancellation checks must follow that wait.
            if _token_budget_request is not None:
                _token_budget_request.usage_guard.check_before_send(_token_budget_request)
            before_send = getattr(self.attempt_gate, 'validate_before_send', None)
            if before_send is not None:
                try:
                    before_send(attempt)
                except AttemptDenied as exc:
                    # A SIMULATED grant can only reach the explicit fixture
                    # transport; the normal urllib boundary stays closed.
                    if not (v6 and type(self._simulated_transport) is SimulatedDeepSeekTransport
                            and self.attempt_gate.grant.execution_kind == 'SIMULATED'
                            and str(exc) == 'V6_SIMULATED_SEND_DISABLED'):
                        raise ProviderRequestNotSent(str(exc)) from None
            self._check_send_claim(provider_claim)
            if v6:self._validate_v6_profile(_v6_request,request.data,timeout_seconds=timeout_seconds)
            if _token_budget_request is not None:
                _token_budget_request.usage_guard.check_current_execution(_token_budget_request)
            if self._simulated_transport is not None:
                response = self._simulated_transport.send(request, timeout_seconds)
            else:
                with opener.open(request, timeout=timeout_seconds) as result:
                    raw = result.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError()
                response = json.loads(raw)
            if v6 and response.get('model') != self.chat_model:
                raise ProviderUnavailable('ROUTER_MODEL_RESPONSE_MISMATCH')
            choice = response['choices'][0]
            content, reason = choice['message']['content'], choice['finish_reason']
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            if reason == 'length':
                finish = 'truncated'
                raise TruncatedAnswer(content.strip())
            if reason != 'stop':
                raise ValueError()
            finish, status = 'ok', 'ok'
            return content.strip()
        except ProviderRequestNotSent:
            finish = 'not_sent'
            raise
        except TruncatedAnswer:
            raise
        except ProviderUnavailable as exc:
            if v6 and str(exc) == 'ROUTER_MODEL_RESPONSE_MISMATCH':
                raise
            raise ProviderUnavailable('DEEPSEEK_REQUEST_FAILED') from None
        except Exception:
            raise ProviderUnavailable('DEEPSEEK_REQUEST_FAILED') from None
        finally:
            telemetry_failed = False
            with call_stage('answer'):
                usage = response.get('usage', {}) if isinstance(response, dict) else {}
                if not isinstance(usage, dict):
                    usage = {}
                if finish != 'not_sent':
                    try:
                        record_call(model=self.chat_model, status=status,
                                    response={'prompt_eval_count': usage.get('prompt_tokens'),
                                              'eval_count': usage.get('completion_tokens'),
                                              'done_reason': 'stop' if finish == 'ok' else finish},
                                    latency_ms=(time.perf_counter() - started) * 1000)
                    except Exception:
                        telemetry_failed = True
                        finish, usage = 'unknown', {}
            self.last_receipt = {
                'request_id': request_id, 'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'wire_request_sha256': hashlib.sha256(request.data).hexdigest(),
                'raw_answer': content, 'finish_reason': reason or 'UNKNOWN', 'provider_usage': usage,
                'response_actual_model': (response.get('model') or 'UNKNOWN') if isinstance(response, dict) else 'UNKNOWN',
                'wall_ms': (time.perf_counter() - started) * 1000, 'ttft_ms': None,
                'attempt_status': finish, 'retry_count': 0,
                'execution_kind': self.execution_kind,
                'send_status': 'NOT_SENT' if finish == 'not_sent' else 'RESPONSE_RECEIVED' if response is not None else 'UNKNOWN'}
            if _token_budget_request is not None:
                self.last_receipt.update(native_token_attempt_id=attempt,
                    product_attempt_id=_token_budget_request.product_attempt_id,
                    token_budget_unit='input+safety+output_tokens')
            if v6 and getattr(_v6_request.capacity_estimate,'capacity_profile',None) is not None:
                self.last_receipt.update(capacity_profile_sha256=_v6_request.capacity_estimate.capacity_profile.manifest_sha256,
                    capacity_readiness_basis='OWNER_SCOPED_CONDITIONAL',hosted_render_equivalence='UNKNOWN')
            receipt_failed = False
            if product:
                self.last_receipt.update(attempt_id=attempt,
                    product_scope_id=self.attempt_gate.product_spec.scope_id,
                    product_spec_sha256=self.receipt_sink.spec_sha256)
                try:
                    self.receipt_sink.persist(gate=self.attempt_gate, attempt_id=attempt,
                                              receipt=dict(self.last_receipt))
                except Exception:
                    # A missing durable acknowledgement is unknown even when the
                    # provider succeeded. Retain the full reserve, never refund.
                    receipt_failed = True
                    if finish != 'not_sent':
                        finish, usage = 'unknown', {}
                        self.last_receipt['attempt_status'] = 'unknown'
            # Existing ledger statuses retain the full bound for a consumed,
            # unsent attempt; the receipt and exception preserve send certainty.
            settlement_status = 'not_sent' if v6 and finish == 'not_sent' else 'unknown' if finish == 'not_sent' else finish
            try:
                finish_usage = getattr(self.attempt_gate, 'finish_with_usage', None)
                if v6 and finish == 'not_sent':
                    self.attempt_gate.finish(attempt, 'not_sent')
                elif finish_usage is not None:
                    finish_usage(attempt, settlement_status, usage)
                else:
                    self.attempt_gate.finish(attempt, settlement_status)
            except AttemptDenied:
                # Already consumed and still reserved on disk; never refund or retry.
                if finish != 'not_sent':
                    raise ProviderUnavailable('SESSION_LEDGER_SETTLEMENT_FAILED') from None
            finally:
                if (pair or type(self.attempt_gate) is self.gate_types.immutable_batch) and self.receipt_sink is not None:
                    self.receipt_sink(dict(self.last_receipt))
            if receipt_failed and finish != 'not_sent':
                raise ProviderUnavailable('PRODUCT_RECEIPT_PERSISTENCE_FAILED') from None
            if provider_claim is not None:
                try:
                    self.send_claim_callback.settle(request_id=request_id,
                        status=('blocked' if finish == 'not_sent' else
                                'simulated' if finish == 'ok' and self.execution_kind == 'SIMULATED' else
                                'succeeded' if finish == 'ok' else 'unknown'),
                        result={'provider_dispatch': dict(self.last_receipt), 'dispatch_contract': dispatch_contract})
                except Exception:
                    self.last_receipt.update(dispatch_persistence='UNKNOWN')
                    raise ProviderUnavailable('V6_DURABLE_FINALIZATION_FAILED') from None
            if telemetry_failed:
                raise ProviderUnavailable('MODEL_USAGE_CAPTURE_FAILED') from None

    def _validate_v6_profile(self,prepared,wire_bytes,*,timeout_seconds=None):
        estimate=prepared.capacity_estimate
        if getattr(estimate,'capacity_profile',None) is not None:
            from backend.app.adapters.models.v6_text_capacity import CapacityCounterUnavailable,DeepseekV41TextCapacityCounter
            try:
                if type(estimate.counter) is not DeepseekV41TextCapacityCounter:raise ValueError()
                estimate.counter.validate_estimate(estimate,grant=prepared.grant,scope=prepared.grant.scope,wire_bytes=wire_bytes)
            except (CapacityCounterUnavailable,ValueError,AttributeError):
                raise ProviderRequestNotSent('V6_CAPACITY_PROFILE_CHANGED') from None
        elif prepared.grant.execution_kind=='REAL':
            raise ProviderRequestNotSent('V6_PRODUCTION_COUNTER_UNVERIFIED')
        if (prepared.grant.version==3 and getattr(estimate,'capacity_profile',None) is not None
                and estimate.capacity_profile.execution_lease is not self.attempt_gate.execution_lease):
            raise ProviderRequestNotSent('V6_EXECUTION_LEASE_PROFILE_MISMATCH')
        if timeout_seconds is not None:
            try:
                self.attempt_gate.validate_request(prepared.budget.prompt_text,timeout_seconds,
                    prepared.grant.output_token_cap,model=prepared.grant.model)
            except AttemptDenied as exc:
                raise ProviderRequestNotSent(str(exc)) from None

    def _check_send_claim(self, claim):
        if claim is not None:
            try:
                self.send_claim_callback.check_before_send(request_id=claim.request_id)
            except Exception:
                raise ProviderRequestNotSent('V6_DURABLE_SEND_CHECK_DENIED') from None
