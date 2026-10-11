"""Reuse the existing budget ledger; token reports are not verified settlement."""
import hashlib
from threading import Lock


class CanonicalTokenUsageGuard:
    """Typed pre-admission + readback of the adapter's sole native reservation."""
    def __init__(self, binding, gateway):
        from backend.app.ports.v6_token_budget import V6CanonicalTokenBudgetBinding
        if type(binding) is not V6CanonicalTokenBudgetBinding:
            raise ValueError('V6_CANONICAL_TOKEN_BINDING_REQUIRED')
        binding.validate(gateway)
        self.binding, self.gateway = binding, gateway
        self._requests, self._lock = {}, Lock()

    def _context(self, *, run_id, attempt_id=None):
        from backend.app.application.run_lifecycle import RunLifecycle
        from backend.app.ports.run_lifecycle import current_attempt, RunClaim, execution_owner
        from backend.app.ports.providers import ProviderRequestNotSent
        context = RunLifecycle.assert_current()
        claim, lifecycle, lost, session = context
        attempt = current_attempt.get()
        if (type(claim) is not RunClaim or type(lifecycle) is not RunLifecycle
                or claim.run_id != run_id or claim.state != 'IN_PROGRESS' or not claim.created
                or execution_owner.get() != (claim.run_id, claim.owner)
                or type(attempt) is not str or not attempt or type(session) is not str or not session
                or attempt_id is not None and attempt != attempt_id):
            raise ProviderRequestNotSent('V6_TOKEN_PRODUCT_ATTEMPT_REQUIRED')
        if self.binding.grant.execution_kind == 'REAL':
            from backend.app.adapters.postgres.run_lifecycle import PostgresRunLifecycle
            from backend.app.adapters.redis_stream import RedisStreamManager
            if type(lifecycle.repository) is not PostgresRunLifecycle or type(lifecycle.streams) is not RedisStreamManager:
                raise ProviderRequestNotSent('V6_TOKEN_NORMAL_LIFECYCLE_REQUIRED')
        return context, attempt

    def admit(self, prepared, *, request_id, run_id, scope, envelope_sha256):
        from backend.app.adapters.models.deepseek import V6PreparedRequest
        from backend.app.ports.v6_token_budget import V6TokenBudgetRequest
        from backend.app.ports.providers import ProviderRequestNotSent
        self.binding.validate(self.gateway)
        context, attempt = self._context(run_id=run_id)
        if (type(prepared) is not V6PreparedRequest or prepared.gateway is not self.gateway
                or prepared.grant is not self.binding.grant
                or scope != self.binding.server_scope
                or type(request_id) is not str or not request_id
                or type(envelope_sha256) is not str or len(envelope_sha256) != 64):
            raise ProviderRequestNotSent('V6_TOKEN_PREPARED_BINDING_INVALID')
        self.binding.activity_gate._validate_budget(prepared.budget)
        permit = V6TokenBudgetRequest(self.binding, self, prepared, request_id, run_id,
            context[0].owner, context[3], attempt, envelope_sha256, scope)
        with self._lock:
            self._requests[id(permit)] = [permit, None]
        return permit

    def validate_request(self, permit, *, prepared, request_id):
        from backend.app.ports.v6_token_budget import V6TokenBudgetRequest
        from backend.app.ports.providers import ProviderRequestNotSent
        self.binding.validate(self.gateway)
        with self._lock:
            state = self._requests.get(id(permit))
            if (type(permit) is not V6TokenBudgetRequest or state is None or state[0] is not permit
                    or state[1] is not None or permit.usage_guard is not self
                    or permit.binding is not self.binding or permit.prepared_request is not prepared
                    or permit.request_id != request_id):
                raise ProviderRequestNotSent('V6_TOKEN_REQUEST_NOT_ISSUED')
        context, _ = self._context(run_id=permit.run_id, attempt_id=permit.product_attempt_id)
        if (context[0].owner, context[3]) != (permit.owner, permit.session_id):
            raise ProviderRequestNotSent('V6_TOKEN_CONTEXT_BINDING_INVALID')

    def _receipt(self, permit, attempt_id):
        from backend.app.ports.v6_token_budget import V6TokenBudgetReceipt
        row = self.binding.activity_gate.token_receipt(attempt_id,
            request_id=permit.request_id, budget=permit.prepared_request.budget)
        tokens, authority = row['validation_tokens'], self.binding.authority
        return V6TokenBudgetReceipt(permit.request_id, attempt_id, permit.product_attempt_id,
            permit.run_id, permit.owner, permit.session_id,
            tuple(sorted(permit.server_scope.knowledge_base_ids)), tuple(sorted(permit.server_scope.document_ids)),
            permit.envelope_sha256,
            self.binding.grant.grant_id, self.binding.grant.activity_id,
            authority.authority_id if authority is not None else None,
            authority.authority_epoch if authority is not None else None,
            self.binding.execution_lease.lease_id, hashlib.sha256(self.binding.ledger_identity.encode()).hexdigest(),
            permit.prepared_request.budget.wire_request_sha256, tokens['reserved_input'], tokens['reserved_output'],
            row['status'], tokens['state'])

    def link_reserved(self, permit, *, attempt_id):
        from backend.app.ports.providers import ProviderRequestNotSent
        with self._lock:
            state = self._requests.get(id(permit))
            if state is None or state[0] is not permit or state[1] is not None:
                raise ProviderRequestNotSent('V6_TOKEN_REQUEST_NOT_ISSUED')
            state[1] = attempt_id
        try:
            self.binding.validate(self.gateway)
            receipt = self._receipt(permit, attempt_id)
            if receipt.status != 'reserved' or receipt.token_state != 'reserved':
                raise ValueError('V6_TOKEN_RECEIPT_NOT_RESERVED')
            context, _ = self._context(run_id=permit.run_id, attempt_id=permit.product_attempt_id)
            context[1].repository.link_token_budget(receipt)
        except Exception:
            # Adapter owns the proven not-sent release. This guard never refunds.
            raise ProviderRequestNotSent('V6_TOKEN_ATTEMPT_LINK_DENIED') from None
        return receipt

    def verify_terminal(self, permit, *, not_sent):
        from backend.app.ports.providers import ProviderUnavailable
        with self._lock:
            state = self._requests.pop(id(permit), None)
        if state is None or state[0] is not permit:
            raise ProviderUnavailable('V6_TOKEN_RECEIPT_NOT_ISSUED')
        if state[1] is None:
            if not not_sent:
                raise ProviderUnavailable('V6_TOKEN_RESERVATION_MISSING')
            return None  # Admission rejected before the adapter reserved anything.
        receipt = self._receipt(permit, state[1])
        if (receipt.status == 'reserved' or receipt.token_state == 'reserved'
                or not_sent and (receipt.status != 'not_sent' or receipt.token_state != 'released')
                or not not_sent and receipt.token_state not in ('settled', 'unknown')):
            raise ProviderUnavailable('V6_TOKEN_TERMINAL_RECEIPT_INVALID')
        return receipt

    def check_before_send(self, permit):
        from backend.app.ports.providers import ProviderRequestNotSent
        try:
            self.binding.validate(self.gateway)
            with self._lock:
                state = self._requests.get(id(permit))
                if state is None or state[0] is not permit or state[1] is None:
                    raise ValueError('V6_TOKEN_RESERVATION_MISSING')
                attempt = state[1]
            context, _ = self._context(run_id=permit.run_id, attempt_id=permit.product_attempt_id)
            receipt = self._receipt(permit, attempt)
            if receipt.status != 'reserved' or receipt.token_state != 'reserved':
                raise ValueError('V6_TOKEN_RECEIPT_NOT_RESERVED')
            context[1].repository.assert_token_budget_current(receipt)
        except Exception:
            raise ProviderRequestNotSent('V6_TOKEN_PRODUCT_FENCE_DENIED') from None


    def check_current_execution(self, permit):
        """Final in-memory cancellation fence; no PG, ledger or profile I/O."""
        from backend.app.ports.providers import ProviderRequestNotSent
        try:
            with self._lock:
                state = self._requests.get(id(permit))
                if state is None or state[0] is not permit or state[1] is None:
                    raise ValueError('V6_TOKEN_RESERVATION_MISSING')
            context, _ = self._context(run_id=permit.run_id, attempt_id=permit.product_attempt_id)
            if (context[0].owner, context[3]) != (permit.owner, permit.session_id):
                raise ValueError('V6_TOKEN_CONTEXT_BINDING_INVALID')
        except Exception:
            raise ProviderRequestNotSent('V6_TOKEN_PRODUCT_FENCE_DENIED') from None


