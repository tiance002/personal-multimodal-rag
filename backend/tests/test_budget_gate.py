from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from backend.app.application.budget import BudgetDenied, InMemoryBudgetGate


def _reserve(gate: InMemoryBudgetGate, amount: int) -> str:
    try:
        return gate.reserve(
            run_id=None,
            provider="cloud",
            model_name="test",
            capability="chat",
            estimate_microunits=amount,
            month=date(2026, 9, 1),
        ).reservation_id
    except BudgetDenied:
        return "denied"


def test_reservations_count_until_release_or_settlement():
    gate = InMemoryBudgetGate(100)
    first = gate.reserve(run_id=None, provider="cloud", model_name="test", capability="chat", estimate_microunits=60, month=date(2026, 9, 1))
    with pytest.raises(BudgetDenied, match="MONTHLY_BUDGET_EXCEEDED"):
        gate.reserve(run_id=None, provider="cloud", model_name="test", capability="chat", estimate_microunits=41, month=date(2026, 9, 1))
    gate.release(first.reservation_id)
    second = gate.reserve(run_id=None, provider="cloud", model_name="test", capability="chat", estimate_microunits=80, month=date(2026, 9, 1))
    gate.settle(second.reservation_id, 50)
    gate.reserve(run_id=None, provider="cloud", model_name="test", capability="chat", estimate_microunits=50, month=date(2026, 9, 1))


def test_unknown_reservation_keeps_conservative_upper_bound():
    gate = InMemoryBudgetGate(100)
    reservation = gate.reserve(run_id=None, provider="cloud", model_name="test", capability="chat", estimate_microunits=80, month=date(2026, 9, 1))
    gate.mark_unknown(reservation.reservation_id)
    with pytest.raises(BudgetDenied):
        gate.reserve(run_id=None, provider="cloud", model_name="test", capability="chat", estimate_microunits=21, month=date(2026, 9, 1))


def test_reservation_is_atomic_under_concurrent_attempts():
    gate = InMemoryBudgetGate(100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _reserve(gate, 80), range(2)))
    assert results.count("denied") == 1
    assert sum(value != "denied" for value in results) == 1
