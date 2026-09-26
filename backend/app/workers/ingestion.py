from __future__ import annotations

import argparse
import time
import uuid

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.bootstrap import build_container
from backend.app.config import Settings


def build_repository(settings: Settings) -> PostgresKnowledgeRepository:
    return build_container(settings).store


def claim_job(
    repository: PostgresKnowledgeRepository,
    *,
    worker_id: str | None = None,
    lease_seconds: int = 60,
) -> dict[str, object] | None:
    worker_id = worker_id or f"worker-{uuid.uuid4()}"
    return repository.claim_job(worker_id=worker_id, lease_seconds=lease_seconds)


def run_once(
    repository: PostgresKnowledgeRepository,
    *,
    worker_id: str | None = None,
    lease_seconds: int = 60,
) -> dict[str, object] | None:
    claim = claim_job(repository, worker_id=worker_id, lease_seconds=lease_seconds)
    if claim is None:
        return None
    return repository.process_job(
        str(claim["id"]),
        worker_id=str(claim["worker_id"]),
        claim_token=str(claim["claim_token"]),
        lease_seconds=lease_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Process durable personal-rag ingestion jobs")
    parser.add_argument("--once", action="store_true", help="process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    settings = Settings.from_env()
    repository = build_repository(settings)
    worker_id = f"worker-{uuid.uuid4()}"
    while True:
        result = run_once(repository, worker_id=worker_id, lease_seconds=settings.ingestion_lease_seconds)
        if result is not None:
            print({"job_id": result.get("id"), "status": result.get("status"), "stage": result.get("stage")}, flush=True)
            if args.once:
                return 0 if result.get("status") == "succeeded" else 1
        elif args.once:
            return 0
        else:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
