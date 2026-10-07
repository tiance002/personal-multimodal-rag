"""Explicitly authorized, capped DeepSeek text answer requests; no implicit retry."""
from __future__ import annotations

import json
import hashlib
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from backend.app.adapters.models.usage import call_stage, record_call
from backend.app.ports.deepseek_gate_types import DeepSeekGateTypes
from backend.app.ports.session_attempts import AttemptDenied, AttemptGate
from backend.app.ports.providers import ProviderUnavailable, ProviderRequestNotSent, TruncatedAnswer

ENDPOINT = 'https://api.deepseek.com/chat/completions'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeekGateway:
    provider_kind = 'cloud'
    provider_name = 'deepseek'

    def __init__(self, *, api_key: str, model: str = 'deepseek-flash',
                 cloud_enabled: bool = False, attempt_gate: AttemptGate, receipt_sink=None,
                 gate_types: DeepSeekGateTypes | None = None):
        self._api_key = api_key
        self.chat_model = model
        self.cloud_enabled = cloud_enabled
        self.attempt_gate = attempt_gate
        self.receipt_sink = receipt_sink
        self.gate_types = gate_types

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
                           _product_context=None) -> str:
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
        request = Request(ENDPOINT, method='POST', headers={
            'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json'},
            data=json.dumps({'model': self.chat_model, 'messages': [{'role': 'user', 'content': prompt}],
                'thinking': {'type': 'disabled'}, 'max_tokens': max_tokens,
                'temperature': 0, 'stream': False}).encode('utf-8'))
        if product:
            try:
                self.receipt_sink.prepare(gate=self.attempt_gate, request_id=request_id,
                    wire_request_payload=request.data)
            except Exception:
                raise ProviderRequestNotSent('PRODUCT_RECEIPT_PATH_UNAVAILABLE') from None
        try:
            attempt = self.attempt_gate.reserve(request_id=request_id)
        except AttemptDenied as exc:
            raise ProviderRequestNotSent(str(exc)) from None
        started = time.perf_counter()
        status, response, finish = 'error', None, 'unknown'
        content, reason = None, None
        try:
            opener = build_opener(_NoRedirect(), ProxyHandler({}))
            before_send = getattr(self.attempt_gate, 'validate_before_send', None)
            if before_send is not None:
                try:
                    before_send(attempt)
                except AttemptDenied as exc:
                    raise ProviderRequestNotSent(str(exc)) from None
            with opener.open(request, timeout=timeout_seconds) as result:
                raw = result.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError()
            response = json.loads(raw)
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
        except Exception:
            raise ProviderUnavailable('DEEPSEEK_REQUEST_FAILED') from None
        finally:
            with call_stage('answer'):
                usage = response.get('usage', {}) if isinstance(response, dict) else {}
                if not isinstance(usage, dict):
                    usage = {}
                if finish != 'not_sent':
                    record_call(model=self.chat_model, status=status,
                                response={'prompt_eval_count': usage.get('prompt_tokens'),
                                          'eval_count': usage.get('completion_tokens'),
                                          'done_reason': 'stop' if finish == 'ok' else finish},
                                latency_ms=(time.perf_counter() - started) * 1000)
            self.last_receipt = {
                'request_id': request_id, 'prompt_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'wire_request_sha256': hashlib.sha256(request.data).hexdigest(),
                'raw_answer': content, 'finish_reason': reason or 'UNKNOWN', 'provider_usage': usage,
                'response_actual_model': (response.get('model') or 'UNKNOWN') if isinstance(response, dict) else 'UNKNOWN',
                'wall_ms': (time.perf_counter() - started) * 1000, 'ttft_ms': None,
                'attempt_status': finish, 'retry_count': 0}
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
            settlement_status = 'unknown' if finish == 'not_sent' else finish
            try:
                finish_usage = getattr(self.attempt_gate, 'finish_with_usage', None)
                if finish_usage is not None:
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
