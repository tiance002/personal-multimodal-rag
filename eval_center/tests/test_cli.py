from __future__ import annotations

import hashlib
import json

from eval_center.cli import main


def make_bundle():
    config = {"top_k": 5, "retrieval_mode": "hybrid"}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": 1,
        "manifest": {
            "experiment_id": "e4a3e2db-5773-4d12-9be9-2db322654e12",
            "git_sha": "a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b",
            "dataset_version": "core-v1", "corpus_hash": "a" * 64, "config_hash": config_hash,
            "model_profile": "q0-deterministic", "embedding_profile": "none",
            "started_at": "2026-09-27T10:00:00+08:00", "ended_at": "2026-09-27T10:00:03+08:00",
            "environment": {"os_family": "linux", "python_version": "3.12.3", "architecture": "x86_64"},
            "evaluation_mode": "deterministic_fixture", "status": "pass", "sample_count": 0,
        },
        "config": config,
        "metrics": {"mrr": 0.75},
        "cases": [],
        "errors": [],
    }


def test_validate_and_import_print_only_machine_status_and_keep_input(tmp_path, monkeypatch, capsys):
    bundle_path = tmp_path / "bundle.json"
    original = json.dumps(make_bundle(), ensure_ascii=False, indent=2) + "\n"
    bundle_path.write_text(original, encoding="utf-8")
    database = tmp_path / "registry.sqlite3"
    monkeypatch.setenv("EVAL_CENTER_DB", str(database))

    assert main(["validate", str(bundle_path)]) == 0
    validate_output = json.loads(capsys.readouterr().out)
    assert validate_output["status"] == "valid"
    assert set(validate_output) == {"status", "experiment_id", "digest"}

    assert main(["import", str(bundle_path)]) == 0
    import_output = json.loads(capsys.readouterr().out)
    assert import_output["status"] == "imported"
    assert set(import_output) == {"status", "experiment_id", "digest"}
    assert bundle_path.read_text(encoding="utf-8") == original

    changed = make_bundle()
    changed["metrics"]["mrr"] = 0.5
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    assert main(["import", str(changed_path)]) == 1
    conflict_output = json.loads(capsys.readouterr().out)
    assert conflict_output == {"status": "CONFLICT", "error": "experiment_content_conflict"}


def test_validate_failure_does_not_echo_bundle_values(tmp_path, capsys):
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text('{"secret":"CLI PRIVATE CANARY"}', encoding="utf-8")

    assert main(["validate", str(bundle_path)]) == 1
    output = capsys.readouterr().out
    assert "CLI PRIVATE CANARY" not in output
    assert "secret" not in output


def test_duplicate_json_key_with_hidden_private_value_is_rejected(tmp_path, capsys):
    serialized = json.dumps(make_bundle(), separators=(",", ":"))
    serialized = serialized.replace(
        '"dataset_version":"core-v1"',
        '"dataset_version":"PRIVATE CANARY","dataset_version":"core-v1"',
        1,
    )
    bundle_path = tmp_path / "duplicate.json"
    bundle_path.write_text(serialized, encoding="utf-8")

    assert main(["validate", str(bundle_path)]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE CANARY" not in output
    assert json.loads(output) == {"status": "FAIL", "error": "duplicate_json_key"}


def test_sanitize_writes_only_a_validated_canonical_bundle(tmp_path, capsys):
    bundle = make_bundle()
    bundle["ignored_private_field"] = "PRIVATE CANARY"
    bundle_path = tmp_path / "source.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    output_path = tmp_path / "sanitized.json"

    assert main(["sanitize", str(bundle_path), str(output_path)]) == 1
    capsys.readouterr()

    bundle.pop("ignored_private_field")
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    assert main(["sanitize", str(bundle_path), str(output_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "sanitized"
    sanitized = output_path.read_bytes()
    assert b"PRIVATE CANARY" not in sanitized
    assert json.loads(sanitized) == bundle
