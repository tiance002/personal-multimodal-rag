from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


class RetryStore:
    def __init__(self) -> None:
        self.processed: list[str] = []

    def retry_job(self, job_id: str) -> dict[str, object]:
        return {"id": job_id, "status": "queued", "stage": "queued", "attempts": 1, "max_attempts": 3, "progress": 0, "error_code": None}

    def process_job(self, job_id: str) -> None:
        self.processed.append(job_id)


def test_retry_leaves_queued_job_for_worker_when_inline_ingestion_is_disabled() -> None:
    store = RetryStore()
    settings = Settings(inline_ingestion_enabled=False)
    with TestClient(create_app(settings, container=SimpleNamespace(settings=settings, store=store))) as client:
        response = client.post("/api/v1/ingestion-jobs/job-1/retry")

    assert response.status_code == 202
    assert response.json()["data"]["status"] == "queued"
    assert store.processed == []


def test_retry_runs_inline_only_when_inline_ingestion_is_enabled() -> None:
    store = RetryStore()
    settings = Settings(inline_ingestion_enabled=True)
    with TestClient(create_app(settings, container=SimpleNamespace(settings=settings, store=store))) as client:
        response = client.post("/api/v1/ingestion-jobs/job-1/retry")

    assert response.status_code == 202
    assert store.processed == ["job-1"]
