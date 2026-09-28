import json

import pytest

from eval_center.public_selection import select_development_candidate


def _candidate_metrics(ndcg, recall=0.5, context_recall=0.4, latency=30):
    return {
        "metrics": {
            "hybrid.document_metrics_at_10.ndcg_at_k": ndcg,
            "hybrid.document_metrics_at_5.recall_at_k": recall,
            "hybrid.context_document_metrics_at_5.recall_at_k": context_recall,
            "retrieval_ms": latency,
        }
    }


def _write_run(tmp_path, profile="standard"):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    configs = {
        "baseline": {"top_k": 5, "candidate_k": 32, "rrf_k": 60},
        "candidate": {"top_k": 8, "candidate_k": 32, "rrf_k": 60},
    }
    manifest = {
        "experiment_id": "run-1", "dataset": "scifact", "dataset_version": "scifact-v1",
        "split": "development", "profile": profile, "effective_configs": configs,
        "git_sha": "a" * 40, "corpus_hash": "b" * 64, "qrels_hash": "c" * 64,
        "run_hash": "d" * 64,
    }
    summary = {
        "baseline": {"hybrid": {"metrics": {
            "hybrid.document_metrics_at_10.ndcg_at_k": 0.4,
            "hybrid.document_metrics_at_5.recall_at_k": 0.5,
            "hybrid.context_document_metrics_at_5.recall_at_k": 0.4,
            "retrieval_ms": 30,
        }}},
        "candidate": {"hybrid": {"metrics": {
            "hybrid.document_metrics_at_10.ndcg_at_k": 0.6,
            "hybrid.document_metrics_at_5.recall_at_k": 0.6,
            "hybrid.context_document_metrics_at_5.recall_at_k": 0.5,
            "retrieval_ms": 32,
        }}},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps({"manifest": manifest, "metrics": summary}), encoding="utf-8")
    rows = []
    for base, candidate in ((0.3, 0.4), (0.5, 0.6), (0.4, 0.7)):
        rows.append({"variants": {
            "baseline": {"metrics": {"hybrid": {"hybrid.document_metrics_at_10.ndcg_at_k": base}}},
            "candidate": {"metrics": {"hybrid": {"hybrid.document_metrics_at_10.ndcg_at_k": candidate}}},
        }})
    (run_dir / "cases.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return run_dir


def test_selection_uses_development_metrics_and_reports_paired_interval(tmp_path):
    run_dir = _write_run(tmp_path)

    result = select_development_candidate(run_dir, tmp_path)

    assert result["candidate_variant"] == "candidate"
    comparison = result["comparisons_vs_first_variant"]["candidate"]
    assert comparison["paired_cases"] == 3
    assert comparison["mean_delta"] == pytest.approx(1 / 6)
    assert comparison["ci_95"][0] <= comparison["mean_delta"] <= comparison["ci_95"][1]
    selection = json.loads((tmp_path / "runs" / "candidates" / "scifact" / "run-1.json").read_text())
    assert selection["candidate_status"] == "opt_in_pending_locked_validation"


def test_smoke_profile_cannot_freeze_a_candidate(tmp_path):
    run_dir = _write_run(tmp_path, profile="smoke")

    with pytest.raises(ValueError, match="standard development run"):
        select_development_candidate(run_dir, tmp_path)
