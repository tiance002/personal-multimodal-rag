from __future__ import annotations

import io
import json
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.ports.providers import EmbeddingResult


class DeterministicLocalModel(OllamaGateway):
    def __init__(self) -> None:
        super().__init__("http://unused", "fake-chat", "fake-embedding")

    def embed(self, texts, timeout_seconds):
        return EmbeddingResult([[0.0] * 1024 for _ in texts], self.embedding_model, 1024, 0.1)

    def answer(self, prompt, timeout_seconds):
        return "基于已验证证据 E1 的智能模式回答。"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser_database = "postgresql+psycopg://rag:rag@127.0.0.1:55432/rag"
    suffix = uuid.uuid4().hex[:10]
    repository = PostgresKnowledgeRepository.from_url(parser_database, Path(f"var/smoke-m4-storage-{suffix}"))
    kb = repository.create_knowledge_base(f"m4-smoke-{suffix}", "temporary M4 verification")
    conversation_id = None
    try:
        stored = repository.storage.put_stream(io.BytesIO(b"# Agent\n\nLocal evidence for smart mode."))
        receipt = repository.create_upload(kb["id"], "agent.md", "text/markdown", stored)
        job = repository.process_job(receipt["job_id"])
        app = create_app(Settings(database_url=parser_database, storage_root=Path(f"var/smoke-m4-api-storage-{suffix}"), local_query_enabled=False))
        app.state.store = repository
        app.state.ollama = DeterministicLocalModel()
        with TestClient(app) as client:
            conversation = client.post("/api/v1/conversations", json={"knowledge_base_scope": [kb["id"]], "title": "M4 smoke"}).json()["data"]
            conversation_id = conversation["id"]
            response = client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "Local evidence", "mode": "smart"})
            payload = response.json().get("data", {})
            citation = client.get(f"/api/v1/runs/{payload.get('run_id')}/citations/E1")
        with repository.engine.connect() as connection:
            agent_status = connection.execute(text("SELECT status FROM agent_runs WHERE id=:id"), {"id": payload.get("run_id")}).scalar()
            step_count = connection.execute(text("SELECT count(*) FROM agent_steps WHERE agent_run_id=:id"), {"id": payload.get("run_id")}).scalar_one()
        report = {"status": "PASS" if job.get("status") == "succeeded" and response.status_code == 201 and payload.get("trace", {}).get("mode") == "smart" and agent_status == "completed" and step_count == 1 and citation.status_code == 200 else "FAIL", "provider_mode": "deterministic local test double", "checks": {"job": job, "message_status": response.status_code, "trace": payload.get("trace"), "agent_status": agent_status, "step_count": step_count, "citation_status": citation.status_code}}
    finally:
        if conversation_id:
            repository.delete_conversation(conversation_id)
        repository.delete_knowledge_base(kb["id"])
    path = Path("var/reports/smoke-m4.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
