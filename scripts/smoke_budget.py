from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.app.application.budget import BudgetDenied, PostgresBudgetGate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="postgresql+psycopg://rag:rag@127.0.0.1:55432/rag")
    parser.add_argument("--report", type=Path, default=Path("var/reports/smoke-budget.json"))
    args = parser.parse_args()
    engine = create_engine(args.database_url, pool_pre_ping=True)
    gate = PostgresBudgetGate(engine, monthly_budget_microunits=100)
    month = date(2099, 1, 1)
    reservation_ids: list[str] = []

    def reserve() -> dict[str, object]:
        try:
            reservation = gate.reserve(
                run_id=None,
                provider="budget-smoke",
                model_name="test",
                capability="chat",
                estimate_microunits=80,
                month=month,
            )
            reservation_ids.append(reservation.reservation_id)
            return {"status": "reserved", "id": reservation.reservation_id}
        except BudgetDenied as exc:
            return {"status": "denied", "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: reserve(), range(2)))
    succeeded = [item for item in outcomes if item["status"] == "reserved"]
    if len(succeeded) != 1:
        result = {"status": "FAIL", "outcomes": outcomes, "reason": "concurrent reservations were not serialized"}
    else:
        first_id = str(succeeded[0]["id"])
        gate.settle(first_id, 40)
        follow_up = gate.reserve(
            run_id=None,
            provider="budget-smoke",
            model_name="test",
            capability="chat",
            estimate_microunits=60,
            month=month,
        )
        reservation_ids.append(follow_up.reservation_id)
        result = {
            "status": "PASS",
            "month": month.isoformat(),
            "outcomes": outcomes,
            "settled_microunits": 40,
            "follow_up_reservation": follow_up.reservation_id,
        }

    with engine.begin() as connection:
        if reservation_ids:
            connection.execute(text("DELETE FROM model_calls WHERE id = ANY(:ids)"), {"ids": reservation_ids})
    engine.dispose()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
