from __future__ import annotations

from typing import Any

from backend.app.workers.ingestion import claim_job, run_once


class RecordingRepository:
    def __init__(self) -> None:
        self.claim_calls: list[dict[str, Any]] = []
        self.process_calls: list[dict[str, Any]] = []

    def claim_job(self, *, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        self.claim_calls.append({"worker_id": worker_id, "lease_seconds": lease_seconds})
        return {
            "id": "job-1",
            "worker_id": worker_id,
            "claim_token": "claim-1",
            "attempts": 1,
        }

    def process_job(self, job_id: str, *, worker_id: str, claim_token: str, lease_seconds: int) -> dict[str, Any]:
        self.process_calls.append({"job_id": job_id, "worker_id": worker_id, "claim_token": claim_token})
        return {"id": job_id, "status": "succeeded"}


def test_claim_job_delegates_atomic_claim_with_worker_and_lease() -> None:
    repository = RecordingRepository()

    claim = claim_job(repository, worker_id="worker-1", lease_seconds=17)

    assert claim == {"id": "job-1", "worker_id": "worker-1", "claim_token": "claim-1", "attempts": 1}
    assert repository.claim_calls == [{"worker_id": "worker-1", "lease_seconds": 17}]


def test_run_once_fences_processing_with_claim_identity() -> None:
    repository = RecordingRepository()

    result = run_once(repository, worker_id="worker-1", lease_seconds=17)

    assert result == {"id": "job-1", "status": "succeeded"}
    assert repository.process_calls == [{"job_id": "job-1", "worker_id": "worker-1", "claim_token": "claim-1"}]
