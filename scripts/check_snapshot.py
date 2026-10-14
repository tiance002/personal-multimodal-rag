"""Offline syntax/import check; never instantiates a runtime or opens services."""
from pathlib import Path
import ast, importlib, sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
files = sorted((root / "backend/weknora_port").rglob("*.py"))
for path in files:
    ast.parse(path.read_bytes(), filename=str(path))
for module in ("backend.weknora_port.runtime", "backend.weknora_port.http_api", "backend.weknora_port.documents", "backend.weknora_port.image_tasks"):
    importlib.import_module(module)
print(f"OFFLINE_SYNTAX_IMPORT_PASS: {len(files)} source modules parsed; four entry modules imported; no service instantiated")
