from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


def main() -> int:
    suffix = uuid.uuid4().hex[:10]
    settings = Settings(
        database_url="postgresql+psycopg://rag:rag@127.0.0.1:55432/rag",
        storage_root=Path(f"var/smoke-api-storage-{suffix}"),
        local_query_enabled=False,
    )
    app = create_app(settings)
    report: dict[str, object] = {"status": "FAIL", "checks": {}}
    with TestClient(app) as client:
        kb_response = client.post("/api/v1/knowledge-bases", json={"name": f"api-smoke-{suffix}", "description": "api smoke"})
        kb_response.raise_for_status()
        kb = kb_response.json()["data"]
        upload_response = client.post(
            f"/api/v1/knowledge-bases/{kb['id']}/documents",
            files={"file": ("api.md", "# API\n\n本地 API 引用证据。", "text/markdown")},
        )
        upload_response.raise_for_status()
        receipt = upload_response.json()["data"]
        job = client.get(f"/api/v1/ingestion-jobs/{receipt['job_id']}").json()["data"]
        conversation = client.post("/api/v1/conversations", json={"knowledge_base_scope": [kb["id"]], "title": "API smoke"}).json()["data"]
        message_response = client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "API 引用"})
        message_response.raise_for_status()
        message = message_response.json()["data"]
        events = client.get(f"/api/v1/runs/{message['run_id']}/events")
        citation = client.get(f"/api/v1/runs/{message['run_id']}/citations/E1")
        report["checks"] = {
            "health": client.get("/healthz").status_code,
            "upload_status": receipt["status"],
            "job_status": job["status"],
            "message_status": message_response.status_code,
            "run_id": message["run_id"],
            "event_status": events.status_code,
            "event_content_type": events.headers.get("content-type"),
            "citation_status": citation.status_code,
            "citation": citation.json().get("data") if citation.status_code == 200 else citation.text,
        }
        report["status"] = "PASS" if job["status"] == "succeeded" and message_response.status_code == 201 and events.status_code == 200 and citation.status_code == 200 else "FAIL"
        app.state.container.store.delete_conversation(conversation["id"])
        app.state.container.store.delete_knowledge_base(kb["id"])
    report_path = Path("var/reports/smoke-api.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
