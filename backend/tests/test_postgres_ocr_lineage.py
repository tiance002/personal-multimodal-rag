from __future__ import annotations

import io
import os
from pathlib import Path

import fitz
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.config import Settings


def _scanned_pdf() -> bytes:
    image_document = fitz.open()
    page = image_document.new_page(width=600, height=200)
    page.insert_text((36, 100), "OCR TEST 1234", fontsize=32)
    raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    image_document.close()
    document = fitz.open()
    page = document.new_page(width=600, height=200)
    page.insert_image(page.rect, stream=raster)
    result = document.tobytes()
    document.close()
    return result


def test_postgres_scanned_pdf_preserves_ocr_lineage_and_readback(tmp_path):
    if not all((Path("var/tessdata") / f"{name}.traineddata").is_file() for name in ("eng", "chi_sim")):
        pytest.skip("local OCR language data unavailable")
    engine = create_engine(os.getenv("RAG_DATABASE_URL", Settings().database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"PostgreSQL unavailable: {type(exc).__name__}")
    repository = PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path / "storage"))
    kb = repository.create_knowledge_base(f"ocr-lineage-{tmp_path.name}")
    try:
        source = repository.storage.put_stream(io.BytesIO(_scanned_pdf()))
        receipt = repository.create_upload(kb["id"], "scan.pdf", "application/pdf", source)
        job = repository.process_job(receipt["job_id"])
        assert job["status"] == "succeeded", job.get("error_code")
        with engine.connect() as connection:
            assets = connection.execute(text("""
                SELECT id,asset_type,storage_key,derived_from_asset_id,text_content,source_locator,status
                FROM document_assets WHERE version_id=:version_id ORDER BY created_at,id
            """), {"version_id": receipt["version_id"]}).mappings().all()
            links = connection.execute(text("""
                SELECT ca.asset_id,c.chunk_type,c.locator
                FROM chunk_assets ca JOIN chunks c ON c.id=ca.chunk_id
                WHERE ca.version_id=:version_id
            """), {"version_id": receipt["version_id"]}).mappings().all()
        original = next(asset for asset in assets if asset["asset_type"] == "scanned_page")
        derived = next(asset for asset in assets if asset["asset_type"] == "ocr_text")
        assert repository.storage.read(original["storage_key"])
        assert original["source_locator"]["sha256"]
        assert derived["derived_from_asset_id"] == original["id"]
        assert "OCR TEST 1234" in derived["text_content"].upper()
        assert any(link["asset_id"] == original["id"] and link["chunk_type"] == "image_ocr"
                   and link["locator"]["page"] == 1
                   and link["locator"]["asset_id"] == str(original["id"]) for link in links)
    finally:
        repository.delete_knowledge_base(kb["id"])
