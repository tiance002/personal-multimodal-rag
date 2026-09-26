from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local PyMuPDF/Tesseract OCR on a raster-only PDF.")
    parser.add_argument("--tessdata", type=Path, default=Path("var/tessdata"))
    parser.add_argument("--report", type=Path, default=Path("var/reports/probe-pdf-ocr.json"))
    args = parser.parse_args()
    source = fitz.open()
    page = source.new_page(width=600, height=200)
    page.insert_text((36, 100), "OCR TEST 1234", fontsize=32)
    raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    source.close()
    scanned = fitz.open()
    page = scanned.new_page(width=600, height=200)
    page.insert_image(page.rect, stream=raster)
    blob = scanned.tobytes()
    scanned.close()
    with fitz.open(stream=blob, filetype="pdf") as pdf:
        page = pdf[0]
        direct_text = page.get_text("text").strip()
        try:
            text_page = page.get_textpage_ocr(language="eng+chi_sim", dpi=150, full=True, tessdata=str(args.tessdata.resolve()))
            ocr_text = page.get_text("text", textpage=text_page).strip()
            error = None
        except Exception as exc:
            ocr_text = ""
            error = type(exc).__name__
    report = {
        "status": "PASS" if not direct_text and "OCR TEST 1234" in ocr_text.upper() else "FAIL",
        "direct_text": direct_text,
        "ocr_text": ocr_text,
        "error": error,
        "language": "eng+chi_sim",
        "tessdata_sha256": {
            name: __import__("hashlib").sha256((args.tessdata / f"{name}.traineddata").read_bytes()).hexdigest()
            for name in ("eng", "chi_sim")
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
