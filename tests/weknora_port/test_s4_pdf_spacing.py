"""Native PDF glyph spacing regressions; no OCR, services or model calls."""
from __future__ import annotations

import pytest

from backend.weknora_port.ingestion import upstream_pdf_parser as parser


def _pdf_with_lines(lines):
    """Tiny native PDF fixture using the standard built-in Helvetica font."""
    operators = [b"BT /F1 12 Tf 72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            operators.append(b"0 -20 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operators.append(b"(" + escaped.encode("ascii") + b") Tj")
    content = b"\n".join(operators + [b"ET"])
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


@pytest.mark.parametrize("date", ["2026-10-12", "2031-01-12", "1991-11-21"])
def test_native_date_is_not_split_by_narrow_glyph_ink_bounds(date):
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    with pdfium.PdfDocument(_pdf_with_lines([f"Inspection date: {date}", "Unit: NP-204"])) as pdf:
        page = pdf[0]
        try:
            layout = parser._extract_layout_text(page, raw)
            assert f"Inspection date: {date}" in layout
            assert "Unit: NP-204" in layout
        finally:
            page.close()


def _glyph(ch, x0, x1, **spacing):
    return {"ch": ch, "x0": x0, "x1": x1, "y0": 0, "y1": 10, **spacing}


def test_font_cell_spacing_retains_real_gaps_and_explicit_spaces():
    adjacent = [_glyph("1", 1, 4, spacing_x0=0, spacing_x1=6),
                _glyph("0", 7, 12, spacing_x0=6, spacing_x1=13)]
    assert parser._join_line_glyphs(adjacent) == "10"
    separated = [adjacent[0], _glyph("0", 17, 22, spacing_x0=16, spacing_x1=23)]
    assert parser._join_line_glyphs(separated) == "1 0"
    explicit = [adjacent[0], _glyph(" ", 6, 9), _glyph("0", 9, 14)]
    assert parser._join_line_glyphs(explicit) == "1 0"


def test_legacy_glyphs_without_font_cells_keep_geometric_fallback():
    assert parser._join_line_glyphs([_glyph("A", 0, 5), _glyph("B", 10, 15)]) == "A B"


def test_normal_parser_and_chunk_offsets_preserve_text_and_source_identity():
    from backend.weknora_port.ingestion import DocumentRequest, ingest_document

    result = ingest_document(DocumentRequest(
        "native.pdf", _pdf_with_lines(["Inspection date: 2031-01-12", "Unit: NP-204"]),
        owner_id="owner", knowledge_base_id="kb", document_id="doc", source_version="version",
        kb_chunking_config={"enable_parent_child": True, "parent_chunk_size": 4096, "child_chunk_size": 384}))
    assert result.parser_engine == "pdf" and result.parsed.ok
    assert "Inspection date: 2031-01-12" in result.parsed.content
    assert result.chunks
    for chunk in result.chunks:
        assert (chunk.owner_id, chunk.knowledge_base_id, chunk.document_id, chunk.source_version) == (
            "owner", "kb", "doc", "version")
        assert chunk.content == result.parsed.content[chunk.start:chunk.end]


@pytest.mark.parametrize("loose", [None, (float("nan"), 0, 5, 10), (6, 0, 5, 10)])
def test_unavailable_or_invalid_font_cells_do_not_discard_glyphs(loose):
    class TextPage:
        def count_chars(self): return 1
        def get_text_range(self, index, count): return "1"
        def get_charbox(self, index, loose=False):
            if not loose: return (1, 0, 4, 10)
            if cells is None: raise RuntimeError("font bounds unavailable")
            return cells

    class Page:
        def get_size(self): return (100, 100)

    cells = loose
    chars, _ = parser._page_chars(TextPage(), Page(), None)
    assert len(chars) == 1 and chars[0]["ch"] == "1"
    assert parser._join_line_glyphs(chars) == "1"
