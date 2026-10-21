"""Offline layout-bound segmentation, without OCR, providers, or storage."""
import copy
import hashlib
import io
import json
import zipfile
from dataclasses import replace

import fitz
import pytest

from backend.weknora_port.ingestion.core import DocumentRequest, ParserRegistry, ingest_document
from backend.weknora_port.ingestion.mineru import ENGINE, normalize_archive
from backend.weknora_port.storage.chunks import DocumentScope, prepare_chunks


def fixture(*, parent_child=False):
    pdf = fitz.open()
    for _ in range(4):
        pdf.new_page()
    data = pdf.tobytes()
    pdf.close()
    request = DocumentRequest("slides.pdf", data, "owner", "kb", "doc", "version", parser_engine=ENGINE,
        kb_chunking_config={"chunk_size": 1000, "chunk_overlap": 8,
            "enable_parent_child": parent_child, "parent_chunk_size": 1000, "child_chunk_size": 64})
    def block(kind, text, index):
        return {"type": kind, "index": index, "bbox": [0.1, 0.1, 0.9, 0.2], "content": text}
    raw = {"schema": "docvortex.middle", "schema_version": "2.0",
        "metadata": {"producer": {"name": "mineru", "version": "4.0.12"}},
        "extensions": {"mineru": {"tier": "basic", "parse_mode": "ocr"}},
        "is_full_document": False, "pages": [
            {"page_idx": 0, "blocks": [block("text", "Untitled introduction😀", 0),
                block("doc_title", "Coding time!", 1),
                block("text", "Kaggle documented exercise😀. " * 10, 2),
                block("paragraph_title", "Second section", 3), block("text", "Other task", 4)]},
            {"page_idx": 2, "blocks": [block("text", "Unrelated next page", 0),
                block("image_caption", "Caption must not become a section", 1),
                block("doc_title", "Coding time!", 2), block("text", "Different repeated heading", 3)]}]}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("middle_json.json", json.dumps(raw).encode())
    parsed = normalize_archive(request, archive.getvalue(),
        {"source_sha256": hashlib.sha256(data).hexdigest()}, allow_partial=True).parsed
    # Build explicit adapter contract independently of adapter section generation.
    locators = parsed.metadata["source_locators"]
    sections = []
    for index, locator in enumerate(locators):
        proof = locator["raw_evidence"]
        if proof["block_type"] not in {"doc_title", "paragraph_title"}:
            continue
        next_title = next((item["start"] for item in locators[index + 1:]
            if item["page"] == locator["page"] and item["raw_evidence"]["block_type"] in
            {"doc_title", "paragraph_title"}), None)
        end = next_title if next_title is not None else max(x["end"] for x in locators if x["page"] == locator["page"])
        heading = locator["quote"]
        sections.append({"start": locator["start"], "end": end, "heading": heading,
            "heading_path": [heading], "level": 1, "page_start": locator["page"], "page_end": locator["page"],
            "heading_start": locator["start"], "heading_end": locator["end"],
            "block_path": proof["block_path"], "bbox": proof["bbox"], "middle": proof["middle"],
            "source_sha256": proof["source_sha256"], "source_version": request.source_version})
    return request, replace(parsed, metadata={**parsed.metadata, "mineru_sections": sections})


def ingest(request, parsed):
    registry = ParserRegistry()
    registry.register(ENGINE, (".pdf",), lambda _: parsed)
    return ingest_document(request, registry)


@pytest.mark.parametrize("parent_child", [False, True])
def test_layout_sections_preserve_unicode_source_identity_and_parent_links(parent_child):
    request, parsed = fixture(parent_child=parent_child)
    original = parsed.content
    result = ingest(request, parsed)
    assert result.parsed.content == original
    assert len({row.source_id for row in result.chunks}) == len(result.chunks)
    parents = {row.source_id: row for row in result.chunks if row.role == "parent"}
    children = [row for row in result.chunks if row.role == "child"]
    assert [row.seq for row in children] == list(range(len(children)))
    covered = {offset for row in children for offset in range(row.start, row.end)}
    assert all(offset in covered for offset, char in enumerate(original) if not char.isspace())
    if parent_child:
        assert parents and any(row.parent_source_id for row in children)
    for row in result.chunks:
        assert row.content == original[row.start:row.end]
        assert row.source_version == request.source_version
        assert len(row.metadata["page_numbers"]) == 1
        section = row.metadata.get("mineru_section")
        if section:
            assert section["start"] <= row.start < row.end <= section["end"]
            assert row.context_header == section["heading"]
        else:
            assert row.context_header == ""
        if row.parent_source_id:
            parent = parents[row.parent_source_id]
            assert parent.start <= row.start < row.end <= parent.end
            assert parent.context_header == row.context_header
        for locator in row.metadata["source_locators"]:
            assert locator["quote"] == original[locator["start"]:locator["end"]]
    assert any("Kaggle" in row.content and row.context_header == "Coding time!" for row in children)
    assert any("Unrelated next page" in row.content and not row.context_header for row in children)
    assert any("Caption must" in row.content and not row.context_header for row in children)
    assert all(2 not in row.metadata["page_numbers"] for row in result.chunks)
    # Repeated headings remain distinct page-local proofs, never a fabricated tree.
    repeated = [row for row in children if row.context_header == "Coding time!"]
    assert {row.metadata["mineru_section"]["page_start"] for row in repeated} == {1, 3}
    plan = prepare_chunks(DocumentScope(1, "owner", "kb", "doc", "version"), result.chunks)
    assert all(row.content == original[row.start_at:row.end_at] for row in plan.persisted)
    assert any("Coding time!\n\n" in index.content and "Kaggle" in index.content for index in plan.indexed)


@pytest.mark.parametrize("mutation", ["source_version", "source_sha256", "middle", "heading", "bbox",
    "page_end", "cross_title", "cross_page", "missing_page", "caption", "heading_path", "locator_quote", "identity"])
def test_tampered_or_unproven_section_fails_closed(mutation):
    request, parsed = fixture()
    metadata = copy.deepcopy(parsed.metadata)
    section = metadata["mineru_sections"][0]
    if mutation == "cross_title":
        section["end"] = metadata["mineru_sections"][1]["end"]
    elif mutation == "cross_page":
        section["end"] = len(parsed.content)
    elif mutation == "missing_page":
        section["page_start"] = section["page_end"] = 2
    elif mutation == "caption":
        title = next(item for item in metadata["source_locators"] if item["start"] == section["start"])
        title["raw_evidence"]["block_type"] = "image_caption"
    elif mutation == "locator_quote":
        metadata["source_locators"][0]["quote"] = "forged"
    elif mutation == "identity":
        metadata["mineru_source"]["document_id"] = "other-doc"
    else:
        section[mutation] = {"middle": {}, "bbox": [0, 0, 1, 1], "page_end": 3,
            "heading_path": ["Invented parent", section["heading"]]}.get(mutation, "forged")
    with pytest.raises(ValueError, match="MINERU_SECTION_INVALID"):
        ingest(request, replace(parsed, metadata=metadata))


def test_missing_section_metadata_retains_existing_splitter_behavior():
    request, parsed = fixture()
    metadata = dict(parsed.metadata)
    metadata.pop("mineru_sections")
    result = ingest(request, replace(parsed, metadata=metadata))
    assert len(result.chunks) == 1
    assert result.chunks[0].content == parsed.content
    assert result.chunks[0].context_header == ""
