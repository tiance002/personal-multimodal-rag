import json

from backend.app.application.backup import build_backup_manifest, validate_backup_manifest


def test_backup_manifest_records_database_and_storage_hashes(tmp_path):
    storage = tmp_path / "storage"
    source = storage / "objects" / "aa"
    source.mkdir(parents=True)
    (source / "source.bin").write_bytes(b"immutable source")
    dump = tmp_path / "database.sql"
    dump.write_text("select 1;\n", encoding="utf-8")

    manifest = build_backup_manifest(storage, dump, "0006_m4_agent", {"models": {"chat": "ornith-1.5:9b"}})

    assert manifest["migration_revision"] == "0006_m4_agent"
    assert manifest["database"]["sha256"]
    assert manifest["storage"][0]["path"] == "objects/aa/source.bin"
    assert validate_backup_manifest(manifest, storage, dump) == []
    assert json.loads(json.dumps(manifest))["cloud_allowed"] is False


def test_backup_manifest_detects_changed_source(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    source = storage / "source.txt"
    source.write_text("before", encoding="utf-8")
    dump = tmp_path / "database.sql"
    dump.write_text("dump", encoding="utf-8")
    manifest = build_backup_manifest(storage, dump, "head")
    source.write_text("after", encoding="utf-8")

    errors = validate_backup_manifest(manifest, storage, dump)

    assert any(error.startswith("STORAGE_HASH_MISMATCH") for error in errors)

