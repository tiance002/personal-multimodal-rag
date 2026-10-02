from __future__ import annotations

import json
import hashlib
import threading
import uuid
from contextlib import contextmanager
from io import BytesIO
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, text

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.chunking import chunk_document, profile_document
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.parsers import ParserError
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery, term_frequencies


class PostgresKnowledgeRepository:
    def __init__(
        self,
        engine: Engine,
        storage: ContentAddressedStorage,
        parsers: ParserRegistry | None = None,
        embedding_provider: Any | None = None,
        *,
        max_chunk_chars: int = 1200,
        chunk_overlap: int = 120,
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.parsers = parsers or ParserRegistry()
        self.embedding_provider = embedding_provider
        self.max_chunk_chars = max_chunk_chars
        self.chunk_overlap = chunk_overlap

    def create_knowledge_base(self, name: str, description: str = "", *, graph_enabled: bool = False, cloud_allowed: bool = False) -> dict[str, Any]:
        kb_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO knowledge_bases (id,name,description,graph_enabled,cloud_allowed) VALUES (:id,:name,:description,:graph_enabled,:cloud_allowed)"),
                {"id": kb_id, "name": name, "description": description, "graph_enabled": graph_enabled, "cloud_allowed": cloud_allowed},
            )
        return {"id": str(kb_id), "name": name, "description": description, "graph_enabled": graph_enabled, "cloud_allowed": cloud_allowed}

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id,name,description,graph_enabled,cloud_allowed,created_at,updated_at FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at")).mappings()
            return [dict(row) for row in rows]

    def get_knowledge_base(self, kb_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT id,name,description,graph_enabled,cloud_allowed,created_at,updated_at FROM knowledge_bases WHERE id=:id AND deleted_at IS NULL"), {"id": kb_id}).mappings().first()
            return dict(row) if row else None

    def update_knowledge_base(self, kb_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {key: value for key, value in fields.items() if key in {"name", "description", "graph_enabled", "cloud_allowed"}}
        if not allowed:
            return self.get_knowledge_base(kb_id)
        assignments = ", ".join(f"{key}=:{key}" for key in allowed)
        allowed["id"] = kb_id
        with self.engine.begin() as conn:
            result = conn.execute(text(f"UPDATE knowledge_bases SET {assignments}, updated_at=now() WHERE id=:id AND deleted_at IS NULL"), allowed)
            if result.rowcount == 0:
                return None
        return self.get_knowledge_base(kb_id)

    def delete_knowledge_base(self, kb_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(text("UPDATE knowledge_bases SET deleted_at=now(), updated_at=now() WHERE id=:id AND deleted_at IS NULL"), {"id": kb_id})
            return result.rowcount == 1

    def create_upload(self, kb_id: str, file_name: str, media_type: str, stored: Any, duplicate_policy: str = "new_version") -> dict[str, Any]:
        document_id = uuid.uuid4()
        version_id = uuid.uuid4()
        job_id = uuid.uuid4()
        with self.engine.begin() as conn:
            kb_exists = conn.execute(text("SELECT 1 FROM knowledge_bases WHERE id=:id AND deleted_at IS NULL"), {"id": kb_id}).first()
            if not kb_exists:
                raise LookupError("knowledge base not found")
            existing = conn.execute(text("SELECT id,active_version_id FROM documents WHERE knowledge_base_id=:kb AND file_name=:name AND deleted_at IS NULL FOR UPDATE"), {"kb": kb_id, "name": file_name}).mappings().first()
            if existing:
                document_id = existing["id"]
                if duplicate_policy == "skip" and existing["active_version_id"]:
                    current = conn.execute(text("SELECT id,source_sha256 FROM document_versions WHERE id=:id"), {"id": existing["active_version_id"]}).mappings().first()
                    if current and current["source_sha256"] == stored.sha256:
                        return {"document_id": str(document_id), "version_id": str(current["id"]), "job_id": None, "storage_key": stored.storage_key, "sha256": stored.sha256, "size": stored.size, "status": "duplicate"}
                version_no = conn.execute(text("SELECT COALESCE(MAX(version_no),0)+1 AS next_no FROM document_versions WHERE document_id=:id"), {"id": document_id}).scalar_one()
                conn.execute(text("UPDATE documents SET media_type=:media_type, original_size=:size, updated_at=now() WHERE id=:id"), {"id": document_id, "media_type": media_type, "size": stored.size})
            else:
                version_no = 1
                conn.execute(text("INSERT INTO documents (id,knowledge_base_id,file_name,media_type,original_size) VALUES (:id,:kb,:name,:media_type,:size)"), {"id": document_id, "kb": kb_id, "name": file_name, "media_type": media_type, "size": stored.size})
            conn.execute(text("INSERT INTO document_versions (id,document_id,version_no,source_sha256,storage_key,parser_version,index_status,graph_status) VALUES (:id,:document_id,:version_no,:sha256,:storage_key,'pending','queued','disabled')"), {"id": version_id, "document_id": document_id, "version_no": version_no, "sha256": stored.sha256, "storage_key": stored.storage_key})
            conn.execute(text("INSERT INTO ingestion_jobs (id,version_id,job_type,status,stage) VALUES (:id,:version_id,'ingest','queued','queued')"), {"id": job_id, "version_id": version_id})
        return {"document_id": str(document_id), "version_id": str(version_id), "job_id": str(job_id), "storage_key": stored.storage_key, "sha256": stored.sha256, "size": stored.size, "status": "stored"}

    def create_version(
        self,
        document_id: str,
        file_name: str,
        media_type: str,
        stored: Any,
        duplicate_policy: str = "new_version",
    ) -> dict[str, Any]:
        """Queue a new immutable version for this exact document identity."""
        version_id = uuid.uuid4()
        job_id = uuid.uuid4()
        with self.engine.begin() as conn:
            document = conn.execute(
                text("""
                    SELECT d.id, d.knowledge_base_id, d.active_version_id
                    FROM documents d
                    JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id AND kb.deleted_at IS NULL
                    WHERE d.id=:document_id AND d.deleted_at IS NULL
                    FOR UPDATE OF d
                """),
                {"document_id": document_id},
            ).mappings().first()
            if not document:
                raise LookupError("document not found")
            if duplicate_policy == "skip" and document["active_version_id"]:
                current = conn.execute(
                    text("SELECT id,source_sha256,version_no FROM document_versions WHERE id=:id"),
                    {"id": document["active_version_id"]},
                ).mappings().first()
                if current and current["source_sha256"] == stored.sha256:
                    return {
                        "document_id": str(document_id),
                        "version_id": str(current["id"]),
                        "version_no": current["version_no"],
                        "job_id": None,
                        "storage_key": stored.storage_key,
                        "sha256": stored.sha256,
                        "size": stored.size,
                        "status": "duplicate",
                    }
            version_no = conn.execute(
                text("SELECT COALESCE(MAX(version_no),0)+1 AS next_no FROM document_versions WHERE document_id=:document_id"),
                {"document_id": document_id},
            ).scalar_one()
            conn.execute(
                text("UPDATE documents SET file_name=:file_name,media_type=:media_type,original_size=:size,updated_at=clock_timestamp() WHERE id=:id"),
                {"id": document_id, "file_name": file_name, "media_type": media_type, "size": stored.size},
            )
            conn.execute(
                text("""
                    INSERT INTO document_versions
                        (id,document_id,version_no,source_sha256,storage_key,parser_version,index_status,graph_status)
                    VALUES (:id,:document_id,:version_no,:sha256,:storage_key,'pending','queued','disabled')
                """),
                {"id": version_id, "document_id": document_id, "version_no": version_no, "sha256": stored.sha256, "storage_key": stored.storage_key},
            )
            conn.execute(
                text("INSERT INTO ingestion_jobs (id,version_id,job_type,status,stage) VALUES (:id,:version_id,'ingest','queued','queued')"),
                {"id": job_id, "version_id": version_id},
            )
        return {
            "document_id": str(document_id),
            "version_id": str(version_id),
            "version_no": version_no,
            "job_id": str(job_id),
            "storage_key": stored.storage_key,
            "sha256": stored.sha256,
            "size": stored.size,
            "status": "stored",
        }

    def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim one job and fence the claim with a fresh token."""
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claim_token = uuid.uuid4()
        params: dict[str, Any] = {
            "worker_id": worker_id,
            "claim_token": claim_token,
            "lease_seconds": lease_seconds,
        }
        target = ""
        if job_id is not None:
            target = " AND id=:job_id"
            params["job_id"] = job_id
        statement = text(f"""
            WITH candidate AS (
                SELECT id
                FROM ingestion_jobs
                WHERE attempts < max_attempts
                  AND (
                      status = 'queued'
                      OR (status IN ('leased', 'running') AND lease_until IS NOT NULL AND lease_until <= clock_timestamp())
                  ){target}
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE ingestion_jobs AS job
            SET status='running',
                stage='processing',
                progress=10,
                worker_id=:worker_id,
                claim_token=:claim_token,
                lease_until=clock_timestamp() + (:lease_seconds * INTERVAL '1 second'),
                attempts=job.attempts + 1,
                error_code=NULL,
                updated_at=clock_timestamp()
            FROM candidate
            WHERE job.id=candidate.id
            RETURNING job.id, job.version_id, job.status, job.stage, job.attempts,
                      job.worker_id, job.claim_token, job.lease_until
        """)
        with self.engine.begin() as conn:
            row = conn.execute(statement, params).mappings().first()
        if not row:
            return None
        result = dict(row)
        for key in ("id", "version_id", "claim_token"):
            if result.get(key) is not None:
                result[key] = str(result[key])
        return result

    def renew_job(self, job_id: str, *, worker_id: str, claim_token: str, lease_seconds: int = 60) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE ingestion_jobs
                    SET lease_until=clock_timestamp() + (:lease_seconds * INTERVAL '1 second'), updated_at=clock_timestamp()
                    WHERE id=:id AND status='running' AND worker_id=:worker_id
                      AND claim_token=:claim_token AND lease_until > clock_timestamp()
                """),
                {"id": job_id, "worker_id": worker_id, "claim_token": claim_token, "lease_seconds": lease_seconds},
            )
            return result.rowcount == 1

    @contextmanager
    def _lease_heartbeat(self, job_id: str, *, worker_id: str, claim_token: str, lease_seconds: int) -> Iterator[None]:
        """Renew a live claim while parser or embedding work is running."""
        stop = threading.Event()
        interval = max(0.5, lease_seconds / 3)

        def heartbeat() -> None:
            while not stop.wait(interval):
                if not self.renew_job(job_id, worker_id=worker_id, claim_token=claim_token, lease_seconds=lease_seconds):
                    return

        thread = threading.Thread(target=heartbeat, name=f"ingestion-lease-{job_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=interval + 1)

    def update_job_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        claim_token: str,
        stage: str,
        progress: int,
    ) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE ingestion_jobs
                    SET stage=:stage, progress=:progress, updated_at=clock_timestamp()
                    WHERE id=:id AND status='running' AND worker_id=:worker_id
                      AND claim_token=:claim_token AND lease_until > clock_timestamp()
                """),
                {"id": job_id, "worker_id": worker_id, "claim_token": claim_token, "stage": stage, "progress": progress},
            )
            return result.rowcount == 1

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT id,version_id,status,stage,attempts,max_attempts,progress,error_code,created_at,updated_at FROM ingestion_jobs WHERE id=:id"), {"id": job_id}).mappings().first()
            return dict(row) if row else None

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT d.id,d.knowledge_base_id,d.file_name,d.media_type,d.original_size,d.active_version_id,d.created_at,d.updated_at,
                dv.version_no,dv.index_status,dv.graph_status,dv.source_sha256
                FROM documents d LEFT JOIN document_versions dv ON dv.id=d.active_version_id
                WHERE d.id=:id AND d.deleted_at IS NULL"""), {"id": document_id}).mappings().first()
            return dict(row) if row else None

    def get_document_access(self, document_id: str) -> dict[str, Any] | None:
        """Resolve ownership and active-index state before a document read."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT d.id, d.knowledge_base_id, d.deleted_at, d.active_version_id,
                           dv.version_no, dv.index_status, dv.graph_status
                    FROM documents d
                    LEFT JOIN document_versions dv ON dv.id=d.active_version_id
                    WHERE d.id=:id
                """),
                {"id": document_id},
            ).mappings().first()
            return dict(row) if row else None

    def list_documents(self, kb_id: str) -> list[dict[str, Any]]:
        """List documents with both active and latest version state.

        `dv.*` describes the *active* (served) version, while `lv.*` and
        `lj.*` describe the *newest* version and its most recent ingestion job.
        Exposing both keeps a failed re-index from being masked by an older
        active version that is still `ready`.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT d.id,d.knowledge_base_id,d.file_name,d.media_type,d.original_size,d.active_version_id,d.updated_at,
                       dv.version_no,dv.index_status,dv.graph_status,
                       lv.id AS latest_version_id,lv.version_no AS latest_version_no,lv.index_status AS latest_index_status,
                       lj.id AS latest_job_id,lj.status AS latest_job_status,lj.stage AS latest_job_stage,
                       lj.progress AS latest_job_progress,lj.error_code AS latest_job_error_code,
                       lj.attempts AS latest_job_attempts,lj.max_attempts AS latest_job_max_attempts
                FROM documents d
                LEFT JOIN document_versions dv ON dv.id=d.active_version_id
                LEFT JOIN LATERAL (
                    SELECT id,version_no,index_status FROM document_versions
                    WHERE document_id=d.id ORDER BY version_no DESC LIMIT 1
                ) lv ON true
                LEFT JOIN LATERAL (
                    SELECT ij.id,ij.status,ij.stage,ij.progress,ij.error_code,ij.attempts,ij.max_attempts
                    FROM ingestion_jobs ij
                    WHERE ij.version_id=lv.id ORDER BY ij.created_at DESC, ij.id DESC LIMIT 1
                ) lj ON true
                WHERE d.knowledge_base_id=:kb AND d.deleted_at IS NULL ORDER BY d.updated_at DESC"""), {"kb": kb_id}).mappings()
            return [self._document_row(row) for row in rows]

    @staticmethod
    def _document_row(row: Any) -> dict[str, Any]:
        document = dict(row)
        job_id = document.pop("latest_job_id", None)
        job_status = document.pop("latest_job_status", None)
        job_stage = document.pop("latest_job_stage", None)
        job_progress = document.pop("latest_job_progress", None)
        job_error = document.pop("latest_job_error_code", None)
        job_attempts = document.pop("latest_job_attempts", None)
        job_max_attempts = document.pop("latest_job_max_attempts", None)
        document["latest_job"] = (
            {
                "id": str(job_id),
                "status": job_status,
                "stage": job_stage,
                "progress": job_progress or 0,
                "error_code": job_error,
                "attempts": job_attempts or 0,
                "max_attempts": job_max_attempts or 1,
            }
            if job_id is not None
            else None
        )
        if document.get("latest_version_id") is not None:
            document["latest_version_id"] = str(document["latest_version_id"])
        return document

    def process_job(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        claim_token: str | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        """Process a fenced claim; direct callers first acquire their own claim."""
        if worker_id is None or claim_token is None:
            worker_id = worker_id or f"inline-{uuid.uuid4()}"
            claim = self.claim_job(worker_id=worker_id, lease_seconds=lease_seconds, job_id=job_id)
            if claim is None:
                return self.get_job(job_id) or {}
            claim_token = str(claim["claim_token"])
        with self.engine.begin() as conn:
            job = conn.execute(
                text("""
                    SELECT * FROM ingestion_jobs
                    WHERE id=:id AND status='running' AND worker_id=:worker_id
                      AND claim_token=:claim_token AND lease_until > clock_timestamp()
                    FOR UPDATE
                """),
                {"id": job_id, "worker_id": worker_id, "claim_token": claim_token},
            ).mappings().first()
            if not job:
                return self.get_job(job_id) or {}
            version = conn.execute(
                text("SELECT dv.*,d.file_name,d.media_type,d.knowledge_base_id FROM document_versions dv JOIN documents d ON d.id=dv.document_id WHERE dv.id=:id"),
                {"id": job["version_id"]},
            ).mappings().one()
        if not self.renew_job(job_id, worker_id=worker_id, claim_token=claim_token, lease_seconds=lease_seconds):
            return self.get_job(job_id) or {}
        try:
            # Make the claim visible immediately.  Previously the job stayed at
            # the database default (0%) until the terminal update, which made a
            # healthy PDF parse look permanently stuck in the UI.
            self.update_job_progress(
                job_id,
                worker_id=worker_id,
                claim_token=claim_token,
                stage="processing",
                progress=10,
            )
            with self._lease_heartbeat(job_id, worker_id=worker_id, claim_token=claim_token, lease_seconds=lease_seconds):
                normalized = self.parsers.parse(self.storage.path_for(version["storage_key"]), version["media_type"], str(version["document_id"]), str(version["id"]))
                self.update_job_progress(
                    job_id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    stage="processing",
                    progress=30,
                )
                asset_ids = {asset.asset_id: uuid.uuid4() for asset in normalized.assets}
                asset_storage = {
                    asset.asset_id: self.storage.put_stream(BytesIO(asset.source_bytes)).storage_key
                    for asset in normalized.assets if asset.source_bytes is not None
                }
                if normalized.assets:
                    with self.engine.begin() as conn:
                        owned = conn.execute(
                            text("""
                                SELECT 1 FROM ingestion_jobs
                                WHERE id=:id AND status='running' AND worker_id=:worker_id
                                  AND claim_token=:claim_token AND lease_until > clock_timestamp()
                                FOR UPDATE
                            """),
                            {"id": job_id, "worker_id": worker_id, "claim_token": claim_token},
                        ).first()
                        if not owned:
                            return self.get_job(job_id) or {}
                        for asset in normalized.assets:
                            conn.execute(text("""INSERT INTO document_assets (id,version_id,document_id,knowledge_base_id,asset_type,storage_key,text_content,derived_from_asset_id,page_no,source_locator,status,error_code)
                                VALUES (:id,:version_id,:document_id,:kb,:asset_type,:storage_key,:text_content,:derived_from,:page_no,CAST(:locator AS jsonb),:status,:error_code)"""), {
                                "id": asset_ids[asset.asset_id], "version_id": version["id"], "document_id": version["document_id"], "kb": version["knowledge_base_id"], "asset_type": asset.asset_type, "storage_key": asset_storage.get(asset.asset_id) or (version["storage_key"] if asset.asset_type == "source_image" and version["media_type"].startswith("image/") else asset.storage_key), "text_content": asset.text_content, "derived_from": asset_ids.get(asset.derived_from_asset_id), "page_no": asset.page_no, "locator": json.dumps({**asset.source_locator, **({"source_asset_id": str(asset_ids[asset.derived_from_asset_id])} if asset.derived_from_asset_id else {})}), "status": asset.status, "error_code": asset.error_code,
                            })
                self.update_job_progress(
                    job_id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    stage="processing",
                    progress=40,
                )
                if not normalized.markdown_content.strip() and normalized.assets:
                    raise ParserError(next((asset.error_code for asset in normalized.assets if asset.error_code), "OCR_EMPTY"))
                if not normalized.markdown_content.strip():
                    raise ParserError("EMPTY_TEXT")
                chunking = profile_document(normalized, max_chars=self.max_chunk_chars)
                chunks = chunk_document(normalized, max_chars=self.max_chunk_chars, overlap=self.chunk_overlap)
                if not chunks:
                    raise ParserError("EMPTY_TEXT")
                self.update_job_progress(
                    job_id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    stage="processing",
                    progress=55,
                )
                normalized_object = self.storage.put_stream(BytesIO(normalized.markdown_content.encode("utf-8"))) if normalized.markdown_content else None
                vectors: list[list[float]] = []
                self.update_job_progress(
                    job_id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    stage="indexing",
                    progress=65,
                )
                if self.embedding_provider is not None and chunks:
                    vectors = self.embedding_provider.embed([chunk.content for chunk in chunks], timeout_seconds=60).vectors
                    if len(vectors) != len(chunks) or any(len(vector) != 1024 for vector in vectors):
                        raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")
                self.update_job_progress(
                    job_id,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    stage="indexing",
                    progress=80,
                )
            if not self.renew_job(job_id, worker_id=worker_id, claim_token=claim_token, lease_seconds=lease_seconds):
                return self.get_job(job_id) or {}
            with self.engine.begin() as conn:
                owned = conn.execute(
                    text("""
                        SELECT 1 FROM ingestion_jobs
                        WHERE id=:id AND status='running' AND worker_id=:worker_id
                          AND claim_token=:claim_token AND lease_until > clock_timestamp()
                        FOR UPDATE
                    """),
                    {"id": job_id, "worker_id": worker_id, "claim_token": claim_token},
                ).first()
                if not owned:
                    return self.get_job(job_id) or {}
                section_ids: dict[str, uuid.UUID] = {}
                for section in normalized.sections:
                    section_id = uuid.uuid4()
                    section_ids[section.section_id] = section_id
                    conn.execute(text("""INSERT INTO document_sections (id,version_id,document_id,knowledge_base_id,heading,heading_path,level,start_pos,end_pos,page_start,page_end)
                        VALUES (:id,:version_id,:document_id,:kb,:heading,:heading_path,:level,:start_pos,:end_pos,:page_start,:page_end)"""), {
                        "id": section_id, "version_id": version["id"], "document_id": version["document_id"], "kb": version["knowledge_base_id"], "heading": section.heading, "heading_path": json.dumps(list(section.heading_path)), "level": section.level, "start_pos": section.start, "end_pos": section.end, "page_start": section.page_start, "page_end": section.page_end,
                    })
                profile_id = None
                if vectors:
                    profile_id = conn.execute(text("SELECT id FROM embedding_profiles WHERE provider='ollama' AND model_name=:model AND dimension=1024 LIMIT 1"), {"model": getattr(self.embedding_provider, "embedding_model", "bge-m3:latest")}).scalar()
                    if profile_id is None:
                        profile_id = uuid.uuid4()
                        conn.execute(text("INSERT INTO embedding_profiles (id,provider,model_name,model_revision,dimension,distance,fingerprint) VALUES (:id,'ollama',:model,'local',1024,'cosine','ollama-local-1024')"), {"id": profile_id, "model": getattr(self.embedding_provider, "embedding_model", "bge-m3:latest")})
                for index, chunk in enumerate(chunks):
                    chunk_id = uuid.uuid4()
                    section_id = section_ids.get(next((section.section_id for section in normalized.sections if section.start <= chunk.start < section.end), ""))
                    conn.execute(text("""INSERT INTO chunks (id,section_id,version_id,document_id,knowledge_base_id,chunk_index,content,content_sha256,start_pos,end_pos,heading_path,chunk_type,locator)
                        VALUES (:id,:section_id,:version_id,:document_id,:kb,:chunk_index,:content,:sha256,:start_pos,:end_pos,:heading_path,:chunk_type,:locator)"""), {
                        "id": chunk_id, "section_id": section_id, "version_id": version["id"], "document_id": version["document_id"], "kb": version["knowledge_base_id"], "chunk_index": index, "content": chunk.content, "sha256": chunk.content_sha256, "start_pos": chunk.start, "end_pos": chunk.end, "heading_path": json.dumps(list(chunk.heading_path)), "chunk_type": chunk.chunk_type, "locator": json.dumps({**chunk.source_locator.model_dump(), "asset_id": str(asset_ids[chunk.source_locator.asset_id]) if chunk.source_locator.asset_id else None}),
                    })
                    if chunk.source_locator.asset_id:
                        conn.execute(text("INSERT INTO chunk_assets (chunk_id,asset_id,version_id) VALUES (:chunk_id,:asset_id,:version_id)"), {
                            "chunk_id": chunk_id, "asset_id": asset_ids[chunk.source_locator.asset_id], "version_id": version["id"],
                        })
                    for term, frequency in term_frequencies(chunk.content).items():
                        conn.execute(text("INSERT INTO chunk_terms (chunk_id,term,term_frequency) VALUES (:chunk_id,:term,:frequency)"), {"chunk_id": chunk_id, "term": term, "frequency": frequency})
                    if profile_id is not None:
                        vector_literal = "[" + ",".join(str(value) for value in vectors[index]) + "]"
                        conn.execute(text("INSERT INTO chunk_embeddings (chunk_id,profile_id,embedding) VALUES (:chunk_id,:profile_id,CAST(:embedding AS vector))"), {"chunk_id": chunk_id, "profile_id": profile_id, "embedding": vector_literal})
                conn.execute(text("""UPDATE document_versions SET index_status='ready',parser_version=:parser_version,chunker_version=:chunker_version,chunk_strategy=:chunk_strategy,normalized_content_key=:normalized_key,normalized_content_sha256=:normalized_sha,normalizer_version='text/v1',activated_at=clock_timestamp()
                    WHERE id=:id"""), {"id": version["id"], "parser_version": normalized.parser_version, "chunker_version": chunking.chunker_version, "chunk_strategy": chunking.strategy, "normalized_key": normalized_object.storage_key if normalized_object else None, "normalized_sha": normalized_object.sha256 if normalized_object else None})
                current = conn.execute(text("SELECT active_version_id FROM documents WHERE id=:id FOR UPDATE"), {"id": version["document_id"]}).scalar()
                current_no = conn.execute(text("SELECT version_no FROM document_versions WHERE id=:id"), {"id": current}).scalar() if current else None
                if current_no is None or version["version_no"] >= current_no:
                    conn.execute(text("UPDATE documents SET active_version_id=:version_id,updated_at=clock_timestamp() WHERE id=:id"), {"version_id": version["id"], "id": version["document_id"]})
                completion = conn.execute(
                    text("""
                        UPDATE ingestion_jobs
                        SET status='succeeded',stage='ready',progress=100,updated_at=clock_timestamp()
                        WHERE id=:id AND status='running' AND worker_id=:worker_id
                          AND claim_token=:claim_token AND lease_until > clock_timestamp()
                    """),
                    {"id": job_id, "worker_id": worker_id, "claim_token": claim_token},
                )
                if completion.rowcount != 1:
                    raise RuntimeError("INGESTION_CLAIM_LOST")
        except Exception as exc:
            code = str(exc) if isinstance(exc, ParserError) else type(exc).__name__
            with self.engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE document_versions
                        SET index_status='failed',error_code=:code
                        WHERE id=:version_id AND EXISTS (
                            SELECT 1 FROM ingestion_jobs
                            WHERE id=:job_id AND status='running' AND worker_id=:worker_id
                              AND claim_token=:claim_token AND lease_until > clock_timestamp()
                        )
                    """),
                    {"version_id": version["id"], "job_id": job_id, "worker_id": worker_id, "claim_token": claim_token, "code": code},
                )
                conn.execute(
                    text("""
                        UPDATE ingestion_jobs
                        SET status='failed',error_code=:code,updated_at=clock_timestamp()
                        WHERE id=:id AND status='running' AND worker_id=:worker_id
                          AND claim_token=:claim_token AND lease_until > clock_timestamp()
                    """),
                    {"id": job_id, "worker_id": worker_id, "claim_token": claim_token, "code": code},
                )
        return self.get_job(job_id) or {}

    def _scope_filter(self, scope: Scope, params: dict[str, Any], alias: str = "c") -> str:
        """Bind the scope predicate and return the SQL fragment.

        Scope is always applied inside the candidate query so a chunk outside
        the server-decided scope can never be ranked, let alone cited.
        """
        kb_names = ",".join(f":kb_{index}" for index, _ in enumerate(scope.knowledge_base_ids))
        params.update({f"kb_{index}": value for index, value in enumerate(scope.knowledge_base_ids)})
        clause = f"{alias}.knowledge_base_id IN ({kb_names})"
        if scope.document_ids:
            document_names = ",".join(f":doc_{index}" for index, _ in enumerate(scope.document_ids))
            params.update({f"doc_{index}": value for index, value in enumerate(scope.document_ids)})
            clause += f" AND {alias}.document_id IN ({document_names})"
        return clause

    _ACTIVE_VERSION_JOINS = """
        JOIN documents d ON d.id = c.document_id AND d.active_version_id = c.version_id
        JOIN document_versions dv ON dv.id = c.version_id AND dv.document_id = c.document_id
    """

    def keyword_candidates(self, scope: Scope, query: NormalizedQuery, limit: int) -> list[RankedHit]:
        """Rank candidates from the persisted `chunk_terms` index.

        The filter, the aggregate and the `LIMIT` all run in PostgreSQL, so the
        response is bounded by `limit` instead of by corpus size.
        """
        if not scope.knowledge_base_ids or not query.terms or limit <= 0:
            return []
        params: dict[str, Any] = {"normalized": query.normalized, "limit": limit}
        clause = self._scope_filter(scope, params)
        term_names = ",".join(f":t_{index}" for index, _ in enumerate(query.terms))
        params.update({f"t_{index}": term for index, term in enumerate(query.terms)})
        statement = text(f"""
            SELECT c.id AS chunk_id,
                   SUM(ct.term_frequency) + CASE
                       WHEN :normalized <> '' AND POSITION(:normalized IN lower(c.content)) > 0 THEN 2
                       ELSE 0 END AS score
            FROM chunks c
            {self._ACTIVE_VERSION_JOINS}
            JOIN chunk_terms ct ON ct.chunk_id = c.id AND ct.term IN ({term_names})
            WHERE {clause} AND d.deleted_at IS NULL AND dv.index_status = 'ready'
            GROUP BY c.id
            ORDER BY score DESC, c.id
            LIMIT :limit
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(statement, params).mappings()
            return [RankedHit(chunk_id=str(row["chunk_id"]), rank=index, raw_score=float(row["score"])) for index, row in enumerate(rows, start=1)]

    def get_embedding_profile_id(self, model_name: str, dimension: int) -> str | None:
        if not model_name or dimension <= 0:
            return None
        with self.engine.connect() as conn:
            profile_id = conn.execute(
                text("""
                    SELECT id
                    FROM embedding_profiles
                    WHERE provider='ollama' AND model_name=:model_name AND dimension=:dimension
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"model_name": model_name, "dimension": dimension},
            ).scalar()
            return str(profile_id) if profile_id is not None else None

    def vector_candidates(self, scope: Scope, vector: Any, limit: int, *, profile_id: str | None = None) -> list[RankedHit]:
        """Rank candidates with the pgvector cosine-distance operator.

        Ordering happens inside PostgreSQL against the HNSW index created in
        migration 0009, so no embedding ever crosses the wire for ranking.
        """
        values = list(vector or ())
        if not scope.knowledge_base_ids or not values or limit <= 0 or not profile_id:
            return []
        params: dict[str, Any] = {"limit": limit, "profile_id": profile_id}
        clause = self._scope_filter(scope, params)
        params["vector"] = "[" + ",".join(str(float(value)) for value in values) + "]"
        statement = text(f"""
            SELECT c.id AS chunk_id, 1 - (ce.embedding <=> CAST(:vector AS vector)) AS score
            FROM chunks c
            {self._ACTIVE_VERSION_JOINS}
            JOIN chunk_embeddings ce ON ce.chunk_id = c.id AND ce.profile_id = :profile_id
            WHERE {clause} AND d.deleted_at IS NULL AND dv.index_status = 'ready'
            ORDER BY ce.embedding <=> CAST(:vector AS vector)
            LIMIT :limit
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(statement, params).mappings()
            return [RankedHit(chunk_id=str(row["chunk_id"]), rank=index, raw_score=float(row["score"])) for index, row in enumerate(rows, start=1)]

    def list_active_chunks(self, scope: Scope, limit: int | None = None) -> list[ChunkRecord]:
        """Read active chunk rows in a scope.

        Embeddings are deliberately not selected here: ranking resolves vectors
        in SQL via `vector_candidates`, so pulling 1024-dimension vectors into
        Python would be pure waste.
        """
        if not scope.knowledge_base_ids:
            return []
        params: dict[str, Any] = {}
        clause = self._scope_filter(scope, params)
        limit_clause = ""
        if limit is not None:
            params["limit"] = limit
            limit_clause = " LIMIT :limit"
        statement = text(f"""
            SELECT c.id,c.knowledge_base_id,c.document_id,c.version_id,c.content,c.content_sha256,c.locator
            FROM chunks c
            {self._ACTIVE_VERSION_JOINS}
            WHERE {clause} AND d.deleted_at IS NULL AND dv.index_status='ready'
            ORDER BY c.id{limit_clause}
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(statement, params).mappings()
            return [ChunkRecord(str(row["id"]), str(row["knowledge_base_id"]), str(row["document_id"]), str(row["version_id"]), row["content"], row["locator"] or {}, content_sha256=row["content_sha256"]) for row in rows]

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT id,knowledge_base_id,document_id,version_id,content,content_sha256,locator FROM chunks WHERE id=:id"), {"id": chunk_id}).mappings().first()
            if not row:
                return None
            return ChunkRecord(str(row["id"]), str(row["knowledge_base_id"]), str(row["document_id"]), str(row["version_id"]), row["content"], row["locator"] or {}, content_sha256=row["content_sha256"])

    def retry_job(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            result = conn.execute(text("UPDATE ingestion_jobs SET status='queued',stage='queued',worker_id=NULL,claim_token=NULL,lease_until=NULL,error_code=NULL,progress=0,updated_at=clock_timestamp() WHERE id=:id AND status='failed' AND attempts < max_attempts"), {"id": job_id})
            if result.rowcount == 0:
                return self.get_job(job_id)
        return self.get_job(job_id)

    def list_chunks(self, document_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id,version_id,chunk_index,content,content_sha256,locator,heading_path,chunk_type FROM chunks WHERE document_id=:id ORDER BY version_id,chunk_index"), {"id": document_id}).mappings()
            return [dict(row) for row in rows]

    def list_assets(self, document_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id,version_id,asset_type,storage_key,text_content,derived_from_asset_id,page_no,source_locator,status,error_code FROM document_assets WHERE document_id=:id ORDER BY created_at"), {"id": document_id}).mappings()
            return [dict(row) for row in rows]

    def get_asset(self, document_id: str, asset_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT id,version_id,asset_type,storage_key,text_content,derived_from_asset_id,page_no,source_locator,status,error_code FROM document_assets WHERE document_id=:document_id AND id=:asset_id"), {"document_id": document_id, "asset_id": asset_id}).mappings().first()
            return dict(row) if row else None

    def get_document_content(self, document_id: str) -> str:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT active_version_id FROM documents WHERE id=:id AND deleted_at IS NULL"), {"id": document_id}).first()
            if not row or row[0] is None:
                raise LookupError("document content not found")
            chunks = conn.execute(text("SELECT content FROM chunks WHERE document_id=:id AND version_id=:version_id ORDER BY chunk_index"), {"id": document_id, "version_id": row[0]}).scalars()
            return "\n".join(chunks)

    def get_document_source(self, document_id: str) -> dict[str, Any]:
        """Return the source bytes for the active version, or latest candidate.

        A document without an active version can still be previewed immediately
        after upload by falling back to its newest immutable candidate. The
        returned path is resolved through content-addressed storage and never
        accepts a client-provided filesystem path.
        """
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT d.file_name,d.media_type,d.active_version_id,
                           dv.storage_key,dv.version_no
                    FROM documents d
                    LEFT JOIN document_versions dv ON dv.id = COALESCE(
                        d.active_version_id,
                        (
                            SELECT latest.id
                            FROM document_versions latest
                            WHERE latest.document_id=d.id
                            ORDER BY latest.version_no DESC
                            LIMIT 1
                        )
                    )
                    WHERE d.id=:id AND d.deleted_at IS NULL
                    """
                ),
                {"id": document_id},
            ).mappings().first()
        if not row or not row["storage_key"]:
            raise LookupError("document source not found")
        return {
            "file_name": row["file_name"],
            "media_type": row["media_type"] or "application/octet-stream",
            "version_no": row["version_no"],
            "path": self.storage.path_for(row["storage_key"]),
        }

    def create_conversation(self, knowledge_base_scope: list[str], document_scope: list[str] | None = None, title: str = "New conversation") -> dict[str, Any]:
        conversation_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO conversations (id,title,knowledge_base_scope,document_scope) VALUES (:id,:title,CAST(:kb AS jsonb),CAST(:docs AS jsonb))"), {"id": conversation_id, "title": title, "kb": json.dumps(knowledge_base_scope), "docs": json.dumps(document_scope or [])})
        return {"id": str(conversation_id), "title": title, "knowledge_base_scope": knowledge_base_scope, "document_scope": document_scope or []}

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id,title,knowledge_base_scope,document_scope,created_at,updated_at FROM conversations WHERE deleted_at IS NULL ORDER BY updated_at DESC")).mappings()
            return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT id,title,knowledge_base_scope,document_scope,created_at,updated_at FROM conversations WHERE id=:id AND deleted_at IS NULL"), {"id": conversation_id}).mappings().first()
            return dict(row) if row else None

    def update_conversation(self, conversation_id: str, title: str | None = None, knowledge_base_scope: list[str] | None = None, document_scope: list[str] | None = None) -> dict[str, Any] | None:
        values: dict[str, Any] = {"id": conversation_id}
        assignments: list[str] = []
        if title is not None:
            assignments.append("title=:title")
            values["title"] = title
        if knowledge_base_scope is not None:
            assignments.append("knowledge_base_scope=CAST(:kb AS jsonb)")
            values["kb"] = json.dumps(knowledge_base_scope)
        if document_scope is not None:
            assignments.append("document_scope=CAST(:docs AS jsonb)")
            values["docs"] = json.dumps(document_scope)
        if assignments:
            with self.engine.begin() as conn:
                result = conn.execute(text(f"UPDATE conversations SET {', '.join(assignments)},updated_at=now() WHERE id=:id AND deleted_at IS NULL"), values)
                if result.rowcount == 0:
                    return None
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(text("UPDATE conversations SET deleted_at=now(),updated_at=now() WHERE id=:id AND deleted_at IS NULL"), {"id": conversation_id})
            return result.rowcount == 1

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return the conversation transcript with its frozen citation labels.

        Assistant rows carry `run_id`, and `citations` is read back from the
        authoritative `answer.completed` run event so reopening a historical
        conversation re-exposes the exact same labels for citation readback.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT m.id,m.conversation_id,m.role,m.content,m.created_at,m.run_id,
                       COALESCE(ev.citations, '[]'::jsonb) AS citations,
                       presentation.payload AS presentation
                FROM conversation_messages m
                JOIN conversations c ON c.id=m.conversation_id AND c.deleted_at IS NULL
                LEFT JOIN rag_runs r ON r.id=m.run_id AND r.conversation_id=m.conversation_id
                LEFT JOIN LATERAL (
                    SELECT e.payload->'presentation' AS payload
                    FROM retrieval_events e
                    WHERE e.run_id=r.id AND e.event_type='run.metrics'
                      AND m.role='user' AND r.status='failed'
                      AND r.error_code='NO_CANDIDATES' AND r.completed_at IS NOT NULL
                      AND r.knowledge_base_scope=c.knowledge_base_scope
                      AND r.document_scope=c.document_scope
                      AND e.payload->>'error'='NO_CANDIDATES'
                      AND e.payload->'citations'='[]'::jsonb
                    ORDER BY e.seq DESC LIMIT 1
                ) presentation ON true
                LEFT JOIN LATERAL (
                    SELECT e.payload->'citations' AS citations
                    FROM retrieval_events e
                    WHERE e.run_id=m.run_id AND m.role='assistant' AND e.event_type='answer.completed'
                    ORDER BY e.seq DESC LIMIT 1
                ) ev ON true
                WHERE m.conversation_id=:id ORDER BY m.created_at
            """), {"id": conversation_id}).mappings()
            return [self._message_row(row) for row in rows]

    @staticmethod
    def _message_row(row: Any) -> dict[str, Any]:
        message = dict(row)
        if message.get("run_id") is not None:
            message["run_id"] = str(message["run_id"])
        citations = message.get("citations")
        message["citations"] = list(citations) if isinstance(citations, list) else []
        presentation = message.pop("presentation", None)
        if (message.get("role") == "user" and message.get("run_id")
                and not message["citations"] and isinstance(presentation, dict)
                and presentation.get("kind") == "clarification"
                and presentation.get("clarification_required") is True
                and isinstance(presentation.get("text"), str) and presentation["text"].strip()):
            message["presentation"] = {"kind": "clarification", "text": presentation["text"],
                                       "clarification_required": True}
        return message

    def completed_history_context(self, conversation_id: str, kb_scope: list[str],
                                  document_scope: list[str], *, current_run_id: str,
                                  limit: int = 3) -> dict[str, Any]:
        from backend.app.ports.persistence import bounded_completed_history

        with self.engine.connect() as conn:
            current = conn.execute(text("""
                SELECT r.created_at FROM rag_runs r
                JOIN conversations c ON c.id=r.conversation_id AND c.deleted_at IS NULL
                WHERE r.id=:run AND r.conversation_id=:conversation
                  AND r.knowledge_base_scope=CAST(:kb AS jsonb)
                  AND r.document_scope=CAST(:docs AS jsonb)
            """), {"run": current_run_id, "conversation": conversation_id,
                    "kb": json.dumps(kb_scope), "docs": json.dumps(document_scope)}).mappings().first()
            if current is None:
                return {"turns": (), "blocked_reason": "HISTORY_UNAVAILABLE"}
            cutoff = current["created_at"]
            rows = list(conn.execute(text("""
                SELECT r.id AS run_id,r.conversation_id,r.q0,r.status,r.error_code,
                       r.knowledge_base_scope,r.document_scope,r.created_at,r.completed_at,
                       EXISTS (SELECT 1 FROM retrieval_events e WHERE e.run_id=r.id
                         AND e.event_type='answer.completed'
                         AND e.payload->>'error_code' IS NULL) AS answer_completed
                FROM rag_runs r
                WHERE r.conversation_id=:conversation AND r.id<>:run AND r.created_at<:cutoff
                ORDER BY r.created_at DESC,r.id DESC LIMIT 16
            """), {"run": current_run_id, "conversation": conversation_id,
                    "cutoff": cutoff}).mappings())
        return bounded_completed_history(rows, conversation_id=conversation_id,
                    kb_scope=kb_scope, document_scope=document_scope,
                    current_run_id=current_run_id, cutoff=cutoff, limit=limit)

    def last_completed_question_context(self, conversation_id: str, kb_scope: list[str], document_scope: list[str]) -> dict[str, str] | None:
        """Completed q0 and provenance, in the same conversation and exact scope."""
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT q0, id AS run_id FROM rag_runs
                WHERE conversation_id=:conversation AND status='completed'
                  AND knowledge_base_scope=CAST(:kb AS jsonb)
                  AND document_scope=CAST(:docs AS jsonb)
                ORDER BY created_at DESC LIMIT 1
            """), {"conversation": conversation_id, "kb": json.dumps(kb_scope),
                    "docs": json.dumps(document_scope)}).mappings().first()
            return {"q0": row["q0"], "run_id": str(row["run_id"])} if row is not None else None

    def last_completed_question(self, conversation_id: str, kb_scope: list[str], document_scope: list[str]) -> str | None:
        """Compatibility for existing callers that only need completed q0."""
        context = self.last_completed_question_context(conversation_id, kb_scope, document_scope)
        return context["q0"] if context else None

    def append_message(self, conversation_id: str, role: str, content: str, run_id: str | None = None) -> dict[str, Any]:
        message_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO conversation_messages (id,conversation_id,role,content,run_id) VALUES (:id,:conversation_id,:role,:content,:run_id)"), {"id": message_id, "conversation_id": conversation_id, "role": role, "content": content, "run_id": run_id})
            conn.execute(text("UPDATE conversations SET updated_at=now() WHERE id=:id"), {"id": conversation_id})
        return {"id": str(message_id), "conversation_id": conversation_id, "role": role, "content": content, "run_id": run_id}

    def create_run(self, conversation_id: str | None, kb_scope: list[str], document_scope: list[str], q0: str) -> str:
        run_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO rag_runs (id,conversation_id,knowledge_base_scope,document_scope,q0,status) VALUES (:id,:conversation_id,CAST(:kb AS jsonb),CAST(:docs AS jsonb),:q0,'running')"), {"id": run_id, "conversation_id": conversation_id, "kb": json.dumps(kb_scope), "docs": json.dumps(document_scope), "q0": q0})
        return str(run_id)

    def create_run_once(self, conversation_id: str, kb_scope: list[str],
                        document_scope: list[str], q0: str, request_id: str) -> tuple[str, bool]:
        """Existing UUID primary key atomically claims one product request; no migration."""
        identity = uuid.UUID(request_id)
        with self.engine.begin() as conn:
            inserted = conn.execute(text("""INSERT INTO rag_runs
                (id,conversation_id,knowledge_base_scope,document_scope,q0,status)
                VALUES (:id,:conversation_id,CAST(:kb AS jsonb),CAST(:docs AS jsonb),:q0,'running')
                ON CONFLICT (id) DO NOTHING RETURNING id"""),
                {"id": identity, "conversation_id": conversation_id, "kb": json.dumps(kb_scope),
                 "docs": json.dumps(document_scope), "q0": q0}).scalar()
            if inserted is not None:
                return str(identity), True
            row = conn.execute(text("""SELECT conversation_id,knowledge_base_scope,document_scope,q0
                FROM rag_runs WHERE id=:id"""), {"id": identity}).mappings().one()
            if (str(row["conversation_id"]) != conversation_id or row["knowledge_base_scope"] != kb_scope
                    or row["document_scope"] != document_scope or row["q0"] != q0):
                raise ValueError("REQUEST_ID_CONFLICT")
            return str(identity), False

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one run event with a gap-free per-run sequence number.

        The counter lives on `rag_runs` and is incremented with a single
        `UPDATE ... RETURNING`, so numbering is O(1) and cannot race, unlike the
        previous `SELECT MAX(seq)+1` which scanned every existing event.
        """
        event_id = uuid.uuid4()
        with self.engine.begin() as conn:
            seq = conn.execute(
                text("UPDATE rag_runs SET next_event_seq=next_event_seq+1 WHERE id=:id RETURNING next_event_seq-1"),
                {"id": run_id},
            ).scalar_one()
            conn.execute(text("INSERT INTO retrieval_events (id,run_id,seq,event_type,payload) VALUES (:id,:run_id,:seq,:event_type,CAST(:payload AS jsonb))"), {"id": event_id, "run_id": run_id, "seq": seq, "event_type": event_type, "payload": json.dumps(payload, ensure_ascii=False)})
        return {"id": str(event_id), "run_id": run_id, "seq": seq, "event": event_type, "data": payload}

    def list_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT seq,event_type,payload,created_at FROM retrieval_events WHERE run_id=:run_id AND seq>:after ORDER BY seq"), {"run_id": run_id, "after": after_seq}).mappings()
            return [{"seq": row["seq"], "event": row["event_type"], "data": row["payload"], "created_at": row["created_at"]} for row in rows]

    def persist_retrieval_hits(self, run_id: str, items: list[Any]) -> None:
        if not items:
            return
        with self.engine.begin() as conn:
            for item in items:
                conn.execute(text("INSERT INTO retrieval_hits (id,run_id,chunk_id,rank,raw_score,fused_score,sources) VALUES (:id,:run_id,:chunk_id,:rank,:raw_score,:fused_score,CAST(:sources AS jsonb)) ON CONFLICT (run_id,chunk_id) DO UPDATE SET rank=EXCLUDED.rank,fused_score=EXCLUDED.fused_score"), {"id": uuid.uuid4(), "run_id": run_id, "chunk_id": item.chunk.chunk_id, "rank": item.hit.rank, "raw_score": item.hit.raw_score, "fused_score": item.hit.fused_score, "sources": json.dumps(list(item.hit.sources))})

    def persist_evidence(self, run_id: str, snapshots: list[Any]) -> None:
        with self.engine.begin() as conn:
            for snapshot in snapshots:
                conn.execute(text("INSERT INTO answer_evidence (id,run_id,label,version_id,chunk_id,quote,quote_sha256,locator) VALUES (:id,:run_id,:label,:version_id,:chunk_id,:quote,:quote_sha256,CAST(:locator AS jsonb)) ON CONFLICT (run_id,label) DO NOTHING"), {"id": uuid.uuid4(), "run_id": run_id, "label": snapshot.label, "version_id": snapshot.version_id, "chunk_id": snapshot.chunk_id, "quote": snapshot.quote, "quote_sha256": snapshot.quote_sha256, "locator": json.dumps(snapshot.locator)})

    def finalize_answer(
        self,
        *,
        run_id: str,
        conversation_id: str | None,
        answer: str,
        citations: tuple[str, ...],
        snapshots: tuple[Any, ...],
        error_code: str | None,
        mode: str,
        agent_terminal: tuple[str, str | None, int] | None = None,
    ) -> bool:
        with self.engine.begin() as conn:
            status = conn.execute(text("SELECT status FROM rag_runs WHERE id=:id FOR UPDATE"), {"id": run_id}).scalar()
            if status not in {"created", "running"}:
                return False
            terminal_status = "completed" if error_code is None else "failed"
            if agent_terminal is not None:
                agent_status = conn.execute(text("SELECT status FROM agent_runs WHERE id=:id FOR UPDATE"), {"id": run_id}).scalar()
                if agent_status != "running":
                    return False
                if agent_terminal[0] != terminal_status:
                    raise ValueError("RAG and Agent terminal statuses disagree")

            def append_final_event(event_type: str, payload: dict[str, Any]) -> None:
                seq = conn.execute(
                    text("UPDATE rag_runs SET next_event_seq=next_event_seq+1 WHERE id=:id RETURNING next_event_seq-1"),
                    {"id": run_id},
                ).scalar_one()
                conn.execute(
                    text("INSERT INTO retrieval_events (id,run_id,seq,event_type,payload) VALUES (:id,:run_id,:seq,:event_type,CAST(:payload AS jsonb))"),
                    {"id": uuid.uuid4(), "run_id": run_id, "seq": seq, "event_type": event_type,
                     "payload": json.dumps(payload, ensure_ascii=False)},
                )

            append_final_event("retrieval.completed", {"count": len(citations), "error_code": error_code, "mode": mode})
            if error_code is None:
                for snapshot in snapshots:
                    conn.execute(
                        text("INSERT INTO answer_evidence (id,run_id,label,version_id,chunk_id,quote,quote_sha256,locator) VALUES (:id,:run_id,:label,:version_id,:chunk_id,:quote,:quote_sha256,CAST(:locator AS jsonb))"),
                        {"id": uuid.uuid4(), "run_id": run_id, "label": snapshot.label,
                         "version_id": snapshot.version_id, "chunk_id": snapshot.chunk_id,
                         "quote": snapshot.quote, "quote_sha256": snapshot.quote_sha256,
                         "locator": json.dumps(snapshot.locator)},
                    )
                if snapshots:
                    append_final_event("evidence.frozen", {"labels": [snapshot.label for snapshot in snapshots]})
                append_final_event("answer.completed", {"error_code": None, "citations": list(citations)})
                if conversation_id is not None:
                    conn.execute(
                        text("INSERT INTO conversation_messages (id,conversation_id,role,content,run_id) VALUES (:id,:conversation_id,'assistant',:content,:run_id)"),
                        {"id": uuid.uuid4(), "conversation_id": conversation_id, "content": answer, "run_id": run_id},
                    )
                    conn.execute(text("UPDATE conversations SET updated_at=now() WHERE id=:id"), {"id": conversation_id})
            else:
                append_final_event("run.failed", {"error_code": error_code, "citations": []})
            if agent_terminal is not None:
                conn.execute(
                    text("UPDATE agent_runs SET status=:status,step_count=(SELECT count(*) FROM agent_steps WHERE agent_run_id=:id),cost_microunits=:cost,error_code=:error_code,completed_at=clock_timestamp() WHERE id=:id"),
                    {"id": run_id, "status": agent_terminal[0], "error_code": agent_terminal[1], "cost": agent_terminal[2]},
                )
            conn.execute(
                text("UPDATE rag_runs SET status=:status,error_code=:error_code,completed_at=clock_timestamp() WHERE id=:id"),
                {"id": run_id, "status": terminal_status, "error_code": error_code},
            )
            return True

    def complete_run(self, run_id: str, status: str, error_code: str | None = None) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                text("UPDATE rag_runs SET status=:status,error_code=:error_code,completed_at=clock_timestamp() WHERE id=:id AND status IN ('created','running')"),
                {"id": run_id, "status": status, "error_code": error_code},
            )
            return result.rowcount == 1

    def cancel_run(self, run_id: str) -> bool:
        with self.engine.begin() as conn:
            status = conn.execute(text("SELECT status FROM rag_runs WHERE id=:id FOR UPDATE"), {"id": run_id}).scalar()
            if status not in {"created", "running"}:
                return False
            agent_status = conn.execute(text("SELECT status FROM agent_runs WHERE id=:id FOR UPDATE"), {"id": run_id}).scalar()
            if agent_status not in {None, "running", "cancelled"}:
                return False
            conn.execute(text("UPDATE rag_runs SET status='cancelled',error_code='CANCELLED',completed_at=clock_timestamp() WHERE id=:id"), {"id": run_id})
            if agent_status == "running":
                conn.execute(text("UPDATE agent_runs SET status='cancelled',error_code='CANCELLED',completed_at=clock_timestamp() WHERE id=:id"), {"id": run_id})
            seq = conn.execute(text("UPDATE rag_runs SET next_event_seq=next_event_seq+1 WHERE id=:id RETURNING next_event_seq-1"), {"id": run_id}).scalar_one()
            conn.execute(
                text("INSERT INTO retrieval_events (id,run_id,seq,event_type,payload) VALUES (:id,:run_id,:seq,'run.failed',CAST(:payload AS jsonb))"),
                {"id": uuid.uuid4(), "run_id": run_id, "seq": seq,
                 "payload": json.dumps({"error_code": "CANCELLED", "citations": []})},
            )
            return True

    def is_cancelled(self, run_id: str) -> bool:
        with self.engine.connect() as conn:
            status = conn.execute(text("SELECT status FROM rag_runs WHERE id=:id"), {"id": run_id}).scalar()
            return status == "cancelled"

    def get_citation(self, run_id: str, citation_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("""SELECT ae.label,ae.version_id,ae.chunk_id,ae.quote,ae.quote_sha256,ae.locator,
                d.id AS document_id,d.active_version_id,c.content
                FROM answer_evidence ae JOIN chunks c ON c.id=ae.chunk_id AND c.version_id=ae.version_id
                JOIN documents d ON d.id=c.document_id
                WHERE ae.run_id=:run_id AND ae.label=:label"""), {"run_id": run_id, "label": citation_id}).mappings().first()
            if not row:
                return None
            quote_hash = hashlib.sha256(row["quote"].encode("utf-8")).hexdigest()
            if quote_hash != row["quote_sha256"] or row["quote"] not in row["content"]:
                raise ValueError("citation evidence integrity check failed")
            return {"citation_id": row["label"], "label": row["label"], "version_id": str(row["version_id"]), "chunk_id": str(row["chunk_id"]), "quote": row["quote"], "locator": row["locator"], "current_status": "current" if row["active_version_id"] == row["version_id"] else "superseded"}
