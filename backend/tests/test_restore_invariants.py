from backend.app.application.backup import validate_backup_manifest


def test_restore_rejects_manifest_without_database_dump(tmp_path):
    errors = validate_backup_manifest({"manifest_version": 1, "storage": []}, tmp_path)

    assert "DATABASE_DUMP_METADATA_MISSING" in errors

