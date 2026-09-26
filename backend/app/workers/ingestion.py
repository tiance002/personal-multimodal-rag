from __future__ import annotations

import argparse
import time

from sqlalchemy import create_engine, text

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.config import Settings


def build_repository(settings: Settings) -> PostgresKnowledgeRepository:
    return PostgresKnowledgeRepository(
        create_engine(settings.database_url, pool_pre_ping=True),
        ContentAddressedStorage(settings.storage_root),
        embedding_provider=OllamaGateway(
            settings.ollama_base_url,
            settings.ollama_chat_model,
            settings.ollama_embedding_model,
        ),
    )


def claim_job(repository: PostgresKnowledgeRepository) -> str | None:
    with repository.engine.begin() as connection:
        row = connection.execute(
            text("""
                SELECT id
                FROM ingestion_jobs
                WHERE status='queued' AND attempts < max_attempts
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """)
        ).mappings().first()
        return str(row["id"]) if row else None


def run_once(repository: PostgresKnowledgeRepository) -> dict[str, object] | None:
    job_id = claim_job(repository)
    if job_id is None:
        return None
    return repository.process_job(job_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process durable personal-rag ingestion jobs")
    parser.add_argument("--once", action="store_true", help="process at most one queued job")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    settings = Settings.from_env()
    repository = build_repository(settings)
    while True:
        result = run_once(repository)
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

