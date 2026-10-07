from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
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
    return PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path / "storage"))


def _blank_pdf() -> bytes:
    document = fitz.open()
    document.new_page()
    result = document.tobytes()
    document.close()
    return result


def test_explicit_version_targets_document_even_when_file_name_changes(tmp_path: Path) -> None:
    repository = _repository_or_skip(tmp_path)
    suffix = tmp_path.name
    knowledge_base = repository.create_knowledge_base(f"version-target-{suffix}")
    try:
        original = repository.create_upload(
            knowledge_base["id"],
            "original.md",
            "text/markdown",
            repository.storage.put_stream(io.BytesIO(b"original")),
        )
        replacement = repository.create_version(
            original["document_id"],
            "renamed.md",
            "text/markdown",
            repository.storage.put_stream(io.BytesIO(b"replacement")),
        )

        assert replacement["document_id"] == original["document_id"]
        assert replacement["version_id"] != original["version_id"]
    finally:
        repository.delete_knowledge_base(knowledge_base["id"])


def test_concurrent_versions_are_serialized_by_document_lock(tmp_path: Path) -> None:
    repository = _repository_or_skip(tmp_path)
    suffix = tmp_path.name
    knowledge_base = repository.create_knowledge_base(f"version-lock-{suffix}")
    try:
        original = repository.create_upload(
            knowledge_base["id"],
            "original.md",
            "text/markdown",
            repository.storage.put_stream(io.BytesIO(b"original")),
        )

        def create(index: int):
            return repository.create_version(
                original["document_id"],
                f"revision-{index}.md",
                "text/markdown",
                repository.storage.put_stream(io.BytesIO(f"revision {index}".encode("utf-8"))),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            versions = list(executor.map(create, (1, 2)))

        assert {version["version_no"] for version in versions} == {2, 3}
    finally:
        repository.delete_knowledge_base(knowledge_base["id"])


@pytest.mark.parametrize("ocr_error", ["OCR_EMPTY", "OCR_UNAVAILABLE"])
def test_empty_text_and_textless_pdf_never_become_ready(tmp_path: Path, monkeypatch, ocr_error: str) -> None:
    from backend.app.adapters.parsers import PdfParser

    # Explicit provider outcomes: this persistence boundary must not depend on
    # installed OCR languages or turn either failed outcome into an active version.
    monkeypatch.setattr(PdfParser, "_ocr", lambda self, page: ("", ocr_error))
    repository = _repository_or_skip(tmp_path)
    suffix = tmp_path.name
    knowledge_base = repository.create_knowledge_base(f"empty-{suffix}")
    try:
        empty_text = repository.create_upload(
            knowledge_base["id"],
            "empty.txt",
            "text/plain",
            repository.storage.put_stream(io.BytesIO(b"\n  \n")),
        )
        blank_pdf = repository.create_upload(
            knowledge_base["id"],
            "scan.pdf",
            "application/pdf",
            repository.storage.put_stream(io.BytesIO(_blank_pdf())),
        )

        empty_result = repository.process_job(empty_text["job_id"])
        pdf_result = repository.process_job(blank_pdf["job_id"])

        assert empty_result["status"] == "failed"
        assert empty_result["error_code"] == "EMPTY_TEXT"
        assert pdf_result["status"] == "failed"
        assert pdf_result["error_code"] == ocr_error
        assert (repository.get_document(empty_text["document_id"]) or {}).get("active_version_id") is None
        assert (repository.get_document(blank_pdf["document_id"]) or {}).get("active_version_id") is None
    finally:
        repository.delete_knowledge_base(knowledge_base["id"])
