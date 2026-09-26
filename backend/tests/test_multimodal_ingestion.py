import io

import fitz
from PIL import Image

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.ingestion import InMemoryIngestionRepository, IngestionService, IngestionWorker


def test_pdf_and_image_keep_source_locators_and_image_failure_is_recoverable(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "page one")
    pdf.save(pdf_path)
    pdf.close()
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (10, 10), "white").save(image_path)

    registry = ParserRegistry()
    pdf_document = registry.parse(pdf_path, "application/pdf", "doc", "ver")
    image_document = registry.parse(image_path, "image/png", "doc", "image-ver")

    assert pdf_document.source_locators[0].page == 1
    assert image_document.assets[0].asset_type == "source_image"

    repository = InMemoryIngestionRepository()
    storage = ContentAddressedStorage(tmp_path / "storage")
    service = IngestionService(repository, storage, registry)
    worker = IngestionWorker(repository, storage, registry)
    receipt = service.submit_upload("kb", "sample.png", "image/png", io.BytesIO(image_path.read_bytes()))
    state = worker.process(receipt.job_id)

    assert state.status == "failed"
    assert state.error_code == "OCR_EMPTY"
    assert storage.read(receipt.storage_key) == image_path.read_bytes()
