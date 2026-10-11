"""Self-contained PDF counterexample, real local OCR; no models or database.

Identical 2x2 black bases carry different high-resolution soft masks, including
one invisible object above native text and a repeated placement of one image.
"""
import hashlib
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image
import pytest

from backend.app.adapters.parsers import PdfParser
from backend.app.domain.adaptive_chunking import prepare_document

TESSDATA = Path("var/tessdata")


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def glyph_mask(text):
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=80)
        page.insert_text((10, 55), text, fontsize=36)
        data = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY).tobytes("png")
    with Image.open(BytesIO(data)) as image:
        return png(image.convert("L").point(lambda v: 255 - v))


def counterexample(path):
    with fitz.open() as doc:
        page = doc.new_page(width=640, height=600)
        page.insert_text((30, 35), "Native text evidence must survive image processing without replacement.", fontsize=11)
        first_bbox, second_bbox = fitz.Rect(40, 100, 540, 180), fitz.Rect(40, 190, 540, 270)
        first = page.insert_image(first_bbox, stream=png(Image.new("RGB", (2, 2), "black")),
                                  mask=glyph_mask("ALPHA FIRST 2468"))
        # Different seed prevents insert_image's own cache from reusing a mask.
        # Restore identical base pixels in the actual PDF, retaining mask two.
        second = page.insert_image(second_bbox, stream=png(Image.new("RGB", (2, 2), (1, 0, 0))),
                                   mask=glyph_mask("BETA SECOND 1357"))
        doc.update_stream(second, doc.xref_stream(first))
        repeat_bbox = fitz.Rect(40, 280, 540, 360)
        page.insert_image(repeat_bbox, xref=first)
        invisible_bbox = fitz.Rect(40, 370, 540, 450)
        page.insert_text((50, 410), "LEGITIMATE NATIVE UNDERLAY", fontsize=18)
        invisible = page.insert_image(invisible_bbox, stream=png(Image.new("RGB", (2, 2), (2, 0, 0))),
            mask=png(Image.new("L", (1000, 160), 0)))
        doc.update_stream(invisible, doc.xref_stream(first))
        assert len(page.get_image_rects(first)) == 4  # The old cross-match.
        doc.save(path)
    return [tuple(r) for r in (first_bbox, second_bbox, repeat_bbox, invisible_bbox)]


@pytest.mark.parametrize("geometry", [False, True])
def test_masks_visible_text_transparency_and_exact_occurrences(tmp_path, geometry):
    assert all((TESSDATA / f"{name}.traineddata").is_file() for name in ("eng", "chi_sim")), "Required real local OCR data missing"
    path = tmp_path / "counterexample.pdf"
    expected = counterexample(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    document = PdfParser(tessdata=TESSDATA, collect_caption_geometry=geometry).parse(path, "doc", "version")
    sources = [a for a in document.assets if a.asset_type == "source_image"]
    derived = [a for a in document.assets if a.asset_type == "ocr_text"]
    assert len(sources) == len(derived) == 4
    assert len({a.source_locator["placement_index"] for a in sources}) == 4
    assert [tuple(a.source_locator["placement_bbox"]) for a in sources] == expected
    assert len({a.source_locator["base_image_sha256"] for a in sources}) == 1
    assert len({a.source_locator["soft_mask_sha256"] for a in sources}) == 3
    assert all(a.source_locator["source_sha256"] == before for a in sources)
    assert all(a.source_locator["xref_candidates"] and a.source_locator["xref"] is not None for a in sources)
    assert all(a.source_locator["extraction_version"] == "pdf-embedded-mask-placement/v1" for a in sources)
    assert all(a.source_locator["sha256"] == hashlib.sha256(a.source_bytes).hexdigest() for a in sources)
    assert "ALPHA FIRST 2468" in document.markdown_content.upper()
    assert "BETA SECOND 1357" in document.markdown_content.upper()
    assert "Native text evidence" in document.markdown_content
    assert "LEGITIMATE NATIVE UNDERLAY" in document.markdown_content
    by_source = {a.derived_from_asset_id: a for a in derived}
    assert set(by_source) == {a.asset_id for a in sources}
    assert all(by_source[a.asset_id].status == "ready" for a in sources[:3])
    invisible = sources[3]
    assert invisible.source_locator["extraction_mode"] == "fully_transparent_object_on_white"
    with Image.open(BytesIO(invisible.source_bytes)) as image:
        assert image.getextrema() == ((255, 255), (255, 255), (255, 255))
    assert by_source[invisible.asset_id].text_content is None
    assert by_source[invisible.asset_id].error_code == "OCR_EMPTY"
    assert by_source[invisible.asset_id].status == "failed"
    for locator in document.source_locators:
        assert document.markdown_content[locator.start:locator.end] == locator.quote
        if locator.asset_id:
            source = next(a for a in sources if a.asset_id == locator.asset_id)
            assert tuple(locator.raw_evidence["placement_bbox"]) == tuple(source.source_locator["placement_bbox"])
            assert locator.raw_evidence["soft_mask_sha256"] == source.source_locator["soft_mask_sha256"]
            if geometry:
                assert tuple(locator.bbox) == tuple(source.source_locator["bbox"])
    prepared = prepare_document(document)
    assert prepared.children
    assert all(c.content == document.markdown_content[c.start:c.end] and c.source_locator.quote == c.content
               for c in prepared.parents + prepared.children)
    assert all(c.source_locator.asset_id in by_source for c in prepared.children if c.chunk_type == "image_ocr")
    assert document.parse_status == "partial"  # Existing semantics warning/failure is retained.
    assert document.parser_version.startswith("pdf/v5-image-mask-placement-")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
