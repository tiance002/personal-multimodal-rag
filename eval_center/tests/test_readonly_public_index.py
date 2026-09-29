import json
from pathlib import Path

import pytest

from eval_center.readonly_public_index import approved_run_directory, validate_completed_run
from eval_center.public_data import DATASET_VERSIONS
from eval_center.verification import ExperimentInvalidError


def test_only_known_complete_development_runs_can_be_attached(tmp_path):
    path = approved_run_directory(tmp_path, "scifact")
    assert path.name == "20260928T060742Z-6f0c61007b"
    with pytest.raises(ExperimentInvalidError, match="unapproved_public_dataset"):
        approved_run_directory(tmp_path, "other")


def test_completed_run_requires_manifest_cases_and_exact_dataset_identity(tmp_path):
    run = approved_run_directory(tmp_path, "scifact")
    run.mkdir(parents=True)
    (run / "cases.jsonl").write_text('{"qid":"1"}\n', encoding="utf-8")
    (run / "report.json").write_text("{}", encoding="utf-8")
    manifest = {"dataset": "scifact", "dataset_version": DATASET_VERSIONS["scifact"], "split": "development",
                "profile": "standard", "phase": "optimize", "ended_at": "2026-09-28T00:00:00Z",
                "sample_count": 1, "corpus_hash": "a" * 64,
                "index_version": "b" * 64,
                "index_counts": {"documents": 1, "chunks": 1, "embeddings": 1},
                "models": {"embedding": {"name": "bge-m3:latest", "digest": "c" * 64}},
                "effective_config": {"chunk_size": 1200, "chunk_overlap": 120}}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert validate_completed_run(run, "scifact")["index_version"] == "b" * 64
    manifest["sample_count"] = 2
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExperimentInvalidError, match="incomplete_public_run"):
        validate_completed_run(run, "scifact")


def test_partial_or_locked_run_is_rejected(tmp_path):
    run = approved_run_directory(tmp_path, "scifact")
    run.mkdir(parents=True)
    (run / "interrupted.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ExperimentInvalidError, match="incomplete_public_run"):
        validate_completed_run(run, "scifact")
