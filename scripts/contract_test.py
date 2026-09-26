from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from backend.app.main import create_app

# Declared dependency direction (see docs/superpowers/specs design, "Layering").
# A layer may import only the layers listed for it.
LAYER_RULES: dict[str, tuple[str, ...]] = {
    "domain": (),
    "ports": ("domain",),
    "application": ("domain", "ports"),
    "adapters": ("domain", "ports"),
    "api": ("domain", "ports", "application"),
}


def _layering_violations(app_root: Path) -> list[str]:
    """Report imports that point against the declared dependency direction.

    `bootstrap.py`, `main.py` and `workers/` are composition entrypoints and are
    intentionally exempt: they are the only places allowed to wire adapters.
    """
    violations: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(app_root).parts
        source_layer = parts[0] if parts and parts[0] in LAYER_RULES else None
        if source_layer is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend.app."):
                modules = [(node.module, node.lineno)]
            elif isinstance(node, ast.Import):
                modules = [(alias.name, node.lineno) for alias in node.names if alias.name.startswith("backend.app.")]
            else:
                continue
            for module, lineno in modules:
                segments = module.split(".")
                if len(segments) < 3 or segments[2] not in LAYER_RULES or segments[2] == source_layer:
                    continue
                if segments[2] not in LAYER_RULES[source_layer]:
                    violations.append(
                        f"{path.as_posix()}:{lineno}: {source_layer} must not import {segments[2]} ({module})"
                    )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=Path("contracts/schema.snapshot.json"))
    parser.add_argument("--openapi", type=Path, default=Path("contracts/openapi.json"))
    parser.add_argument("--sse-schema", type=Path, default=Path("contracts/sse/events.schema.json"))
    parser.add_argument("--sse-sample", type=Path, default=Path("contracts/sse/events.sample.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("var/reports/contract-test.json"))
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    approved_openapi = json.loads(args.openapi.read_text(encoding="utf-8"))
    sse_schema = json.loads(args.sse_schema.read_text(encoding="utf-8"))
    openapi = create_app().openapi()
    actual_paths = sorted(openapi.get("paths", {}))
    approved_paths = sorted(approved_openapi.get("paths", {}))
    required = sorted(snapshot["required_paths"])
    missing = [path for path in required if path not in actual_paths]
    unexpected = [path for path in actual_paths if path not in approved_paths]
    snapshot_drift = actual_paths != approved_paths
    forbidden = [term for term in snapshot.get("forbidden_terms", []) if term in json.dumps(openapi, ensure_ascii=False).lower()]
    layering = _layering_violations(Path("backend/app"))
    event_names = sse_schema.get("properties", {}).get("event", {}).get("enum", [])
    sample_errors: list[str] = []
    for line_number, raw in enumerate(args.sse_sample.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            sample_errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(event, dict) or not {"seq", "event", "data"}.issubset(event):
            sample_errors.append(f"line {line_number}: missing required SSE fields")
        elif event["event"] not in event_names or not isinstance(event["seq"], int) or event["seq"] < 1 or not isinstance(event["data"], dict):
            sample_errors.append(f"line {line_number}: SSE event does not match fixed schema")
    result = {
        "status": "PASS"
        if not missing and not unexpected and not snapshot_drift and not forbidden and not sample_errors and not layering
        else "FAIL",
        "required_paths": len(required),
        "actual_paths": len(actual_paths),
        "missing_paths": missing,
        "unexpected_paths": unexpected,
        "openapi_snapshot_drift": snapshot_drift,
        "forbidden_terms": forbidden,
        "layering_violations": layering,
        "sse_events": event_names,
        "sse_sample_errors": sample_errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
