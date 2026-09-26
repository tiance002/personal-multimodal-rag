from __future__ import annotations

import argparse
import io
import json
import sys
import time
import uuid
from pathlib import Path

from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.scope import Scope


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Real PostgreSQL/BGE/Qwen evidence-coverage smoke.")
    parser.add_argument("--database-url", default=Settings.database_url)
    parser.add_argument("--report", type=Path, default=Path("var/reports/smoke-quality-postgres.json"))
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:10]
    settings = Settings(database_url=args.database_url, storage_root=Path(f"var/smoke-quality-storage-{suffix}"))
    container = build_container(settings)
    store = container.store
    kb = store.create_knowledge_base(f"quality-{suffix}", "temporary quality smoke", cloud_allowed=False)
    checks = {}
    document_ids = {}
    calls: list[list[str]] = []

    def invoke(question: str, scope: Scope, document_scope: list[str] | None = None):
        document_scope = document_scope or []
        run_id = store.create_run(None, [kb["id"]], document_scope, question)
        result = container.quick_chain.invoke(
            question,
            scope,
            run_id=run_id,
            cloud_allowed_by_kb={kb["id"]: False},
            on_retrieval=lambda _run_id, items: calls.append([item.chunk.chunk_id for item in items]),
        )
        if result.evidence:
            store.persist_evidence(run_id, list(result.evidence))
        store.complete_run(run_id, "completed" if result.error_code is None else "failed", result.error_code)
        return result, run_id

    try:
        for name, content in (
            ("A.md", "# A方案\n\nA方案采购成本1000元。A方案维护成本200元。"),
            ("B.md", "# B方案\n\nB方案采购成本1200元。B方案维护成本300元。"),
        ):
            stored = store.storage.put_stream(io.BytesIO(content.encode("utf-8")))
            receipt = store.create_upload(kb["id"], name, "text/markdown", stored)
            document_ids[name] = receipt["document_id"]
            job = store.process_job(receipt["job_id"])
            checks[f"ingest_{name}"] = job.get("status")

        scope = Scope.from_ids([kb["id"]])
        started = time.perf_counter()
        compound, compound_run_id = invoke("A 与 B 两种方案各自的采购成本是多少？", scope)
        checks["compound"] = {
            "error_code": compound.error_code, "answer": compound.answer,
            "citations": list(compound.citations), "chat_model_calls": compound.trace.model_calls,
            "retrieved_chunk_count": len(calls[-1]) if calls else 0,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        missing, _missing_run_id = invoke("C 的采购成本是多少？", scope)
        checks["missing"] = {
            "error_code": missing.error_code, "chat_model_calls": missing.trace.model_calls,
            "citations": list(missing.citations),
        }
        partial, _partial_run_id = invoke(
            "A 与 B 两种方案各自的采购成本是多少？",
            Scope.from_ids([kb["id"]], [document_ids["A.md"]]),
            [document_ids["A.md"]],
        )
        checks["document_scoped_partial"] = {
            "error_code": partial.error_code, "reason_codes": list(partial.trace.reason_codes),
            "answer": partial.answer, "citations": list(partial.citations),
            "chat_model_calls": partial.trace.model_calls,
        }
        citation_readback = all(store.get_citation(compound_run_id, label) for label in compound.citations)
        checks["citation_readback"] = bool(citation_readback)
        report = {
            "status": "PASS" if all(checks[f"ingest_{name}"] == "succeeded" for name in ("A.md", "B.md"))
            and compound.error_code is None and len(compound.citations) >= 2
            and compound.trace.model_calls == 1 and citation_readback
            and missing.error_code == "INSUFFICIENT_EVIDENCE" and missing.trace.model_calls == 0
            and partial.error_code is None and "PARTIAL_EVIDENCE" in partial.trace.reason_codes
            and partial.trace.model_calls == 0 and len(partial.citations) == 1 else "FAIL",
            "models": {"chat": settings.ollama_chat_model, "embedding": settings.ollama_embedding_model},
            "checks": checks,
        }
    finally:
        store.delete_knowledge_base(kb["id"])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
