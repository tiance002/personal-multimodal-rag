from backend.app.adapters.parsers import _sectioned_text
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import RetrievalItem
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkRecord, DocumentSection, NormalizedDocument, RankedHit


def test_context_freezes_only_complete_chunks_actually_sent_to_model():
    items = [
        RetrievalItem(ChunkRecord("c1", "kb", "d1", "v1", "first evidence", {"start": 0}), RankedHit(chunk_id="c1", rank=1)),
        RetrievalItem(ChunkRecord("c2", "kb", "d2", "v2", "second evidence", {"start": 0}), RankedHit(chunk_id="c2", rank=2)),
    ]
    citations = CitationService(InMemoryCitationStore())

    context, labels = ContextBuilder(max_chars=22).build("run", items, citations)

    assert context == "[E1] first evidence"
    assert labels == ["E1"]
    assert set(citations.snapshots) == {("run", "E1")}


def test_markdown_heading_path_discards_old_sibling():
    text = "# Root\n## Old\ntext\n## New\nnew text\n### Leaf\nleaf text\n"

    sections, _ = _sectioned_text(text, "text/markdown")

    assert [section.heading_path for section in sections] == [
        ("Root",), ("Root", "Old"), ("Root", "New"), ("Root", "New", "Leaf")
    ]


def test_pdf_chunk_locator_keeps_page_number():
    content = "First page text\nSecond page text"
    document = NormalizedDocument(
        document_id="doc", version_id="v", title="PDF", media_type="application/pdf",
        markdown_content=content,
        sections=[
            DocumentSection(section_id="p1", heading="Page 1", level=1, start=0, end=15, page_start=1, page_end=1),
            DocumentSection(section_id="p2", heading="Page 2", level=1, start=16, end=len(content), page_start=2, page_end=2),
        ],
        content_sha256="sha", parser_version="pdf/v1",
    )

    chunks = chunk_document(document, max_chars=1200)

    assert [chunk.source_locator.page for chunk in chunks] == [1, 2]
    assert [chunk.source_locator.kind for chunk in chunks] == ["pdf", "pdf"]