class BudgetUsageGuard:
    def __init__(self, budget_gate, spec, estimate_microunits, *, zero_price_permit=None):
        if type(estimate_microunits) is not int or estimate_microunits < 0:
            raise ValueError("CONSERVATIVE_PROVIDER_RESERVATION_REQUIRED")
        if estimate_microunits == 0:
            from backend.app.ports.xing_zero_reservation import validate_zero_permit
            validate_zero_permit(zero_price_permit, spec)
        elif zero_price_permit is not None:
            raise ValueError('XING_ZERO_CALIBRATION_ESTIMATE_MISMATCH')
        self.budget_gate, self.spec = budget_gate, spec
        self.estimate_microunits = estimate_microunits
        self.zero_price_permit = zero_price_permit

    def reserve(self, *, model_key, role, model_id, planned_tokens, zero_price_authority=None):
        if (model_key, role, model_id) != (self.spec.model_key, self.spec.role, self.spec.model_id):
            raise ValueError("BUDGET_MODEL_ROLE_MISMATCH")
        from backend.app.ports.run_lifecycle import current_execution, current_attempt
        context = current_execution.get()
        if context is not None and context[2].is_set():
            raise ValueError('RUN_LEASE_LOST')
        chat_attempt = role in {'chat_cheap','chat_expensive'}
        kwargs = {}
        if self.estimate_microunits == 0:
            from backend.app.ports.xing_zero_reservation import XingCalibrationZeroReservation, SiliconFlowZeroReservation
            if (type(zero_price_authority) not in (XingCalibrationZeroReservation,SiliconFlowZeroReservation)
                    or zero_price_authority.permit is not self.zero_price_permit
                    or type(planned_tokens) is not int or planned_tokens <= 0):
                raise ValueError('XING_ZERO_CALIBRATION_AUTHORITY_REQUIRED')
            zero_price_authority.validate(run_id=context[0].run_id if context else None,
                provider=self.spec.provider, model_name=model_id, capability=role, planned_tokens=planned_tokens)
            kwargs['zero_price_authority'] = zero_price_authority
        elif zero_price_authority is not None:
            raise ValueError('XING_ZERO_CALIBRATION_ESTIMATE_MISMATCH')
        reservation = self.budget_gate.reserve(run_id=context[0].run_id if context else None, provider=self.spec.provider,
            model_name=model_id, capability=role,
            estimate_microunits=self.estimate_microunits, **kwargs).reservation_id
        if context is not None and chat_attempt:
            if current_attempt.get() is None:
                self.budget_gate.release(reservation)
                raise ValueError('ATTEMPT_BUDGET_CONTEXT_REQUIRED')
            try:
                context[1].repository.link_budget(context[0].run_id,context[0].owner,current_attempt.get(),reservation)
            except Exception:
                # Reliable pre-transport failure; retain any release failure.
                self.budget_gate.release(reservation)
                raise
        return reservation

    def settle(self, reservation, usage, *, sent):
        # A planned zero quote is not a verified billing receipt. Sent calls,
        # including fixed zero-plan calibration, retain UNKNOWN / settled NULL.
        if sent:
            self.budget_gate.mark_unknown(reservation)
        else:
            self.budget_gate.release(reservation)
