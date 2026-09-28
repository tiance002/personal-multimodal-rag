from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {"question", "expected_chunk_ids", "answer_points", "kb_scope"}
CASE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


def validate(path: Path, *, schema: str = "core-v1") -> dict[str, Any]:
    if schema not in {"core-v1", "quality-v1"}:
        raise ValueError(f"unsupported evaluation schema: {schema}")
    errors: list[str] = []
    records = 0
    seen_case_ids: set[str] = set()
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
        if "case_id" in item:
            case_id = item["case_id"]
            if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id):
                errors.append(f"line {line_number}: case_id must be a stable lowercase identifier")
            elif case_id in seen_case_ids:
                errors.append(f"line {line_number}: case_id must be unique within the dataset")
            else:
                seen_case_ids.add(case_id)
        missing = sorted(REQUIRED_FIELDS - item.keys())
        if missing:
            errors.append(f"line {line_number}: missing fields {missing}")
        if schema == "quality-v1":
            extra = {"dataset_version", "scenario", "expected_outcome", "expected_target_count"} - item.keys()
            if extra:
                errors.append(f"line {line_number}: missing quality fields {sorted(extra)}")
            if item.get("dataset_version") != "quality-v1":
                errors.append(f"line {line_number}: dataset_version must be quality-v1")
            if item.get("expected_outcome") not in {"full", "partial", "refuse"}:
                errors.append(f"line {line_number}: expected_outcome is invalid")
            target_count = item.get("expected_target_count")
            if type(target_count) is not int or target_count < 0:
                errors.append(f"line {line_number}: expected_target_count must be a nonnegative integer")
        if not isinstance(item.get("question"), str) or not item.get("question", "").strip():
            errors.append(f"line {line_number}: question must be a non-empty string")
        for field in ("expected_chunk_ids", "answer_points", "kb_scope"):
            value = item.get(field)
            allow_empty = schema == "quality-v1" and item.get("expected_outcome") == "refuse" and field != "kb_scope"
            if not isinstance(value, list) or (not value and not allow_empty) or not all(isinstance(entry, str) and entry for entry in value):
                errors.append(f"line {line_number}: {field} must be a {'possibly empty' if allow_empty else 'non-empty'} string list")
    return {
        "path": str(path),
        "records": records,
        "required_fields": sorted(REQUIRED_FIELDS),
        "schema": schema,
        "status": "PASS" if records and not errors else "FAIL",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=Path("evaluations/core.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("var/reports/eval-schema.json"))
    parser.add_argument("--schema", choices=("core-v1", "quality-v1"), default="core-v1")
    args = parser.parse_args()
    try:
        result = validate(args.path, schema=args.schema)
    except OSError as exc:
        result = {"path": str(args.path), "status": "FAIL", "errors": [str(exc)]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
