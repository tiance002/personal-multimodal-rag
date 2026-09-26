from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def main() -> int:
    violations: list[dict[str, object]] = []
    roots = [ROOT / "backend" / "app", ROOT / "scripts"]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == Path(__file__).resolve():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                violations.append({"file": str(path.relative_to(ROOT)), "line": 0, "symbol": "PARSE_ERROR", "detail": type(exc).__name__})
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "from_url":
                    violations.append({"file": str(path.relative_to(ROOT)), "line": node.lineno, "symbol": "from_url"})
                if isinstance(node, ast.Attribute) and _chain(node) in {("app", "state", "store"), ("app", "state", "ollama")}:
                    violations.append({"file": str(path.relative_to(ROOT)), "line": node.lineno, "symbol": ".".join(_chain(node))})
    report = {"status": "PASS" if not violations else "FAIL", "violations": violations}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
