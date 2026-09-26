import io
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.config import Settings


def test_postgres_records_actual_parser_chunker_and_strategy(tmp_path):
    engine = create_engine(os.getenv("RAG_DATABASE_URL", Settings().database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL unavailable: {type(exc).__name__}")
    repository = PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path))
    kb = repository.create_knowledge_base(f"chunk-strategy-{tmp_path.name}")
    try:
        stored = repository.storage.put_stream(io.BytesIO(b"# Alpha\nA long section\n# Beta\nAnother long section"))
        receipt = repository.create_upload(kb["id"], "strategy.md", "text/markdown", stored)
        job = repository.process_job(receipt["job_id"])
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT parser_version,chunker_version,chunk_strategy FROM document_versions WHERE id=:id"),
                {"id": receipt["version_id"]},
            ).mappings().one()
        assert job["status"] == "succeeded"
        assert dict(row) == {
            "parser_version": "text/v1",
            "chunker_version": "adaptive/v1",
            "chunk_strategy": "heading_recursive",
        }
    finally:
        repository.delete_knowledge_base(kb["id"])
