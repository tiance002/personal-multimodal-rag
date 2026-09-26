from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository
from backend.app.application.graph import GraphService
from backend.app.bootstrap import build_container
from backend.app.config import Settings


def main() -> int:
    suffix = uuid.uuid4().hex[:10]
    settings = Settings(
        database_url="postgresql+psycopg://rag:rag@127.0.0.1:55432/rag",
        storage_root=Path(f"var/smoke-m3-storage-{suffix}"),
    )
    repository = build_container(settings, model=None).store
    kb = repository.create_knowledge_base(f"m3-smoke-{suffix}", "temporary M3 verification", graph_enabled=True)
    try:
        content = "# Scope\n\n本地知识库边界。\n# Evidence\n\n引用必须可回读。"
        stored = repository.storage.put_stream(io.BytesIO(content.encode("utf-8")))
        receipt = repository.create_upload(kb["id"], "graph.md", "text/markdown", stored)
        job = repository.process_job(receipt["job_id"])
        document = repository.get_document(receipt["document_id"])
        graph_repository = PostgresGraphRepository(repository.engine, repository.storage)
        result = GraphService(graph_repository).build(receipt["document_id"], str(document["active_version_id"]))
        graph = graph_repository.get_document_graph(receipt["document_id"])
        same_version = all(str(edge.get("version_id", document["active_version_id"])) == str(document["active_version_id"]) for edge in graph["edges"])
        report = {"status": "PASS" if job.get("status") == "succeeded" and result.status == "ready" and graph["edges"] and same_version else "FAIL", "checks": {"job": job, "graph_build": {"status": result.status, "nodes": len(result.nodes), "edges": len(result.edges)}, "graph_read": graph, "same_version": same_version}}
    finally:
        repository.delete_knowledge_base(kb["id"])
    path = Path("var/reports/smoke-m3.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
