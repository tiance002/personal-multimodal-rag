from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from eval_center.contracts import (
    BundleValidationError,
    bundle_digest,
    canonical_bundle_bytes,
    parse_bundle_bytes,
)
from eval_center.store import ExperimentConflictError, import_bundle


def _database_path() -> Path:
    return Path(os.environ.get("EVAL_CENTER_DB", "var/eval-center/registry.sqlite3"))


def _read_bundle(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return parse_bundle_bytes(source.read(10 * 1024 * 1024 + 1))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or import a sanitized RAG evaluation bundle.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate a bundle without writing it")
    validate_parser.add_argument("bundle", type=Path)
    sanitize_parser = subparsers.add_parser(
        "sanitize", help="write a strict canonical allowlist snapshot for transfer"
    )
    sanitize_parser.add_argument("bundle", type=Path)
    sanitize_parser.add_argument("output", type=Path)
    import_parser = subparsers.add_parser("import", help="transactionally import a sanitized bundle")
    import_parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)

    try:
        bundle = _read_bundle(args.bundle)
        experiment_id = bundle["manifest"]["experiment_id"]
        digest = bundle_digest(bundle)
        if args.command == "validate":
            result = {"status": "valid", "experiment_id": experiment_id, "digest": digest}
        elif args.command == "sanitize":
            with args.output.open("xb") as output:
                output.write(canonical_bundle_bytes(bundle))
            result = {"status": "sanitized", "experiment_id": experiment_id, "digest": digest}
        else:
            imported = import_bundle(_database_path(), bundle)
            result = {"status": imported["status"], "experiment_id": experiment_id, "digest": digest}
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except ExperimentConflictError as exc:
        print(json.dumps({"status": "CONFLICT", "error": exc.code}, separators=(",", ":")))
        return 1
    except BundleValidationError as exc:
        print(json.dumps({"status": "FAIL", "error": exc.code}, separators=(",", ":")))
        return 1
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError):
        print(json.dumps({"status": "FAIL", "error": "invalid_or_unavailable_bundle"}, separators=(",", ":")))
        return 1
    except Exception:
        # Avoid leaking bundle fields, filesystem paths, or database details in routine output.
        print(json.dumps({"status": "FAIL", "error": "import_failed"}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
