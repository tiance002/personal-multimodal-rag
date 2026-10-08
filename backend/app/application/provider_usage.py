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
        return self.budget_gate.reserve(run_id=None, provider=self.spec.provider,
            model_name=model_id, capability=role,
            estimate_microunits=self.estimate_microunits).reservation_id

    def settle(self, reservation, usage, *, sent):
        # Preserve the full reservation even with known tokens: no billing
        # receipt or independently frozen price contract was supplied here.
        if sent:
            self.budget_gate.mark_unknown(reservation)
        else:
            self.budget_gate.release(reservation)
