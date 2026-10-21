"""Narrow negative tests for P8 package audit boundary checks."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import zipfile


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "p8_dataset_asset_audit.py"
SPEC = importlib.util.spec_from_file_location("p8_dataset_asset_audit_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def test_archive_path_traversal_is_rejected(tmp_path):
    archive_path = tmp_path / "package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("objects/ok", b"ok")
    with zipfile.ZipFile(archive_path) as archive:
        try:
            AUDIT._read_ref(None, tmp_path, {"path": "../outside", "bytes": 2,
                                              "sha256": AUDIT._sha(b"ok")}, "source", archive)
        except ValueError as exc:
            assert "PACKAGE_UNSAFE_RELATIVE_PATH" in str(exc)
        else:
            raise AssertionError("unsafe archive path was accepted")


def test_archive_hash_mismatch_is_rejected(tmp_path):
    archive_path = tmp_path / "package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("objects/item", b"actual")
    with zipfile.ZipFile(archive_path) as archive:
        try:
            AUDIT._read_ref(None, tmp_path, {"path": "objects/item", "bytes": 6,
                                              "sha256": "0" * 64}, "source", archive)
        except ValueError as exc:
            assert "ARTIFACT_HASH_MISMATCH" in str(exc)
        else:
            raise AssertionError("wrong artifact hash was accepted")


def test_duplicate_source_is_marked_non_independent():
    rows = [
        {"dataset": "raw-a", "unit_id": "a1", "source_sha256": "a" * 64,
         "source_document_id": "shared", "duplicate_source_hash": False,
         "duplicate_source_identity": False, "source_unique_asset": True},
        {"dataset": "prepared-b", "unit_id": "b1", "source_sha256": "a" * 64,
         "source_document_id": "shared", "duplicate_source_hash": False,
         "duplicate_source_identity": False, "source_unique_asset": True},
    ]
    hashes, identities = AUDIT._mark_source_aliases(rows)
    assert len(hashes) == 1 and len(identities) == 1
    assert all(row["duplicate_source_hash"] for row in rows)
    assert all(row["duplicate_source_identity"] for row in rows)
    assert not any(row["source_unique_asset"] for row in rows)
