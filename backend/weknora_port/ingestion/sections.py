"""Source-validated, page-local MinerU section segmentation.

Headers are index context only; extracted source text and its Unicode offsets
remain unchanged. No title is inferred from prose, captions, or nearby pages.
"""
from collections.abc import Mapping
from dataclasses import replace
import hashlib


def _invalid():
    raise ValueError("MINERU_SECTION_INVALID")


def validated_sections(parsed, request):
    sections = parsed.metadata.get("mineru_sections")
    identity = parsed.metadata.get("mineru_source")
    if not isinstance(sections, list) or not isinstance(identity, Mapping):
        _invalid()
    if (identity.get("source_version") != request.source_version
            or identity.get("document_id") != request.document_id
            or identity.get("source_sha256") != hashlib.sha256(request.data).hexdigest()
            or identity.get("derived_text_sha256") != hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()):
        _invalid()
    locators = parsed.metadata.get("source_locators", ())
    if not isinstance(locators, (list, tuple)):
        _invalid()
    pages = {}
    titles = []
    for locator in locators:
        if not isinstance(locator, Mapping):
            _invalid()
        a, b, page = locator.get("start"), locator.get("end"), locator.get("page")
        if (type(a) is not int or type(b) is not int or type(page) is not int
                or not 0 <= a < b <= len(parsed.content)
                or locator.get("quote") != parsed.content[a:b]
                or page - 1 not in identity.get("page_indices", ())):
            _invalid()
        proof = locator.get("raw_evidence", {})
        if (not isinstance(proof, Mapping) or proof.get("page_idx") != page - 1 or proof.get("middle") != identity.get("middle")
                or proof.get("source_sha256") != identity.get("source_sha256")):
            _invalid()
        pages.setdefault(page, []).append(locator)
        if proof.get("block_type") in {"doc_title", "paragraph_title"}:
            titles.append(locator)
    ranges = sorted((min(x["start"] for x in values), max(x["end"] for x in values), page)
                    for page, values in pages.items())
    if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
        _invalid()
    result = []
    for section in sections:
        if not isinstance(section, Mapping):
            _invalid()
        a, b = section.get("start"), section.get("end")
        hs, he = section.get("heading_start"), section.get("heading_end")
        page = section.get("page_start")
        heading = section.get("heading")
        if (any(type(x) is not int for x in (a, b, hs, he, page))
                or not 0 <= a == hs < he <= b <= len(parsed.content)
                or not isinstance(heading, str) or not heading.strip()
                or parsed.content[hs:he] != heading
                or section.get("heading_path") != [heading]
                or type(section.get("level")) is not int or not 0 <= section["level"] <= 9
                or section.get("page_end") != page or page not in pages
                or section.get("source_version") != identity.get("source_version")
                or section.get("source_sha256") != identity.get("source_sha256")
                or section.get("middle") != identity.get("middle")):
            _invalid()
        same_page = pages[page]
        if a < min(x["start"] for x in same_page) or b > max(x["end"] for x in same_page):
            _invalid()
        matching = [x for x in titles if x["page"] == page and x["start"] <= hs < he <= x["end"]
                    and x["raw_evidence"].get("block_path") == section.get("block_path")
                    and x["raw_evidence"].get("bbox") == section.get("bbox")]
        if len(matching) != 1 or any(hs < x["start"] < b for x in titles if x["page"] == page):
            _invalid()
        result.append(dict(section))
    result.sort(key=lambda item: item["start"])
    if any(left["end"] > right["start"] for left, right in zip(result, result[1:])):
        _invalid()
    return result, ranges


def ingest_section_rows(parsed, request, config):
    """Reuse the existing splitter inside proven boundaries, then rebase IDs."""
    from .core import _chunk_record, split, split_parent_child

    sections, pages = validated_sections(parsed, request)
    boundaries = {0, len(parsed.content)}
    for start, end, _page in pages:
        boundaries.update((start, end))
    for section in sections:
        boundaries.update((section["start"], section["end"]))
    boundaries = sorted(boundaries)
    rows = []
    parent_seq = child_seq = 0
    for start, end in zip(boundaries, boundaries[1:]):
        body = parsed.content[start:end]
        if not body.strip():
            continue
        selected = next((item for item in sections if item["start"] <= start < end <= item["end"]), None)
        header = selected["heading"] if selected else ""

        def record(chunk, role, seq, parent_id=None):
            absolute = replace(chunk, start=chunk.start + start, end=chunk.end + start,
                               seq=seq, context_header=header)
            row = _chunk_record(request, absolute, role, parent_id)
            if selected:
                row = replace(row, metadata={**row.metadata, "mineru_section": dict(selected)})
            return row

        if config.enable_parent_child:
            group = split_parent_child(body, config)
            parent_ids = []
            for parent in group.parents:
                row = record(parent, "parent", parent_seq)
                rows.append(row)
                parent_ids.append(row.source_id)
                parent_seq += 1
            for child in group.children:
                if not child.chunk.content.strip():
                    continue
                parent_id = parent_ids[child.parent_index] if child.parent_index >= 0 else None
                rows.append(record(child.chunk, "child", child_seq, parent_id))
                child_seq += 1
        else:
            for chunk in split(body, config):
                if not chunk.content.strip():
                    continue
                rows.append(record(chunk, "child", child_seq))
                child_seq += 1
    return tuple(rows)
