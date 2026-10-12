"""One normal request reserves token quota and the existing monthly cost gate.

This is deliberately independent of evaluation permits. Token settlement does
not assert invoice settlement: sent requests retain UNKNOWN cost until verified.
"""
from dataclasses import dataclass

from backend.app.ports.model_access import scope_allows
from backend.app.ports.run_lifecycle import current_execution, current_attempt, execution_owner
from backend.app.ports.service_policy import ServiceModelPolicy, ServiceZeroReservation, scope_fingerprint, validate_tokens


@dataclass(frozen=True)
class ServiceReservation:
    token_id: str
    cost_id: str


class ServiceUsageGuard:
    def __init__(self, budget_gate, token_ledger, spec, policy):
        if type(policy) is not ServiceModelPolicy:
            raise ValueError('SERVICE_POLICY_REQUIRED')
        policy.check_model(spec)
        self.budget_gate, self.token_ledger = budget_gate, token_ledger
        self.spec, self.policy = spec, policy

    def reserve_prepared(self, *, request_id, scope, wire_sha256, input_tokens, output_tokens):
        validate_tokens(input_tokens, output_tokens)
        if not scope_allows(self.spec.role):
            raise ValueError('MODEL_ROLE_EGRESS_DENIED')
        scope_hash = scope_fingerprint(scope)
        context = current_execution.get()
        run_id = None
        if context is not None:
            claim, lifecycle, lost, _ = context
            if (lost.is_set() or not claim.created or claim.state != 'IN_PROGRESS'
                    or execution_owner.get() != (claim.run_id, claim.owner)):
                raise ValueError('RUN_LEASE_LOST')
            run_id = claim.run_id
        chat = self.spec.role in {'chat_cheap', 'chat_expensive'}
        if chat and (context is None or current_attempt.get() is None):
            raise ValueError('SERVICE_ATTEMPT_CONTEXT_REQUIRED')
        token_id = self.token_ledger.reserve(self.policy, request_id=request_id, role=self.spec.role,
            scope_sha256=scope_hash, wire_sha256=wire_sha256, input_tokens=input_tokens, output_tokens=output_tokens)
        cost_id = None
        try:
            authority = ServiceZeroReservation(self.policy, self.spec.model_key, self.spec.role, request_id,
                scope_hash, wire_sha256, input_tokens + output_tokens, run_id) if self.policy.zero_quote else None
            kwargs = {'zero_price_authority': authority} if authority is not None else {}
            cost_id = self.budget_gate.reserve(run_id=run_id, provider=self.spec.provider,
                model_name=self.spec.model_id, capability=self.spec.role,
                estimate_microunits=self.policy.cost_bound(input_tokens, output_tokens), **kwargs).reservation_id
            self.token_ledger.link_cost(token_id, cost_id)
            if chat:
                lifecycle.repository.link_budget(run_id, claim.owner, current_attempt.get(), cost_id)
            if context is not None and lost.is_set():
                raise ValueError('RUN_LEASE_LOST')
        except Exception:
            # Every failure here is pretransport. Cleanup failures remain occupied.
            if cost_id is not None:
                self.budget_gate.release(cost_id)
            self.token_ledger.finish(token_id, None, sent=False)
            raise
        return ServiceReservation(token_id, cost_id)

    def check_before_send(self, reservation):
        if type(reservation) is not ServiceReservation:
            raise ValueError('SERVICE_RESERVATION_REQUIRED')
        context = current_execution.get()
        if context is not None:
            claim, _, lost, _ = context
            if lost.is_set() or execution_owner.get() != (claim.run_id, claim.owner):
                raise ValueError('RUN_LEASE_LOST')
        self.token_ledger.check_before_send(self.policy, reservation.token_id)

    def settle(self, reservation, usage, *, sent):
        if type(reservation) is not ServiceReservation:
            raise ValueError('SERVICE_RESERVATION_REQUIRED')
        # Set conservative invoice state first so a token-ledger failure cannot
        # leave a possibly-sent call looking releasable.
        if sent:
            self.budget_gate.mark_unknown(reservation.cost_id)
        else:
            self.budget_gate.release(reservation.cost_id)
        return self.token_ledger.finish(reservation.token_id, usage, sent=sent)
