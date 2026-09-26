from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {"question", "expected_chunk_ids", "answer_points", "kb_scope"}


def validate(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    records = 0
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        records += 1
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(item, dict):
            errors.append(f"line {line_number}: record must be an object")
            continue
        missing = sorted(REQUIRED_FIELDS - item.keys())
        if missing:
            errors.append(f"line {line_number}: missing fields {missing}")
        if not isinstance(item.get("question"), str) or not item.get("question", "").strip():
            errors.append(f"line {line_number}: question must be a non-empty string")
        for field in ("expected_chunk_ids", "answer_points", "kb_scope"):
            value = item.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(entry, str) and entry for entry in value):
                errors.append(f"line {line_number}: {field} must be a non-empty string list")
    return {
        "path": str(path),
        "records": records,
        "required_fields": sorted(REQUIRED_FIELDS),
        "status": "PASS" if records and not errors else "FAIL",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=Path("evaluations/core.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("var/reports/eval-schema.json"))
    args = parser.parse_args()
    try:
        result = validate(args.path)
    except OSError as exc:
        result = {"path": str(args.path), "status": "FAIL", "errors": [str(exc)]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
