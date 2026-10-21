"""Local image-only OCR with the existing PyMuPDF/Tesseract data path.

No old application service, database, or model is imported.  Geometry is tied
to the native OCR TextPage, so later source readback can locate recognized text.
"""
from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OCRResult:
    text: str
    layout: dict
    error: str = ""


def _layout(textpage, text: str, width: int, height: int, page_rect) -> dict:
    x0, y0, x1, y1 = tuple(page_rect)
    if x1 <= x0 or y1 <= y0 or width <= 0 or height <= 0:
        raise ValueError("IMAGE_OCR_GEOMETRY_INVALID")
    sx, sy = width / (x1 - x0), height / (y1 - y0)
    def pixels(box):
        values = [(box[0] - x0) * sx, (box[1] - y0) * sy,
                  (box[2] - x0) * sx, (box[3] - y0) * sy]
        if any(not math.isfinite(v) for v in values):
            raise ValueError("IMAGE_OCR_GEOMETRY_INVALID")
        return values
    lines, original = [], []
    cursor = 0
    for block in textpage.extractDICT(sort=False)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            value = "".join(span["text"] for span in line["spans"])
            if not value:
                continue
            lines.append({"text": value, "bbox": pixels(line["bbox"]),
                          "start": cursor, "end": cursor + len(value)})
            original.append(value + "\n")
            cursor += len(value) + 1
    joined = "".join(original)
    if joined.strip() != text:
        raise ValueError("IMAGE_OCR_TEXT_ALIGNMENT_UNAVAILABLE")
    trim = len(joined) - len(joined.lstrip())
    return {"schema": "image-ocr-layout-v1", "coordinate_basis": "image-pixels-top-left",
            "lines": [{**line, "start": max(0, line["start"] - trim),
                       "end": min(len(text), line["end"] - trim)} for line in lines]}


class TesseractOCR:
    def __init__(self, tessdata: str | Path | None = None):
        self.tessdata = Path(tessdata or os.environ.get("RAG_TESSDATA_DIR", "var/tessdata"))

    def recognize(self, image: bytes, media_type: str) -> OCRResult:
        if not all((self.tessdata / f"{name}.traineddata").is_file() for name in ("eng", "chi_sim")):
            return OCRResult("", {}, "OCR_UNAVAILABLE")
        try:
            import fitz
            from PIL import Image
            with Image.open(io.BytesIO(image)) as opened:
                width, height = opened.size
                format_name = (opened.format or "").lower()
            if format_name not in {"png", "jpeg", "gif", "bmp", "tiff", "webp"}:
                return OCRResult("", {}, "OCR_IMAGE_FORMAT_UNSUPPORTED")
            source = fitz.open(stream=image, filetype=format_name)
            try:
                pdf_bytes = source.convert_to_pdf()
            finally:
                source.close()
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                page = document[0]
                textpage = page.get_textpage_ocr(language="eng+chi_sim", dpi=150,
                    full=True, tessdata=str(self.tessdata.resolve()))
                text = page.get_text(textpage=textpage).strip()
                if not text:
                    return OCRResult("", {}, "OCR_EMPTY")
                try:
                    layout = _layout(textpage, text, width, height, page.rect)
                except (ValueError, KeyError, TypeError):
                    layout = {"schema": "image-ocr-layout-v1", "status": "GEOMETRY_UNAVAILABLE",
                              "coordinate_basis": "image-pixels-top-left", "lines": []}
                return OCRResult(text, layout)
            finally:
                document.close()
        except Exception:
            return OCRResult("", {}, "OCR_UNAVAILABLE")
