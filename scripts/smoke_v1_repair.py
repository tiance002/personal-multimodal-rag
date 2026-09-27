"""Real end-to-end verification of the V1 targeted repairs.

Covers the two repaired flows against a real PostgreSQL/pgvector database and,
with ``--real-model``, a real Ollama chat + embedding model:

* P1-1 ingestion refresh recovery: a failed newest version stays visible with a
  usable retry entry instead of hiding behind an older ``ready`` active version.
* P1-2 historical citation recovery: an assistant message keeps its ``run_id``
  and citation labels, and the frozen evidence is re-readable.

The script only touches the knowledge base it creates and removes it afterwards.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import text

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.scope import Scope


def _purge(repository, kb_id: str) -> None:
    version_filter = "version_id IN (SELECT id FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE knowledge_base_id=:kb))"
    chunk_filter = "chunk_id IN (SELECT id FROM chunks WHERE knowledge_base_id=:kb)"
    with repository.engine.begin() as connection:
        connection.execute(text(f"DELETE FROM chunk_terms WHERE {chunk_filter}"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM chunk_embeddings WHERE {chunk_filter}"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM chunk_assets WHERE {version_filter}"), {"kb": kb_id})
        connection.execute(text("DELETE FROM chunks WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_sections WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_assets WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text(f"DELETE FROM ingestion_jobs WHERE {version_filter}"), {"kb": kb_id})
        connection.execute(text("DELETE FROM document_versions WHERE document_id IN (SELECT id FROM documents WHERE knowledge_base_id=:kb)"), {"kb": kb_id})
        connection.execute(text("DELETE FROM documents WHERE knowledge_base_id=:kb"), {"kb": kb_id})
        connection.execute(text("DELETE FROM knowledge_bases WHERE id=:kb"), {"kb": kb_id})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="postgresql+psycopg://rag:rag@127.0.0.1:55432/rag")
    parser.add_argument("--storage-root", type=Path, default=Path("var/smoke-v1-repair-storage"))
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--report", type=Path, default=Path("var/reports/smoke-v1-repair.json"))
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:10]
    settings = Settings(database_url=args.database_url, storage_root=args.storage_root, local_query_enabled=False)
    model = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_embedding_model) if args.real_model else None
    container = build_container(settings, model=model)
    repository = container.store
    kb = repository.create_knowledge_base(f"v1-repair-{suffix}", "temporary V1 repair verification", cloud_allowed=False)
    scope = Scope.from_ids([kb["id"]])
    checks: dict[str, object] = {}
    run_id: str | None = None
    conversation_id: str | None = None
    try:
        # P1-1(a): a real Markdown upload indexes to `ready`.
        stored = repository.storage.put_stream(io.BytesIO("# 事务回滚\n\n本地知识库中的事务回滚说明。".encode("utf-8")))
        receipt = repository.create_upload(kb["id"], "repair.md", "text/markdown", stored)
        job = repository.process_job(receipt["job_id"])
        checks["upload_status"] = receipt["status"]
        checks["first_job_status"] = job.get("status")

        # P1-1(b): a failing newest version must not hide behind the ready active version.
        failing = repository.create_version(
            receipt["document_id"], "repair.md", "text/markdown", repository.storage.put_stream(io.BytesIO(b""))
        )
        repository.process_job(failing["job_id"])
        document = next(item for item in repository.list_documents(kb["id"]) if str(item["id"]) == receipt["document_id"])
        latest_job = document.get("latest_job") or {}
        checks["active_index_status"] = document["index_status"]
        checks["latest_version_no"] = document["latest_version_no"]
        checks["latest_index_status"] = document["latest_index_status"]
        checks["latest_job_status"] = latest_job.get("status")
        checks["latest_job_error"] = latest_job.get("error_code")
        checks["retry_entry_available"] = bool(latest_job.get("attempts", 0) < latest_job.get("max_attempts", 0))

        # P1-1(c): the retry entry is usable and re-queues the job.
        retried = repository.retry_job(latest_job["id"])
        checks["retry_status"] = retried.get("status") if retried else None

        # P1-2: a real Q&A keeps its run link and citation labels across a reopen.
        conversation = repository.create_conversation([kb["id"]])
        conversation_id = conversation["id"]
        run_id = repository.create_run(conversation_id, [kb["id"]], [], "事务回滚")
        result = container.quick_chain.invoke("事务回滚", scope, run_id=run_id, cloud_allowed_by_kb={kb["id"]: False})
        committed = repository.finalize_answer(
            run_id=run_id,
            conversation_id=conversation_id,
            answer=result.answer,
            citations=tuple(result.citations),
            snapshots=tuple(result.evidence),
            error_code=result.error_code,
            mode="quick",
        )
        messages = repository.list_messages(conversation_id)
        assistant = next((message for message in messages if message["role"] == "assistant"), None)
        checks["answer_error_code"] = result.error_code
        checks["finalized"] = committed
        checks["history_run_linked"] = bool(assistant and assistant.get("run_id") == run_id)
        checks["history_citations"] = assistant.get("citations") if assistant else None
        citation = repository.get_citation(run_id, "E1") if result.citations else None
        checks["citation_readback"] = bool(citation and citation.get("quote"))
        checks["citation_locator"] = citation.get("locator") if citation else None

        passed = (
            checks["first_job_status"] == "succeeded"
            and checks["active_index_status"] == "ready"
            and checks["latest_index_status"] == "failed"
            and checks["latest_job_status"] == "failed"
            and checks["retry_entry_available"] is True
            and checks["retry_status"] == "queued"
            and result.error_code is None
            and checks["finalized"] is True
            and checks["history_run_linked"] is True
            and bool(checks["history_citations"])
            and checks["citation_readback"] is True
        )
        report = {
            "status": "PASS" if passed else "FAIL",
            "database": "postgresql+pgvector",
            "real_model": bool(args.real_model),
            "chat_model": settings.ollama_chat_model if args.real_model else None,
            "embedding_model": settings.ollama_embedding_model if args.real_model else None,
            "knowledge_base": kb["id"],
            "checks": checks,
        }
    finally:
        if conversation_id is not None and run_id is not None:
            with repository.engine.begin() as connection:
                connection.execute(text("DELETE FROM retrieval_events WHERE run_id=:id"), {"id": run_id})
                connection.execute(text("DELETE FROM answer_evidence WHERE run_id=:id"), {"id": run_id})
                connection.execute(text("DELETE FROM conversation_messages WHERE conversation_id=:id"), {"id": conversation_id})
                connection.execute(text("DELETE FROM rag_runs WHERE id=:id"), {"id": run_id})
                connection.execute(text("DELETE FROM conversations WHERE id=:id"), {"id": conversation_id})
        _purge(repository, kb["id"])

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
