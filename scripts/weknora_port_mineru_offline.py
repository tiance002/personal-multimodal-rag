"""Offline source/sidecar/chunk acceptance, or explicitly requested local Basic OCR.

No database, embedding, rerank, summary, or generation execution is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.weknora_port.ingestion.core import DocumentRequest, ParserRegistry, ingest_document
from backend.weknora_port.ingestion.mineru import (
    ENGINE, LocalMinerUParser, MinerUSettings, normalize_archive, public_chunk_locators,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--live-local-basic", action="store_true")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--temp-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-assets", action="store_true", help="save exact parser sidecars/crops next to report")
    args = parser.parse_args()
    if args.output.exists() or (args.save_assets and args.output.with_suffix(".assets").exists()):
        parser.error("output already exists; choose a new evidence path before any parsing")
    request = DocumentRequest(args.source.name, args.source.read_bytes(), "offline-owner", "offline-kb",
        "offline-document", "offline-version", parser_engine=ENGINE,
        kb_chunking_config={"chunk_size": 512, "chunk_overlap": 80})
    normalized = None
    if args.live_local_basic:
        if not all((args.python, args.model_root, args.temp_root)) or args.archive or args.receipt:
            parser.error("live local Basic requires explicit python/model/temp paths, no archive")
        callback = LocalMinerUParser(MinerUSettings(args.python, args.model_root, args.temp_root))
    else:
        if not args.archive or not args.receipt:
            parser.error("offline replay requires archive and receipt")
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        normalized = normalize_archive(request, args.archive.read_bytes(), receipt, allow_partial=args.allow_partial)
        callback = lambda _: normalized.parsed
    registry = ParserRegistry()
    registry.register(ENGINE, (args.source.suffix,), callback)
    result = ingest_document(request, registry)
    if result.parsed.failures:
        raise RuntimeError(result.parsed.failures[0].code)
    document_metadata = {"sha256": hashlib.sha256(request.data).hexdigest(),
        "source_version": request.source_version, "parser_metadata": dict(result.parsed.metadata),
        "assets": [{"logical_path": asset.path, "sha256": hashlib.sha256(asset.data).hexdigest()}
                   for asset in result.parsed.assets]}
    checks = []
    for chunk in result.chunks:
        assert chunk.content == result.parsed.content[chunk.start:chunk.end]
        public = public_chunk_locators(chunk.metadata, document_metadata, request.document_id,
                                      request.source_version, chunk.start, chunk.end, chunk.content)
        checks.append({"source_id": chunk.source_id, "start": chunk.start, "end": chunk.end,
                       "locator_count": len(public["source_locators"]),
                       "page_numbers": chunk.metadata["page_numbers"]})
    report = {"status": "PASS", "mode": "live-local-basic" if args.live_local_basic else "saved-output-replay",
        "source_sha256": document_metadata["sha256"], "normalized_model": "NormalizedDocument",
        "parser_evidence": result.parsed.metadata["mineru_source"], "chunk_count": len(result.chunks),
        "characters": len(result.parsed.content), "chunks": checks,
        "asset_count": len(result.parsed.assets), "assets": document_metadata["assets"],
        "db_calls": 0, "rag_provider_calls": 0, "legacy_ocr_caption_calls": 0,
        "input_archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest() if args.archive else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.save_assets:
        folder = args.output.with_suffix(".assets")
        folder.mkdir(exist_ok=False)
        for asset in result.parsed.assets:
            path = folder.joinpath(*Path(asset.path).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(asset.data)
        (folder / "derived.txt").write_text(result.parsed.content, encoding="utf-8")
        (folder / "source-locators.json").write_text(json.dumps(
            result.parsed.metadata["source_locators"], ensure_ascii=False, indent=2), encoding="utf-8")
        report["saved_assets"] = str(folder)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in ("status", "mode", "chunk_count", "characters", "asset_count")}))


if __name__ == "__main__":
    main()
