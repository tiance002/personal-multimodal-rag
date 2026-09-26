from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from sqlalchemy import Engine, text


class BudgetDenied(RuntimeError):
    """Raised when a new external call would exceed the monthly budget."""


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    month: date
    reserved_microunits: int


class BudgetGate(Protocol):
    def reserve(
        self,
        *,
        run_id: str | None,
        provider: str,
        model_name: str,
        capability: str,
        estimate_microunits: int,
        month: date | None = None,
    ) -> BudgetReservation: ...

    def settle(self, reservation_id: str, actual_microunits: int) -> None: ...

    def release(self, reservation_id: str) -> None: ...

    def mark_unknown(self, reservation_id: str) -> None: ...


def _month(value: date | None) -> date:
    return value or datetime.now(timezone.utc).date().replace(day=1)


def _validate_estimate(value: int) -> int:
    if value <= 0:
        raise ValueError("budget estimate must be positive")
    return int(value)


class InMemoryBudgetGate:
    """Deterministic gate used by unit tests and provider-boundary tests."""

    def __init__(self, monthly_budget_microunits: int) -> None:
        if monthly_budget_microunits < 0:
            raise ValueError("monthly budget must be non-negative")
        self.monthly_budget_microunits = monthly_budget_microunits
        self.reservations: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()

    def _used(self, month: date) -> int:
        total = 0
        for item in self.reservations.values():
            if item["month"] != month:
                continue
            if item["state"] in {"reserved", "unknown"}:
                total += int(item["reserved_microunits"])
            elif item["state"] == "settled":
                total += int(item.get("settled_microunits", 0))
        return total

    def reserve(
        self,
        *,
        run_id: str | None,
        provider: str,
        model_name: str,
        capability: str,
        estimate_microunits: int,
        month: date | None = None,
    ) -> BudgetReservation:
        estimate = _validate_estimate(estimate_microunits)
        budget_month = _month(month)
        with self._lock:
            if self._used(budget_month) + estimate > self.monthly_budget_microunits:
                raise BudgetDenied("MONTHLY_BUDGET_EXCEEDED")
            reservation_id = str(uuid.uuid4())
            self.reservations[reservation_id] = {
                "month": budget_month,
                "reserved_microunits": estimate,
                "settled_microunits": 0,
                "state": "reserved",
                "run_id": run_id,
                "provider": provider,
                "model_name": model_name,
                "capability": capability,
            }
            return BudgetReservation(reservation_id, budget_month, estimate)

    def settle(self, reservation_id: str, actual_microunits: int) -> None:
        if actual_microunits < 0:
            raise ValueError("actual cost must be non-negative")
        with self._lock:
            item = self.reservations[reservation_id]
            if item["state"] not in {"reserved", "unknown"}:
                return
            if actual_microunits > int(item["reserved_microunits"]):
                item["state"] = "unknown"
                raise BudgetDenied("ACTUAL_COST_EXCEEDS_RESERVATION")
            item["settled_microunits"] = actual_microunits
            item["state"] = "settled"

    def release(self, reservation_id: str) -> None:
        with self._lock:
            item = self.reservations.get(reservation_id)
            if item and item["state"] == "reserved":
                item["state"] = "released"

    def mark_unknown(self, reservation_id: str) -> None:
        with self._lock:
            item = self.reservations.get(reservation_id)
            if item and item["state"] == "reserved":
                item["state"] = "unknown"


class PostgresBudgetGate:
    """Atomic monthly reservation ledger backed by model_calls."""

    def __init__(self, engine: Engine, monthly_budget_microunits: int) -> None:
        if monthly_budget_microunits < 0:
            raise ValueError("monthly budget must be non-negative")
        self.engine = engine
        self.monthly_budget_microunits = monthly_budget_microunits

    def reserve(
        self,
        *,
        run_id: str | None,
        provider: str,
        model_name: str,
        capability: str,
        estimate_microunits: int,
        month: date | None = None,
    ) -> BudgetReservation:
        estimate = _validate_estimate(estimate_microunits)
        budget_month = _month(month)
        reservation_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"personal-rag-budget:{budget_month.isoformat()}"},
            )
            used = connection.execute(
                text(
                    """SELECT COALESCE(SUM(CASE
                        WHEN reservation_state IN ('reserved','unknown') THEN reserved_cost_microunits
                        WHEN reservation_state = 'settled' THEN COALESCE(settled_cost_microunits, 0)
                        ELSE 0 END), 0)
                    FROM model_calls WHERE budget_month=:month"""
                ),
                {"month": budget_month},
            ).scalar_one()
            if int(used) + estimate > self.monthly_budget_microunits:
                raise BudgetDenied("MONTHLY_BUDGET_EXCEEDED")
            connection.execute(
                text(
                    """INSERT INTO model_calls
                    (id,run_id,provider,model_name,purpose,status,latency_ms,cost_microunits,
                     budget_month,reserved_cost_microunits,settled_cost_microunits,reservation_state)
                    VALUES (:id,:run_id,:provider,:model,:purpose,'created',0,0,
                            :month,:reserved,NULL,'reserved')"""
                ),
                {
                    "id": reservation_id,
                    "run_id": uuid.UUID(run_id) if run_id else None,
                    "provider": provider,
                    "model": model_name,
                    "purpose": capability,
                    "month": budget_month,
                    "reserved": estimate,
                },
            )
        return BudgetReservation(str(reservation_id), budget_month, estimate)

    def settle(self, reservation_id: str, actual_microunits: int) -> None:
        if actual_microunits < 0:
            raise ValueError("actual cost must be non-negative")
        reservation_uuid = uuid.UUID(reservation_id)
        with self.engine.begin() as connection:
            row = connection.execute(
                text("SELECT reserved_cost_microunits FROM model_calls WHERE id=:id FOR UPDATE"),
                {"id": reservation_uuid},
            ).mappings().first()
            if not row:
                return
            if actual_microunits > int(row["reserved_cost_microunits"]):
                connection.execute(
                    text("UPDATE model_calls SET status='error',reservation_state='unknown' WHERE id=:id"),
                    {"id": reservation_uuid},
                )
                raise BudgetDenied("ACTUAL_COST_EXCEEDS_RESERVATION")
            connection.execute(
                text("UPDATE model_calls SET status='ok',cost_microunits=:actual,settled_cost_microunits=:actual,reservation_state='settled' WHERE id=:id"),
                {"id": reservation_uuid, "actual": actual_microunits},
            )

    def release(self, reservation_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE model_calls SET status='released',reservation_state='released' WHERE id=:id AND reservation_state='reserved'"),
                {"id": uuid.UUID(reservation_id)},
            )

    def mark_unknown(self, reservation_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE model_calls SET status='error',reservation_state='unknown' WHERE id=:id AND reservation_state='reserved'"),
                {"id": uuid.UUID(reservation_id)},
            )
