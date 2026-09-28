import json
import hashlib
import uuid

from eval_center.public_benchmark_cli import main


def test_cli_dry_run_does_not_need_database_or_touch_external_services(capsys, tmp_path):
    assert main(["--phase", "baseline", "--dataset", "scifact", "--data-root", str(tmp_path), "--dry-run"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "DRY_RUN",
        "phase": "baseline",
        "dataset": "scifact",
        "profile": "smoke",
        "split": "development",
        "network_or_database_access": False,
        "data_root": str(tmp_path),
    }


def test_cli_reports_errors_without_echoing_database_url(monkeypatch, capsys):
    monkeypatch.setenv("RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL", "postgresql+psycopg://private:secret@127.0.0.1:1/postgres")
    assert main(["--phase", "preflight", "--dataset", "scifact", "--data-root", "missing-root"]) == 1

    output = capsys.readouterr().out
    assert "private" not in output
    assert "secret" not in output


def test_report_phase_writes_both_public_report_copies(capsys, tmp_path):
    repo = tmp_path / "repo"
    (repo / "eval_center").mkdir(parents=True)
    from unittest.mock import patch
    with patch("eval_center.public_benchmark_cli.Path.resolve", return_value=repo / "eval_center" / "public_benchmark_cli.py"):
        assert main(["--phase", "report", "--data-root", str(tmp_path / "data")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PUBLIC_REPORT_WRITTEN"
    assert (tmp_path / "data" / "reports" / "public-benchmark-optimization-report.md").is_file()
    assert (repo / "docs" / "reviews" / "public-benchmark-optimization-report.md").is_file()


def test_sync_requires_a_completed_locked_validation(capsys, tmp_path):
    assert main(["--phase", "sync", "--dataset", "scifact", "--data-root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "no_locked_validation_candidate"


def test_sync_builds_bundle_only_from_matching_completed_lock(capsys, tmp_path):
    root = tmp_path / "data"
    dataset = "scifact"
    version = "beir-scifact-5f7d1de60b170fc8027bb7898e2efca1"
    variant_id = "candidate"
    config = {"top_k": 5, "candidate_k": 32, "rrf_k": 60,
              "chunk_size": 1200, "chunk_overlap": 120, "context_budget_chars": 8000}
    git_sha = "a" * 40
    source_run = root / "runs" / dataset / "development" / "source"
    source_manifest = {"dataset": dataset, "dataset_version": version, "split": "development",
                       "profile": "standard", "git_sha": git_sha}
    source_run.mkdir(parents=True)
    source_bytes = json.dumps(source_manifest).encode()
    (source_run / "manifest.json").write_bytes(source_bytes)
    source_hash = hashlib.sha256(source_bytes).hexdigest()

    run_dir = root / "runs" / dataset / "locked_holdout" / "validation"
    run_dir.mkdir(parents=True)
    manifest = {
        "experiment_id": str(uuid.uuid4()), "git_sha": git_sha,
        "dataset": dataset, "dataset_version": version, "split": "locked_holdout",
        "phase": "validate", "profile": "standard", "started_at": "2026-09-28T00:00:00+00:00",
        "ended_at": "2026-09-28T00:01:00+00:00", "environment": {
            "os_family": "windows", "python_version": "3.11.0", "architecture": "AMD64"},
        "corpus_hash": "b" * 64, "qrels_hash": "c" * 64, "index_version": "d" * 64,
        "models": {"chat": {"name": "qwen3.5:4b", "digest": "e" * 64},
                   "embedding": {"name": "bge-m3:latest", "digest": "f" * 64}},
        "index_counts": {"documents": 2, "chunks": 2, "embeddings": 2},
        "effective_config": config, "effective_configs": {variant_id: config},
    }
    manifest_bytes = json.dumps(manifest).encode()
    (run_dir / "manifest.json").write_bytes(manifest_bytes)
    (run_dir / "report.json").write_text(json.dumps({
        "metrics": {variant_id: {"hybrid": {"metrics": {}}}},
        "cases": [{"qid": "private-qid", "question": "PRIVATE QUESTION", "qrels": {"private-doc": 1},
                   "variants": {variant_id: {
                       "effective_config": config,
                       "document_rankings": {"keyword": ["private-doc"], "vector": ["private-doc"], "hybrid": ["private-doc"]},
                       "context_document_rankings": {"keyword": ["private-doc"], "vector": ["private-doc"], "hybrid": ["private-doc"]},
                       "metrics": {"hybrid": {}}, "timings_ms": {}, "estimated_context_tokens": {"hybrid": None},
                       "model_usage": {"calls": []},
                    }}}],
    }), encoding="utf-8")
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    lock_path = root / "runs" / "locks" / f"{dataset}-locked-holdout.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"status": "completed", "run_dir": str(run_dir.resolve()),
                                     "git_sha": git_sha, "run_manifest_sha256": manifest_hash}), encoding="utf-8")
    candidate = {
        "dataset": dataset, "dataset_version": version, "split": "development", "profile": "standard",
        "source_run": str(source_run.resolve()), "source_run_manifest_sha256": source_hash,
        "git_sha": git_sha, "candidate_variant": variant_id, "candidate_config": config,
        "candidate_status": "locked_validation_completed",
        "locked_validation": {"run_dir": str(run_dir.resolve()), "git_sha": git_sha},
    }
    candidates = root / "runs" / "candidates" / dataset
    candidates.mkdir(parents=True)
    (candidates / "candidate.json").write_text(json.dumps(candidate), encoding="utf-8")

    assert main(["--phase", "sync", "--dataset", dataset, "--data-root", str(root)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "SANITIZED_BUNDLE_READY"
    assert result["remote_transfer"] == "NOT_RUN_REMOTE_SHA_CHECK_PENDING"
    bundle_text = (run_dir / f"bundle-v2-{variant_id}.json").read_text(encoding="utf-8")
    assert "PRIVATE QUESTION" not in bundle_text
    assert "private-qid" not in bundle_text
    assert "private-doc" not in bundle_text
