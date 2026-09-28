from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from eval_center.store import (
    ExperimentConflictError,
    compare_experiments,
    get_experiment,
    import_bundle,
    initialize_database,
    list_experiments,
)


def make_bundle(experiment_id: str = "e4a3e2db-5773-4d12-9be9-2db322654e12", *, dataset_version: str = "core-v1"):
    config = {"top_k": 5, "retrieval_mode": "hybrid"}
    return {
        "schema_version": 1,
        "manifest": {
            "experiment_id": experiment_id,
            "git_sha": "a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b",
            "dataset_version": dataset_version,
            "corpus_hash": "a" * 64,
            "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "model_profile": "q0-deterministic",
            "embedding_profile": "none",
            "started_at": "2026-09-27T10:00:00+08:00",
            "ended_at": "2026-09-27T10:00:03+08:00",
            "environment": {"os_family": "windows", "python_version": "3.12.5", "architecture": "x86_64"},
            "evaluation_mode": "deterministic_fixture",
            "status": "pass",
            "sample_count": 1,
        },
        "config": config,
        "metrics": {"hit_at_5": 1.0, "mrr": 0.75},
        "cases": [{"case_id": "case_123456789abc", "status": "passed", "metrics": {"mrr": 0.75}}],
        "errors": [],
    }


def test_initialize_database_is_repeatable_and_records_schema_v2(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"experiments", "experiment_cases", "experiment_errors"} <= names
        columns = {row[1] for row in connection.execute("PRAGMA table_info(experiments)")}
        assert "question" not in columns
        assert "answer" not in columns
        assert "chunk_id" not in columns


def test_import_is_idempotent_and_lists_sanitized_details(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    bundle = make_bundle()

    first = import_bundle(database, bundle)
    second = import_bundle(database, bundle)

    assert first["status"] == "imported"
    assert second == {"status": "unchanged", "digest": first["digest"]}
    rows = list_experiments(database, limit=20, offset=0, include_unverified=True)
    assert len(rows) == 1
    assert rows[0]["experiment_id"] == bundle["manifest"]["experiment_id"]
    detail = get_experiment(database, bundle["manifest"]["experiment_id"])
    assert detail["cases"][0]["case_id"] == "case_123456789abc"
    assert detail["metrics"] == bundle["metrics"]


def test_changed_content_under_existing_id_is_a_conflict(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    bundle = make_bundle()
    import_bundle(database, bundle)
    changed = make_bundle()
    changed["metrics"]["mrr"] = 0.5
    changed["cases"][0]["metrics"]["mrr"] = 0.5

    with pytest.raises(ExperimentConflictError) as exc:
        import_bundle(database, changed)

    assert exc.value.code == "experiment_content_conflict"
    assert len(list_experiments(database, limit=20, offset=0, include_unverified=True)) == 1


def test_failed_case_insert_rolls_back_entire_import(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_test_case BEFORE INSERT ON experiment_cases "
            "WHEN NEW.case_id = 'case_123456789abc' BEGIN SELECT RAISE(ABORT, 'forced'); END"
        )

    with pytest.raises(sqlite3.IntegrityError):
        import_bundle(database, make_bundle())

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM experiment_cases").fetchone()[0] == 0


def test_concurrent_identical_imports_create_one_record(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    bundle = make_bundle()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: import_bundle(database, bundle), range(2)))

    assert sorted(result["status"] for result in results) == ["imported", "unchanged"]
    assert len(list_experiments(database, limit=20, offset=0, include_unverified=True)) == 1


def test_detail_returns_none_and_comparison_warns_about_dataset_mismatch(tmp_path):
    database = tmp_path / "runs.sqlite3"
    initialize_database(database)
    first_id = "e4a3e2db-5773-4d12-9be9-2db322654e12"
    second_id = "d7fa2e01-472c-47c0-903b-0d496c4d8f3a"
    import_bundle(database, make_bundle(first_id, dataset_version="core-v1"))
    import_bundle(database, make_bundle(second_id, dataset_version="core-v2"))

    assert get_experiment(database, "c66a1e20-01c2-42d9-b70c-289aaf645ad9") is None
    comparison = compare_experiments(database, [first_id, second_id])
    assert comparison["comparable"] is False
    assert comparison["mismatches"]['dataset_version'] == ["core-v1", "core-v2"]
    assert comparison['mismatches']['validation_status'] == ['unverified','unverified']
    assert comparison['metrics'] == {}
