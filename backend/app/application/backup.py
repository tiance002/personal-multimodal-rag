from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def storage_entries(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return entries


def build_backup_manifest(storage_root: Path, database_dump: Path, migration_revision: str, model_report: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "migration_revision": migration_revision,
        "database": {
            "dump_file": database_dump.name,
            "size": database_dump.stat().st_size,
            "sha256": sha256_file(database_dump),
        },
        "storage": storage_entries(storage_root),
        "models": (model_report or {}).get("models", {}),
        "cloud_allowed": False,
    }


def validate_backup_manifest(manifest: dict[str, Any], storage_root: Path, database_dump: Path | None = None) -> list[str]:
    errors: list[str] = []
    if manifest.get("manifest_version") != 1:
        errors.append("MANIFEST_VERSION_UNSUPPORTED")
    database = manifest.get("database")
    if not isinstance(database, dict) or not database.get("dump_file") or not database.get("sha256"):
        errors.append("DATABASE_DUMP_METADATA_MISSING")
    elif database_dump is not None:
        if not database_dump.exists():
            errors.append("DATABASE_DUMP_MISSING")
        elif sha256_file(database_dump) != database.get("sha256"):
            errors.append("DATABASE_DUMP_HASH_MISMATCH")
    recorded = manifest.get("storage")
    if not isinstance(recorded, list):
        errors.append("STORAGE_MANIFEST_MISSING")
        return errors
    root = storage_root.resolve()
    for entry in recorded:
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            errors.append("STORAGE_PATH_INVALID")
            continue
        path = root / relative
        if not path.exists():
            errors.append(f"STORAGE_FILE_MISSING:{relative}")
            continue
        if path.stat().st_size != entry.get("size") or sha256_file(path) != entry.get("sha256"):
            errors.append(f"STORAGE_HASH_MISMATCH:{relative}")
    return errors

