import json

from eval_center.public_report import render_public_report, write_public_report


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_report_uses_aggregate_artifacts_and_omits_case_text(tmp_path):
    root = tmp_path / "data"
    repo = tmp_path / "repo"
    _write(root / "prepared" / "adapters" / "scifact" / "manifest.json", {
        "dataset": "scifact", "dataset_version": "scifact-v1", "document_count": 2,
        "split_counts": {"development": 1, "locked_holdout": 1},
        "qrels_kind": "official_document_qrels", "source_licenses": {"abstracts": "ODC-By 1.0"},
    })
    manifest = {
        "dataset": "scifact", "dataset_version": "scifact-v1", "phase": "baseline",
        "split": "development", "profile": "standard", "sample_count": 1,
        "git_sha": "a" * 40, "index_counts": {"documents": 2},
        "effective_configs": {"baseline": {"top_k": 5}},
    }
    _write(root / "runs" / "scifact" / "development" / "run1" / "manifest.json", manifest)
    _write(root / "runs" / "scifact" / "development" / "run1" / "report.json", {
        "manifest": manifest,
        "metrics": {"baseline": {"hybrid": {"metrics": {
            "hybrid.document_metrics_at_10.ndcg_at_k": 0.75,
            "hybrid.document_metrics_at_5.recall_at_k": 0.5,
            "hybrid.context_document_metrics_at_5.recall_at_k": 0.25,
            "retrieval_ms": 12.5,
        }}}},
        "cases": [{"question": "PRIVATE QUESTION", "answer": "PRIVATE ANSWER"}],
    })
    _write(root / "runs" / "scifact" / "development" / "failed1" / "failure.json", {
        "dataset": "scifact", "phase": "qa", "split": "development", "profile": "smoke",
        "stage": "index_validation", "error_code": "source_coordinates_mismatch",
        "documents": 50, "queries_completed": 0, "git_sha": "b" * 40,
    })

    report = render_public_report(root)
    paths = write_public_report(root, repo)

    assert "0.7500" in report
    assert "LongBench Chinese" in report
    assert "NOT RUN" in report
    assert "source_coordinates_mismatch" in report
    assert "PRIVATE QUESTION" not in report
    assert "PRIVATE ANSWER" not in report
    assert "PRIVATE QUESTION" not in (root / "reports" / "public-benchmark-optimization-report.md").read_text(encoding="utf-8")
    assert paths["repository_path"].endswith("public-benchmark-optimization-report.md")
