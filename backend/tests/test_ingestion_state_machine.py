import io

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.ingestion import InMemoryIngestionRepository, IngestionService, IngestionWorker


def test_ingestion_progresses_through_ready_and_activates_candidate(tmp_path):
    repository = InMemoryIngestionRepository()
    storage = ContentAddressedStorage(tmp_path)
    service = IngestionService(repository, storage, ParserRegistry())
    worker = IngestionWorker(repository, storage, ParserRegistry())

    receipt = service.submit_upload("kb-1", "notes.txt", "text/plain", io.BytesIO(b"hello"))
    state = worker.process(receipt.job_id)

    assert state.status == "succeeded"
    assert state.stage == "ready"
    assert repository.active_version(receipt.document_id).id == receipt.version_id


def test_failed_candidate_does_not_replace_active_version(tmp_path):
    repository = InMemoryIngestionRepository()
    storage = ContentAddressedStorage(tmp_path)
    service = IngestionService(repository, storage, ParserRegistry())
    worker = IngestionWorker(repository, storage, ParserRegistry())

    first = service.submit_upload("kb-1", "notes.txt", "text/plain", io.BytesIO(b"good"))
    worker.process(first.job_id)
    active_before = repository.active_version(first.document_id).id

    failed = service.submit_upload("kb-1", "notes.txt", "application/octet-stream", io.BytesIO(b"broken"))
    failed_state = worker.process(failed.job_id)

    assert failed_state.status == "failed"
    assert repository.active_version(first.document_id).id == active_before
