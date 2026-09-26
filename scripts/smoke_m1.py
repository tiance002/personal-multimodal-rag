from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from pathlib import Path

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.bootstrap import build_container
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.application.retrieval import HybridRetriever
from backend.app.config import Settings
from backend.app.domain.scope import Scope


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="postgresql+psycopg://rag:rag@127.0.0.1:55432/rag")
    parser.add_argument("--storage-root", type=Path, default=Path("var/smoke-m1-storage"))
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("var/reports/smoke-m1.json"))
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:10]
    settings = Settings(
        database_url=args.database_url,
        storage_root=args.storage_root,
        local_query_enabled=False,
    )
    model = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_embedding_model) if args.real_model else None
    container = build_container(settings, model=model)
    repository = container.store
    kb = repository.create_knowledge_base(f"m1-smoke-{suffix}", "temporary M1 verification", cloud_allowed=False)
    scope = Scope.from_ids([kb["id"]])
    stored = repository.storage.put_stream(io.BytesIO("# 事务回滚\n\n本地知识库中的事务回滚说明。".encode("utf-8")))
    receipt = repository.create_upload(kb["id"], "smoke.md", "text/markdown", stored)
    job = repository.process_job(receipt["job_id"])
    chunks = repository.list_active_chunks(scope)
    citation_service = CitationService(InMemoryCitationStore())
    orchestrator = RAGOrchestrator(
        HybridRetriever(repository, embedding_provider=model),
        citation_service,
        answer_gateway=model,
        cloud_allowed_by_kb={kb["id"]: False},
    )
    run_id = repository.create_run(None, [kb["id"]], [], "事务回滚")
    result = orchestrator.answer_query("事务回滚", scope, RagSettings(local_query_enabled=False), run_id=run_id)
    if citation_service.snapshots:
        repository.persist_evidence(run_id, list(citation_service.snapshots.values()))
    citation = repository.get_citation(run_id, "E1") if result.citations else None
    repository.complete_run(run_id, "completed" if result.error_code is None else "failed", result.error_code)
    repository.delete_knowledge_base(kb["id"])
    report = {
        "status": "PASS" if job.get("status") == "succeeded" and chunks and result.error_code is None and citation else "FAIL",
        "database": "postgresql+pgvector",
        "real_model": bool(args.real_model),
        "chat_model": settings.ollama_chat_model if args.real_model else None,
        "embedding_model": settings.ollama_embedding_model if args.real_model else None,
        "checks": {
            "upload_status": receipt["status"],
            "job": {"status": job.get("status"), "stage": job.get("stage"), "progress": job.get("progress")},
            "active_chunks": len(chunks),
            "answer": result.answer,
            "citations": list(result.citations),
            "citation_readback": citation,
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
