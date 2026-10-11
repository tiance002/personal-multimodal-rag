"""SIMULATED per-object API faults on real PDF images; no OCR/model/DB calls."""
from io import BytesIO

import fitz
from PIL import Image
import pytest

from backend.app.adapters.parsers import PdfParser


def png(mode, color):
    stream = BytesIO()
    Image.new(mode, (100, 30), color).save(stream, format="PNG")
    return stream.getvalue()


def three_images(pdf):
    page = pdf.new_page(width=640, height=600)
    page.insert_text((30, 35), "Native evidence survives a malformed adjacent image.", fontsize=12)
    for index, color in enumerate(("red", "green", "blue")):
        page.insert_image(fitz.Rect(40, 100 + index * 70, 240, 160 + index * 70),
                          stream=png("RGB", color), mask=png("L", 127))
    return page


class ResourceFault:
    def __init__(self, pdf, xref, kind):
        self.pdf, self.xref, self.kind = pdf, xref, kind
        self.mask_xref = pdf.extract_image(xref)["smask"]

    def extract_image(self, xref):
        if self.kind == "extract" and xref == self.xref:
            raise ValueError("SIMULATED resource extraction failure")
        result = self.pdf.extract_image(xref)
        if (self.kind == "base" and xref == self.xref or
                self.kind == "smask" and xref == self.mask_xref):
            result = {**result, "image": b"SIMULATED malformed image bytes"}
        return result


class PlacementFault:
    def __init__(self, page, index, kind):
        self.page, self.index, self.kind = page, index, kind
        images = [b for b in page.get_text("dict", flags=fitz.TEXT_PRESERVE_IMAGES)["blocks"] if b["type"] == 1]
        self.target = images[index]

    def __getattr__(self, name):
        return getattr(self.page, name)

    def get_text(self, *args, **kwargs):
        result = self.page.get_text(*args, **kwargs)
        blocks = [dict(b) for b in result["blocks"]]
        target = next(b for b in blocks if b["number"] == self.target["number"])
        if self.kind in {"base", "mask"}:
            target["image" if self.kind == "base" else "mask"] = b"SIMULATED malformed placement bytes"
        elif self.kind == "bbox":
            target["bbox"] = (0, 0, 0, 0)
        elif self.kind == "transform":
            del target["transform"]
        return {**result, "blocks": blocks}

    def get_pixmap(self, *args, **kwargs):
        if self.kind == "render" and tuple(kwargs["clip"]) == tuple(self.target["bbox"]):
            raise ValueError("SIMULATED placement rendering failure")
        return self.page.get_pixmap(*args, **kwargs)


@pytest.mark.parametrize("kind", ["extract", "base", "smask"])
def test_one_bad_resource_keeps_all_readable_placements(kind):
    with fitz.open() as pdf:
        page = three_images(pdf)
        parser = PdfParser(collect_caption_geometry=True)
        warnings = []
        clean = list(parser._embedded_image_occurrences(pdf, page, "original-sha", warnings=warnings))
        assert not warnings and len(clean) == 3
        xref = clean[1][2]["xref"]
        actual = list(parser._embedded_image_occurrences(ResourceFault(pdf, xref, kind), page,
                                                         "original-sha", warnings=warnings))
        assert len(actual) == 3
        assert actual[0] == clean[0] and actual[2] == clean[2]
        assert actual[1][:2] == clean[1][:2]
        assert actual[1][2]["xref"] is None and actual[1][2]["xref_candidates"] == []
        assert {k: v for k, v in actual[1][2].items() if k not in {"xref", "xref_candidates"}} == {
            k: v for k, v in clean[1][2].items() if k not in {"xref", "xref_candidates"}}
        assert len(warnings) == 1
        assert warnings[0].startswith(f"PARTIAL_PARSE:page=1:embedded-image:resource:xref={xref}:error=")


@pytest.mark.parametrize("kind", ["base", "mask", "bbox", "transform", "render"])
def test_one_bad_placement_keeps_both_normal_neighbors(kind):
    with fitz.open() as pdf:
        page = three_images(pdf)
        parser = PdfParser(collect_caption_geometry=True)
        warnings = []
        clean = list(parser._embedded_image_occurrences(pdf, page, "original-sha", warnings=warnings))
        assert not warnings and len(clean) == 3
        faulty = PlacementFault(page, 1, kind)
        actual = list(parser._embedded_image_occurrences(pdf, faulty, "original-sha", warnings=warnings))
        assert actual == [clean[0], clean[2]]
        assert len(warnings) == 1
        assert warnings[0].startswith("PARTIAL_PARSE:page=1:embedded-image:placement:"
                                      f"index={clean[1][2]['placement_index']}:error=")


def test_page_wide_enumeration_failure_keeps_existing_diagnostic(tmp_path, monkeypatch):
    path = tmp_path / "SIMULATED-enumeration-failure.pdf"
    with fitz.open() as pdf:
        three_images(pdf)
        pdf.save(path)

    def failed_enumeration(self, *args, **kwargs):
        raise ValueError("SIMULATED entire resource list unavailable")

    monkeypatch.setattr(fitz.Page, "get_images", failed_enumeration)
    document = PdfParser(tessdata=tmp_path / "missing").parse(path, "doc", "v1")
    assert "Native evidence survives" in document.markdown_content
    assert "PARTIAL_PARSE:page=1:embedded-images" in document.parse_warnings
    assert document.parse_status == "partial"
    assert not [a for a in document.assets if a.asset_type == "source_image"]
