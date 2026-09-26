from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import fitz
from PIL import Image

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository


def main() -> int:
    suffix = uuid.uuid4().hex[:10]
    repository = PostgresKnowledgeRepository.from_url("postgresql+psycopg://rag:rag@127.0.0.1:55432/rag", Path(f"var/smoke-m2-storage-{suffix}"))
    kb = repository.create_knowledge_base(f"m2-smoke-{suffix}", "temporary M2 verification")
    report: dict[str, object]
    try:
        pdf = fitz.open()
        pdf.new_page().insert_text((72, 72), "normal PDF page one")
        pdf_bytes = pdf.tobytes()
        pdf.close()
        pdf_stored = repository.storage.put_stream(io.BytesIO(pdf_bytes))
        pdf_receipt = repository.create_upload(kb["id"], "normal.pdf", "application/pdf", pdf_stored)
        pdf_job = repository.process_job(pdf_receipt["job_id"])
        pdf_chunks = repository.list_chunks(pdf_receipt["document_id"])

        image_buffer = io.BytesIO()
        Image.new("RGB", (12, 12), "white").save(image_buffer, format="PNG")
        image_bytes = image_buffer.getvalue()
        image_stored = repository.storage.put_stream(io.BytesIO(image_bytes))
        image_receipt = repository.create_upload(kb["id"], "scanned.png", "image/png", image_stored)
        image_job = repository.process_job(image_receipt["job_id"])
        assets = repository.list_assets(image_receipt["document_id"])
        source_preserved = bool(assets and repository.storage.read(assets[0]["storage_key"]) == image_bytes)
        report = {
            "status": "PASS" if pdf_job.get("status") == "succeeded" and pdf_chunks and image_job.get("error_code") == "OCR_UNAVAILABLE" and source_preserved else "FAIL",
            "checks": {
                "pdf_job": pdf_job,
                "pdf_chunks": len(pdf_chunks),
                "image_job": image_job,
                "image_assets": assets,
                "source_preserved": source_preserved,
            },
        }
    finally:
        repository.delete_knowledge_base(kb["id"])
    path = Path("var/reports/smoke-m2.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
