from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.config import Settings


def _repository_or_skip(tmp_path: Path) -> PostgresKnowledgeRepository:
    database_url = os.getenv("RAG_DATABASE_URL", Settings().database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    return PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path))


def test_postgres_claim_is_exclusive_and_stale_token_is_fenced(tmp_path: Path) -> None:
    repository = _repository_or_skip(tmp_path)
    suffix = tmp_path.name
    knowledge_base = repository.create_knowledge_base(f"lease-{suffix}")
    stored = repository.storage.put_stream(io.BytesIO(b"lease test"))
    receipt = repository.create_upload(knowledge_base["id"], f"{suffix}.txt", "text/plain", stored)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(repository.claim_job, worker_id="worker-a", lease_seconds=60, job_id=receipt["job_id"]),
                executor.submit(repository.claim_job, worker_id="worker-b", lease_seconds=60, job_id=receipt["job_id"]),
            ]
            claims = [future.result() for future in futures]

        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        first = claimed[0]
        assert first["attempts"] == 1

        with repository.engine.begin() as connection:
            connection.execute(
                text("UPDATE ingestion_jobs SET lease_until=clock_timestamp() - INTERVAL '1 second' WHERE id=:id"),
                {"id": receipt["job_id"]},
            )

        second = repository.claim_job(worker_id="worker-c", lease_seconds=60, job_id=receipt["job_id"])

        assert second is not None
        assert second["attempts"] == 2
        assert second["claim_token"] != first["claim_token"]
        assert not repository.update_job_progress(
            receipt["job_id"],
            worker_id=str(first["worker_id"]),
            claim_token=str(first["claim_token"]),
            stage="stale",
            progress=50,
        )
        assert repository.update_job_progress(
            receipt["job_id"],
            worker_id="worker-c",
            claim_token=str(second["claim_token"]),
            stage="owned",
            progress=50,
        )
    finally:
        repository.delete_knowledge_base(knowledge_base["id"])
