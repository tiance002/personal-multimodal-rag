from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.main import create_app


def test_version_route_keeps_the_path_document_when_filename_changes(tmp_path: Path) -> None:
    settings = Settings(
        database_url=os.getenv("RAG_DATABASE_URL", Settings().database_url),
        storage_root=tmp_path / "storage",
        inline_ingestion_enabled=False,
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL is unavailable: {type(exc).__name__}")
    container = build_container(settings, model=None)
    app = create_app(settings, container=container)

    with TestClient(app) as client:
        knowledge_base = client.post("/api/v1/knowledge-bases", json={"name": f"route-{tmp_path.name}"}).json()["data"]
        original = client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("original.md", "original", "text/markdown")},
        ).json()["data"]
        container.store.process_job(original["job_id"])
        version_response = client.post(
            f"/api/v1/documents/{original['document_id']}/versions",
            files={"file": ("renamed.md", "replacement", "text/markdown")},
        )

    assert version_response.status_code == 202
    assert version_response.json()["data"]["document_id"] == original["document_id"]
    container.store.delete_knowledge_base(knowledge_base["id"])
