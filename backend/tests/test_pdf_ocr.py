from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from backend.app.adapters.parsers import ImageParser, PdfParser
from backend.app.domain.chunking import chunk_document


TESSDATA = Path("var/tessdata")


def _require_ocr_data() -> None:
    if not all((TESSDATA / f"{language}.traineddata").is_file() for language in ("eng", "chi_sim")):
        pytest.skip("local Tesseract eng+chi_sim data unavailable")


def _raster_text() -> bytes:
    document = fitz.open()
    page = document.new_page(width=600, height=200)
    page.insert_text((36, 100), "OCR TEST 1234", fontsize=32)
    raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    document.close()
    return raster


def test_scanned_pdf_ocr_is_derived_from_a_hashed_page_asset(tmp_path):
    _require_ocr_data()
    document = fitz.open()
    page = document.new_page(width=600, height=200)
    page.insert_image(page.rect, stream=_raster_text())
    path = tmp_path / "scan.pdf"
    document.save(path)
    document.close()

    normalized = PdfParser(tessdata=TESSDATA).parse(path, "doc", "version")
    chunks = chunk_document(normalized)

    assert "OCR TEST 1234" in normalized.markdown_content.upper()
    source = next(asset for asset in normalized.assets if asset.asset_type == "scanned_page")
    derived = next(asset for asset in normalized.assets if asset.asset_type == "ocr_text")
    assert source.source_bytes and source.source_locator["sha256"]
    assert derived.derived_from_asset_id == source.asset_id
    assert derived.text_content and "OCR TEST" in derived.text_content.upper()
    assert chunks[0].chunk_type == "image_ocr"
    assert chunks[0].source_locator.page == 1
    assert chunks[0].source_locator.asset_id == source.asset_id


def test_image_parser_indexes_ocr_as_derived_text(tmp_path):
    _require_ocr_data()
    path = tmp_path / "image.png"
    path.write_bytes(_raster_text())

    normalized = ImageParser(tessdata=TESSDATA).parse(path, "doc", "version")

    assert "OCR TEST 1234" in normalized.markdown_content.upper()
    assert normalized.assets[0].asset_type == "source_image"
    assert normalized.assets[1].asset_type == "ocr_text"
    assert normalized.assets[1].derived_from_asset_id == normalized.assets[0].asset_id
    assert chunk_document(normalized)[0].chunk_type == "image_ocr"


def test_pdf_with_text_preserves_embedded_image_as_a_hashed_asset(tmp_path):
    _require_ocr_data()
    document = fitz.open()
    page = document.new_page(width=600, height=200)
    page.insert_text((36, 36), "Visible text layer")
    page.insert_image(fitz.Rect(50, 60, 150, 160), stream=_raster_text())
    path = tmp_path / "mixed.pdf"
    document.save(path)
    document.close()

    normalized = PdfParser(tessdata=TESSDATA).parse(path, "doc", "version")

    assert "Visible text layer" in normalized.markdown_content
    assert any(asset.asset_type == "source_image" and asset.source_bytes and asset.source_locator["sha256"] for asset in normalized.assets)
    assert "OCR TEST 1234" in normalized.markdown_content.upper()
    image_chunk = next(chunk for chunk in chunk_document(normalized) if chunk.chunk_type == "image_ocr")
    assert image_chunk.source_locator.page == 1
    assert image_chunk.source_locator.asset_id


def test_missing_local_ocr_data_keeps_source_but_does_not_create_text(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(_raster_text())

    normalized = ImageParser(tessdata=tmp_path / "missing").parse(path, "doc", "version")

    assert normalized.markdown_content == ""
    assert normalized.assets[0].asset_type == "source_image"
    assert normalized.assets[1].status == "failed"
    assert normalized.assets[1].error_code == "OCR_UNAVAILABLE"
    assert chunk_document(normalized) == []
