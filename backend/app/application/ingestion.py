from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkDraft, NormalizedDocument
from backend.app.domain.parsers import ParserError
from backend.app.ports.ingestion import BlobStore, DocumentParser


@dataclass
class UploadReceipt:
    document_id: str
    version_id: str
    job_id: str
    storage_key: str
    sha256: str
    size: int
    status: str = "stored"


@dataclass
class VersionRecord:
    id: str
    document_id: str
    version_no: int
    file_name: str
    media_type: str
    source_sha256: str
    storage_key: str
    index_status: str = "queued"
    error_code: str | None = None
    normalized_document: NormalizedDocument | None = None
    chunks: list[ChunkDraft] = field(default_factory=list)


@dataclass
class DocumentRecord:
    id: str
    knowledge_base_id: str
    file_name: str
    media_type: str
    active_version_id: str | None = None
    version_ids: list[str] = field(default_factory=list)


@dataclass
class JobState:
    id: str
    version_id: str
    status: str = "queued"
    stage: str = "queued"
    attempts: int = 0
    max_attempts: int = 3
    progress: int = 0
    error_code: str | None = None


class InMemoryIngestionRepository:
    def __init__(self) -> None:
        self.documents: dict[str, DocumentRecord] = {}
        self.versions: dict[str, VersionRecord] = {}
        self.jobs: dict[str, JobState] = {}

    def find_document(self, knowledge_base_id: str, file_name: str) -> DocumentRecord | None:
        return next(
            (
                document
                for document in self.documents.values()
                if document.knowledge_base_id == knowledge_base_id and document.file_name == file_name
            ),
            None,
        )

    def create_document(self, knowledge_base_id: str, file_name: str, media_type: str) -> DocumentRecord:
        document = DocumentRecord(str(uuid.uuid4()), knowledge_base_id, file_name, media_type)
        self.documents[document.id] = document
        return document

    def create_version(self, document: DocumentRecord, source_sha256: str, storage_key: str) -> VersionRecord:
        version = VersionRecord(
            id=str(uuid.uuid4()),
            document_id=document.id,
            version_no=len(document.version_ids) + 1,
            file_name=document.file_name,
            media_type=document.media_type,
            source_sha256=source_sha256,
            storage_key=storage_key,
        )
        document.version_ids.append(version.id)
        self.versions[version.id] = version
        return version

    def create_job(self, version_id: str) -> JobState:
        job = JobState(id=str(uuid.uuid4()), version_id=version_id)
        self.jobs[job.id] = job
        return job

    def active_version(self, document_id: str) -> VersionRecord:
        document = self.documents[document_id]
        if document.active_version_id is None:
            raise LookupError("document has no active version")
        return self.versions[document.active_version_id]

    def activate_if_current(self, version: VersionRecord) -> bool:
        document = self.documents[version.document_id]
        if document.active_version_id is None:
            document.active_version_id = version.id
            return True
        active = self.versions[document.active_version_id]
        if version.version_no >= active.version_no:
            document.active_version_id = version.id
            return True
        return False


class IngestionService:
    def __init__(
        self,
        repository: InMemoryIngestionRepository,
        storage: BlobStore,
        parsers: DocumentParser,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.parsers = parsers

    def submit_upload(
        self,
        knowledge_base_id: str,
        file_name: str,
        media_type: str,
        stream: BinaryIO,
        duplicate_policy: str = "new_version",
    ) -> UploadReceipt:
        normalized_name = Path(file_name).name
        if normalized_name != file_name or not normalized_name:
            raise ValueError("invalid file name")
        if duplicate_policy not in {"new_version", "skip"}:
            raise ValueError("unsupported duplicate policy")
        stored = self.storage.put_stream(stream)
        document = self.repository.find_document(knowledge_base_id, normalized_name)
        if document is None:
            document = self.repository.create_document(knowledge_base_id, normalized_name, media_type)
        else:
            document.media_type = media_type
        if duplicate_policy == "skip" and document.active_version_id:
            active = self.repository.active_version(document.id)
            if active.source_sha256 == stored.sha256:
                return UploadReceipt(document.id, active.id, "", stored.storage_key, stored.sha256, stored.size, status="duplicate")
        version = self.repository.create_version(document, stored.sha256, stored.storage_key)
        job = self.repository.create_job(version.id)
        return UploadReceipt(document.id, version.id, job.id, stored.storage_key, stored.sha256, stored.size)


class IngestionWorker:
    def __init__(
        self,
        repository: InMemoryIngestionRepository,
        storage: BlobStore,
        parsers: DocumentParser,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.parsers = parsers

    def process(self, job_id: str) -> JobState:
        job = self.repository.jobs[job_id]
        if job.status in {"succeeded", "failed", "cancelled"}:
            return job
        if job.attempts >= job.max_attempts:
            job.status, job.error_code = "failed", "MAX_ATTEMPTS"
            return job
        job.attempts += 1
        job.status, job.stage, job.progress = "running", "processing", 10
        version = self.repository.versions[job.version_id]
        try:
            document = self.repository.documents[version.document_id]
            normalized = self.parsers.parse(
                self.storage.path_for(version.storage_key),
                version.media_type,
                document.id,
                version.id,
            )
            if not normalized.markdown_content and normalized.assets:
                raise ParserError(next((asset.error_code for asset in normalized.assets if asset.error_code), "OCR_EMPTY"))
            job.stage, job.progress = "indexing", 60
            version.normalized_document = normalized
            version.chunks = chunk_document(normalized)
            version.index_status = "ready"
            self.repository.activate_if_current(version)
            job.status, job.stage, job.progress = "succeeded", "ready", 100
        except ParserError as exc:
            version.index_status = "failed"
            version.error_code = str(exc)
            job.status, job.stage, job.progress, job.error_code = "failed", "processing", job.progress, str(exc)
        except Exception as exc:
            version.index_status = "failed"
            version.error_code = "INGESTION_FAILED"
            job.status, job.stage, job.error_code = "failed", "processing", type(exc).__name__
        return job


__all__ = [
    "DocumentRecord",
    "InMemoryIngestionRepository",
    "IngestionService",
    "IngestionWorker",
    "JobState",
    "UploadReceipt",
    "VersionRecord",
]
