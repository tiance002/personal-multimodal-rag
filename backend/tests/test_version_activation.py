import io

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.ingestion import InMemoryIngestionRepository, IngestionService, IngestionWorker


def test_stale_candidate_cannot_activate_over_newer_ready_version(tmp_path):
    repository = InMemoryIngestionRepository()
    storage = ContentAddressedStorage(tmp_path)
    service = IngestionService(repository, storage, ParserRegistry())
    worker = IngestionWorker(repository, storage, ParserRegistry())

    first = service.submit_upload("kb-1", "notes.txt", "text/plain", io.BytesIO(b"one"))
    second = service.submit_upload("kb-1", "notes.txt", "text/plain", io.BytesIO(b"two"))
    worker.process(second.job_id)
    worker.process(first.job_id)

    assert repository.active_version(first.document_id).id == second.version_id
