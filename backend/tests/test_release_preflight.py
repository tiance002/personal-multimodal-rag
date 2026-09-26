from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _legacy_call_sites() -> list[str]:
    violations: list[str] = []
    roots = (ROOT / "scripts", ROOT / "backend" / "app")
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.name == "release_preflight.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "from_url":
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: from_url")
                if isinstance(node, ast.Attribute):
                    chain = _attribute_chain(node)
                    if chain in {("app", "state", "store"), ("app", "state", "ollama")}:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {'.'.join(chain)}")
    return violations


def test_release_preflight_has_no_deleted_runtime_symbols() -> None:
    assert _legacy_call_sites() == []
