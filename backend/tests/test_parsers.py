import fitz
from PIL import Image

from backend.app.adapters.parsers import ParserRegistry


def test_text_and_markdown_parsers_preserve_spans(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Heading\n\n事务回滚说明", encoding="utf-8")

    document = ParserRegistry().parse(path, "text/markdown", "doc-1", "ver-1")

    assert document.markdown_content == "# Heading\n\n事务回滚说明"
    assert document.sections[0].heading == "Heading"
    assert document.sections[0].start == 0
    assert document.source_locators[0].kind == "markdown"


def test_pdf_parser_keeps_page_locator(tmp_path):
    path = tmp_path / "one.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "page one source")
    pdf.save(path)
    pdf.close()

    document = ParserRegistry().parse(path, "application/pdf", "doc-1", "ver-1")

    assert "page one source" in document.markdown_content
    assert any(locator.page == 1 for locator in document.source_locators)


def test_image_parser_preserves_original_asset_and_declares_ocr_requirement(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (8, 8), color="white").save(path)

    document = ParserRegistry().parse(path, "image/png", "doc-1", "ver-1")

    assert document.assets[0].asset_type == "source_image"
    assert document.assets[0].storage_key is None
    assert document.markdown_content == ""
