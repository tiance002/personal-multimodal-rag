from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.quality import QualityGate
from backend.app.application.query_router import QueryRouter
from backend.app.application.rag_orchestrator import RAGOrchestrator
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from validate_eval import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic RAG evidence coverage without a chat model.")
    parser.add_argument("--dataset", type=Path, default=Path("evaluations/quality_v1.jsonl"))
    parser.add_argument("--fixture", type=Path, default=Path("evaluations/quality_fixture_chunks.json"))
    parser.add_argument("--report", type=Path, default=Path("var/reports/eval-rag-quality.json"))
    args = parser.parse_args()
    schema_result = validate(args.dataset, schema="quality-v1")
    if schema_result["status"] != "PASS":
        raise ValueError(f"invalid quality-v1 dataset: {schema_result['errors']}")

    repository = InMemoryRetrievalRepository()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    for item in fixture:
        repository.add(ChunkRecord(
            item["chunk_id"], item["knowledge_base_id"], item["document_id"],
            item["version_id"], item["content"], {"kind": "text", "start": 0, "end": len(item["content"])},
        ))
    rows = []
    for line in args.dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        required = {"question", "expected_chunk_ids", "answer_points", "kb_scope"}
        if not required <= case.keys():
            raise ValueError(f"dataset row lacks required fields: {sorted(required - case.keys())}")
        retrieved = []
        citations = CitationService(InMemoryCitationStore())
        orchestrator = RAGOrchestrator(
            HybridRetriever(repository, top_k=5), citations,
            on_retrieval=lambda run_id, items: retrieved.extend(items),
        )
        result = orchestrator.answer_query(case["question"], Scope.from_ids(case["kb_scope"]))
        plan = QueryRouter().plan(case["question"])
        coverage = QualityGate().evaluate_chunks([item.chunk for item in retrieved], plan)
        expected = set(case["expected_chunk_ids"])
        actual = {item.chunk.chunk_id for item in retrieved}
        outcome = (
            "partial" if "PARTIAL_EVIDENCE" in result.trace.reason_codes else
            "refuse" if result.error_code is not None else "full"
        )
        citation_readback = all(citations.resolve(result.run_id, label).chunk_id in actual for label in result.citations)
        row = {
            "scenario": case["scenario"], "question": case["question"],
            "expected_outcome": case["expected_outcome"], "outcome": outcome,
            "expected_chunk_ids": sorted(expected), "retrieved_chunk_ids": sorted(actual),
            "expected_targets": case["expected_target_count"], "planned_targets": len(plan.targets),
            "target_coverage": 0 if not plan.targets else (len(plan.targets) - len(coverage.missing_targets)) / len(plan.targets),
            "citation_readback": citation_readback, "model_calls": result.trace.model_calls,
            "pass": outcome == case["expected_outcome"] and expected <= actual and
                    len(plan.targets) == case["expected_target_count"] and citation_readback and
                    result.trace.model_calls == 0,
        }
        rows.append(row)
    report = {
        "status": "PASS" if rows and all(row["pass"] for row in rows) else "FAIL",
        "mode": "deterministic_fixture", "dataset_version": "quality-v1",
        "dataset": str(args.dataset), "fixture": str(args.fixture),
        "model": {"query": "rules", "embedding": "none", "answer": "none"},
        "metrics": {
            "cases": len(rows), "passed": sum(bool(row["pass"]) for row in rows),
            "expected_hit_at_5": sum(bool(set(row["expected_chunk_ids"]) & set(row["retrieved_chunk_ids"])) for row in rows if row["expected_chunk_ids"]) / max(1, sum(bool(row["expected_chunk_ids"]) for row in rows)),
            "mean_target_coverage": sum(float(row["target_coverage"]) for row in rows if row["expected_targets"]) / max(1, sum(bool(row["expected_targets"]) for row in rows)),
            "max_chat_model_calls": max(row["model_calls"] for row in rows),
        },
        "cases": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "metrics": report["metrics"], "failed": [row["scenario"] for row in rows if not row["pass"]]}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
