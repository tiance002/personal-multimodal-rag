"""Local native-PDF spacing replay. No API, OCR, model, DB or index writes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.weknora_port.ingestion import DocumentRequest, ingest_document
from backend.weknora_port.ingestion import upstream_pdf_parser as native


def replay(source: Path, expected_sha: str) -> dict:
    data = source.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha:
        raise ValueError("SOURCE_SHA_MISMATCH")
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pages = []
    with pdfium.PdfDocument(data) as pdf:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                plain = native._extract_page_text(page)
                route = native._classify_page(native._page_image_area_ratio(page, raw), len(plain.strip()))
                record = {"physical_page": index + 1, "route": route, "plain": plain}
                if route == "text":
                    record["layout"] = native._extract_layout_text(page, raw)
                    textpage = page.get_textpage()
                    try:
                        chars, _ = native._page_chars(textpage, page, raw)
                        record["glyphs"] = chars
                    finally:
                        textpage.close()
                pages.append(record)
            finally:
                page.close()
    result = ingest_document(DocumentRequest(source.name, data,
        owner_id="offline-s4", knowledge_base_id="offline-s4", document_id="offline-s4",
        source_version=actual, kb_chunking_config={"enable_parent_child": True,
            "parent_chunk_size": 4096, "child_chunk_size": 384}))
    chunks = [{"source_id": c.source_id, "role": c.role, "start": c.start, "end": c.end,
               "content": c.content, "source_version": c.source_version,
               "source_slice_matches": c.content == result.parsed.content[c.start:c.end]}
              for c in result.chunks]
    return {"kind": "LOCAL_NATIVE_PDF_REPLAY_NOT_REINGESTION", "source_sha256": actual,
            "parser": result.parser_engine, "parsed_ok": result.parsed.ok,
            "pages": pages, "parsed_content": result.parsed.content, "chunks": chunks,
            "assets": [{"path": a.path, "page": a.page_number, "source_type": a.source_type,
                        "sha256": hashlib.sha256(a.data).hexdigest()} for a in result.parsed.assets],
            "provider_calls": 0, "ocr_calls": 0, "database_writes": 0, "index_writes": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    result = replay(args.source, args.source_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"parsed_ok": result["parsed_ok"], "parser": result["parser"],
                      "chunks": len(result["chunks"]), "output": str(args.output), "provider_calls": 0}))


if __name__ == "__main__":
    main()
