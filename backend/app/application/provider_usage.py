"""Reuse the existing budget ledger; token reports are not verified settlement."""
class BudgetUsageGuard:
    def __init__(self, budget_gate, spec, estimate_microunits):
        if type(estimate_microunits) is not int or estimate_microunits <= 0:
            raise ValueError("CONSERVATIVE_PROVIDER_RESERVATION_REQUIRED")
        self.budget_gate, self.spec = budget_gate, spec
        self.estimate_microunits = estimate_microunits

    def reserve(self, *, model_key, role, model_id, planned_tokens):
        if (model_key, role, model_id) != (self.spec.model_key, self.spec.role, self.spec.model_id):
            raise ValueError("BUDGET_MODEL_ROLE_MISMATCH")
        from backend.app.ports.run_lifecycle import current_execution, current_attempt
        context = current_execution.get()
        if context is not None and context[2].is_set():
            raise ValueError('RUN_LEASE_LOST')
        chat_attempt = role in {'chat_cheap','chat_expensive'}
        reservation = self.budget_gate.reserve(run_id=context[0].run_id if context else None, provider=self.spec.provider,
            model_name=model_id, capability=role,
            estimate_microunits=self.estimate_microunits).reservation_id
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
        # Preserve the full reservation even with known tokens: no billing
        # receipt or independently frozen price contract was supplied here.
        if sent:
            self.budget_gate.mark_unknown(reservation)
        else:
            self.budget_gate.release(reservation)
