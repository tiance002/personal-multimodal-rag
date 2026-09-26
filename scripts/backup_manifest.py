from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.application.backup import build_backup_manifest, validate_backup_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--storage-root", type=Path, required=True)
    create.add_argument("--database-dump", type=Path, required=True)
    create.add_argument("--migration-revision", required=True)
    create.add_argument("--model-report", type=Path)
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--storage-root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--database-dump", type=Path)
    args = parser.parse_args()

    if args.command == "create":
        model_report = json.loads(args.model_report.read_text(encoding="utf-8")) if args.model_report and args.model_report.exists() else {}
        manifest = build_backup_manifest(args.storage_root, args.database_dump, args.migration_revision, model_report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "PASS", "manifest": str(args.output)}, ensure_ascii=False))
        return 0

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    database_dump = args.database_dump or args.manifest.parent / str(manifest.get("database", {}).get("dump_file", "database.sql"))
    errors = validate_backup_manifest(manifest, args.storage_root, database_dump)
    result = {"status": "PASS" if not errors else "FAIL", "errors": errors, "manifest": str(args.manifest)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

