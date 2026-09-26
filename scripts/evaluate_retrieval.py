from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evaluations/core.jsonl"))
    parser.add_argument("--fixture", type=Path, default=Path("evaluations/fixture_chunks.json"))
    parser.add_argument("--report", type=Path, default=Path("var/reports/eval-retrieval.json"))
    args = parser.parse_args()
    repository = InMemoryRetrievalRepository()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    for item in fixture:
        repository.add(ChunkRecord(item["chunk_id"], item["knowledge_base_id"], item["document_id"], item["version_id"], item["content"], {}))
    retriever = HybridRetriever(repository, top_k=5)
    rows: list[dict[str, object]] = []
    for raw in args.dataset.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        item = json.loads(raw)
        started = time.perf_counter()
        result = retriever.retrieve(Scope.from_ids(item["kb_scope"]), item["question"])
        ranked = [entry.chunk.chunk_id for entry in result.items]
        expected = set(item["expected_chunk_ids"])
        ranks = [rank for rank, chunk_id in enumerate(ranked, start=1) if chunk_id in expected]
        rows.append({"question": item["question"], "ranked_chunk_ids": ranked, "expected_chunk_ids": list(expected), "hit_at_5": bool(ranks), "recall_at_5": len(ranks) / max(len(expected), 1), "mrr": 1 / ranks[0] if ranks else 0.0, "latency_ms": round((time.perf_counter() - started) * 1000, 3)})
    count = len(rows)
    report = {
        "status": "PASS" if count and all(row["hit_at_5"] for row in rows) else "FAIL",
        "mode": "deterministic_fixture",
        "dataset": str(args.dataset),
        "dataset_version": "core-v2-20-cases",
        "corpus": {"fixture": str(args.fixture), "chunks": len(fixture)},
        "chunker": "text/v1",
        "embedding_profile": {"provider": "none", "model": "keyword-fixture", "dimension": 0},
        "model": {"query": "q0-deterministic", "answer": "not-run"},
        "metrics": {"count": count, "hit_at_5": sum(bool(row["hit_at_5"]) for row in rows) / max(count, 1), "recall_at_5": sum(float(row["recall_at_5"]) for row in rows) / max(count, 1), "mrr": sum(float(row["mrr"]) for row in rows) / max(count, 1)},
        "cases": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
