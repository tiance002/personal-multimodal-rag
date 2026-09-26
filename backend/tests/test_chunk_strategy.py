from backend.app.domain.chunking import chunk_document, profile_document
from backend.app.domain.models import DocumentSection, NormalizedDocument


def _document(text: str, media_type: str, sections: list[DocumentSection] | None = None) -> NormalizedDocument:
    return NormalizedDocument(
        document_id="doc", version_id="v1", title="example", media_type=media_type,
        markdown_content=text, sections=sections or [], content_sha256="sha", parser_version="test/v1",
    )


def test_clear_markdown_headings_keep_section_boundaries_and_strategy():
    text = "# Alpha\n" + "A" * 80 + "\n# Beta\n" + "B" * 80
    boundary = text.index("# Beta")
    sections = [
        DocumentSection(section_id="a", heading="Alpha", heading_path=("Alpha",), level=1, start=0, end=boundary),
        DocumentSection(section_id="b", heading="Beta", heading_path=("Beta",), level=1, start=boundary, end=len(text)),
    ]
    document = _document(text, "text/markdown", sections)

    assert profile_document(document, max_chars=120).strategy == "heading_recursive"
    chunks = chunk_document(document, max_chars=120, overlap=0)
    assert [chunk.heading_path for chunk in chunks] == [("Alpha",), ("Beta",)]


def test_short_markdown_sections_are_merged_without_losing_exact_spans():
    text = "# A\na\n# B\nb\n# C\nc\n# D\nd\n"
    starts = [text.index(f"# {name}") for name in "ABCD"]
    sections = [
        DocumentSection(section_id=name, heading=name, heading_path=(name,), level=1, start=start,
                        end=starts[index + 1] if index + 1 < len(starts) else len(text))
        for index, (name, start) in enumerate(zip("ABCD", starts, strict=True))
    ]
    document = _document(text, "text/markdown", sections)

    assert profile_document(document, max_chars=100).strategy == "merged_sections"
    chunks = chunk_document(document, max_chars=100, overlap=0)
    assert len(chunks) == 1
    assert chunks[0].content == text


def test_plain_text_uses_paragraph_boundary_when_possible():
    text = "First paragraph has several words.\n\nSecond paragraph has more words."
    document = _document(text, "text/plain")

    assert profile_document(document, max_chars=45).strategy == "paragraph_recursive"
    chunks = chunk_document(document, max_chars=45, overlap=0)
    assert chunks[0].content.endswith("\n\n")
    assert all(text[chunk.start:chunk.end] == chunk.content for chunk in chunks)


def test_pdf_strategy_stays_page_scoped():
    text = "page one text\npage two text"
    document = _document(text, "application/pdf", [
        DocumentSection(section_id="p1", heading="Page 1", level=1, start=0, end=13, page_start=1, page_end=1),
        DocumentSection(section_id="p2", heading="Page 2", level=1, start=14, end=len(text), page_start=2, page_end=2),
    ])

    assert profile_document(document, max_chars=100).strategy == "page_recursive"
    assert [chunk.source_locator.page for chunk in chunk_document(document, max_chars=100, overlap=0)] == [1, 2]
