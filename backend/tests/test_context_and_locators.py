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


def test_context_selection_applies_soft_document_cap_before_stable_backfill():
    items = [
        RetrievalItem(ChunkRecord("a1", "kb", "doc-a", "v1", "A1", {"start": 1}), RankedHit(chunk_id="a1", rank=1)),
        RetrievalItem(ChunkRecord("a2", "kb", "doc-a", "v1", "A2", {"start": 2}), RankedHit(chunk_id="a2", rank=2)),
        RetrievalItem(ChunkRecord("a3", "kb", "doc-a", "v1", "A3", {"start": 3}), RankedHit(chunk_id="a3", rank=3)),
        RetrievalItem(ChunkRecord("b1", "kb", "doc-b", "v1", "B1", {"start": 4}), RankedHit(chunk_id="b1", rank=4)),
        RetrievalItem(ChunkRecord("c1", "kb", "doc-c", "v1", "C1", {"start": 5}), RankedHit(chunk_id="c1", rank=5)),
        RetrievalItem(ChunkRecord("d1", "kb", "doc-d", "v1", "D1", {"start": 6}), RankedHit(chunk_id="d1", rank=6)),
    ]

    selected = ContextBuilder().select(items, max_items=5, max_per_document=2)

    assert [item.chunk.chunk_id for item in selected] == ["a1", "a2", "b1", "c1", "d1"]
    assert [item.hit.rank for item in selected] == [1, 2, 4, 5, 6]


def test_soft_document_cap_backfills_same_document_evidence_when_alone():
    items = [
        RetrievalItem(
            ChunkRecord(f"a{rank}", "kb", "doc-a", "v1", f"evidence {rank}", {"start": rank}),
            RankedHit(chunk_id=f"a{rank}", rank=rank),
        )
        for rank in range(1, 7)
    ]

    selected = ContextBuilder().select(items, max_items=5, max_per_document=2)

    assert [item.chunk.chunk_id for item in selected] == ["a1", "a2", "a3", "a4", "a5"]


def test_soft_document_cap_keeps_citation_locators_and_budget():
    items = [
        RetrievalItem(
            ChunkRecord(chunk_id, "kb", document_id, "v1", content, {"start": start}),
            RankedHit(chunk_id=chunk_id, rank=start),
        )
        for chunk_id, document_id, content, start in (
            ("a1", "doc-a", "A" * 8, 1),
            ("a2", "doc-a", "B" * 8, 2),
            ("a3", "doc-a", "C" * 8, 3),
            ("b1", "doc-b", "D" * 8, 4),
            ("c1", "doc-c", "E" * 8, 5),
        )
    ]
    builder = ContextBuilder(max_chars=48)
    selected = builder.select(items, max_items=3, max_per_document=1)
    citations = CitationService(InMemoryCitationStore())

    context, labels = builder.build("run", selected, citations)
    snapshots = [citations.snapshots[("run", label)] for label in labels]

    assert [snapshot.chunk_id for snapshot in snapshots] == ["a1", "b1", "c1"]
    assert [snapshot.locator["start"] for snapshot in snapshots] == [1, 4, 5]
    assert len(context) <= builder.max_chars


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
