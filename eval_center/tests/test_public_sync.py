import json
import uuid

from eval_center.public_sync import build_public_v2_bundle
from eval_center.contracts import normalize_bundle


def test_public_sync_projects_only_opaque_ids_and_sufficient_stats(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    variant_id = "baseline"
    config = {"top_k": 5, "candidate_k": 32, "rrf_k": 60,
              "chunk_size": 1200, "chunk_overlap": 120, "context_budget_chars": 8000}
    experiment_id = str(uuid.uuid4())
    git_sha = "a" * 40
    corpus_hash, qrels_hash, index_version = "b" * 64, "c" * 64, "d" * 64
    model_digest, embedding_digest = "e" * 64, "f" * 64
    manifest = {
        "experiment_id": experiment_id, "git_sha": git_sha,
        "dataset": "scifact", "dataset_version": "scifact-v1",
        "corpus_hash": corpus_hash, "qrels_hash": qrels_hash,
        "index_version": index_version, "started_at": "2026-09-28T00:00:00+00:00",
        "ended_at": "2026-09-28T00:01:00+00:00", "environment": {
            "os_family": "windows", "python_version": "3.11.0", "architecture": "AMD64"},
        "effective_configs": {variant_id: config},
        "models": {"chat": {"name": "qwen3.5:4b", "digest": model_digest},
                   "embedding": {"name": "bge-m3:latest", "digest": embedding_digest}},
        "index_counts": {"documents": 2, "chunks": 2, "embeddings": 2},
    }
    metrics = {variant_id: {"hybrid": {"metrics": {}}}}
    variant = {
        "effective_config": config,
        "document_rankings": {"keyword": ["private-doc-1"], "vector": ["private-doc-1"],
                              "hybrid": ["private-doc-1"]},
        "context_document_rankings": {"keyword": ["private-doc-1"], "vector": ["private-doc-1"],
                                      "hybrid": ["private-doc-1"]},
        "metrics": {"hybrid": {"context_building_ms": 4.0}}, "timings_ms": {"retrieval_ms": 5.0},
        "estimated_context_tokens": {"hybrid": 12},
        "model_usage": {"calls": [], "summary": {}},
    }
    report = {"manifest": manifest, "metrics": metrics, "cases": [{
        "qid": "private-question-id", "question": "PRIVATE QUESTION TEXT", "qrels": {"private-doc-1": 1},
        "variants": {variant_id: variant},
    }]}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")

    bundle, path = build_public_v2_bundle(run_dir, variant_id)

    assert normalize_bundle(bundle)["schema_version"] == 2
    serialized = path.read_text(encoding="utf-8")
    assert "PRIVATE QUESTION TEXT" not in serialized
    assert "private-question-id" not in serialized
    assert "private-doc-1" not in serialized
    assert bundle["manifest"]["dataset_version"].endswith("DOCUMENT-QRELS-V1")
    assert bundle["cases"][0]["statistics"]["coverage"] == {
        "gold_lengths": {}, "intervals": {}, "threshold": 0.8}
    assert bundle["cases"][0]["statistics"]["telemetry"]["timings"]["context_building_ms"] == 4.0
    assert bundle["manifest"]["environment"]["architecture"] == "x86_64"
    assert bundle["metrics"]["fused.recall_at_k"] == 1
