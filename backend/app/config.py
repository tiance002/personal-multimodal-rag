from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    service_name: str = "personal-rag"
    cloud_enabled: bool = False
    local_query_enabled: bool = False
    retrieval_mode: str = "adaptive"
    local_answer_enabled: bool = True
    rerank_enabled: bool = False
    mmr_enabled: bool = False
    query_rewrite_enabled: bool = False
    cloud_fallback_enabled: bool = False
    inline_ingestion_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    storage_root: Path = Path("var/storage")
    database_url: str = "postgresql+psycopg://rag:rag@127.0.0.1:55432/rag"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen3.5:4b"
    ollama_embedding_model: str = "bge-m3:latest"
    monthly_cloud_budget_microunits: int = 0
    max_upload_bytes: int = 50 * 1024 * 1024
    max_chunk_chars: int = 1200
    chunk_overlap: int = 120
    ingestion_lease_seconds: int = 60
    langfuse_enabled: bool = False
    langfuse_capture_content: bool = False
    langfuse_base_url: str = "https://us.cloud.langfuse.com"

    @classmethod
    def from_env(cls) -> "Settings":
        port_raw = os.getenv("RAG_PORT", str(cls.port))
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError("RAG_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("RAG_PORT must be between 1 and 65535")
        max_chunk_chars = int(os.getenv("RAG_MAX_CHUNK_CHARS", str(cls.max_chunk_chars)))
        chunk_overlap = int(os.getenv("RAG_CHUNK_OVERLAP", str(cls.chunk_overlap)))
        ingestion_lease_seconds = int(os.getenv("RAG_INGESTION_LEASE_SECONDS", str(cls.ingestion_lease_seconds)))
        if max_chunk_chars <= 0:
            raise ValueError("RAG_MAX_CHUNK_CHARS must be positive")
        if not 0 <= chunk_overlap < max_chunk_chars:
            raise ValueError("RAG_CHUNK_OVERLAP must be >= 0 and < RAG_MAX_CHUNK_CHARS")
        if ingestion_lease_seconds <= 0:
            raise ValueError("RAG_INGESTION_LEASE_SECONDS must be positive")
        return cls(
            service_name=os.getenv("RAG_SERVICE_NAME", cls.service_name),
            cloud_enabled=_env_bool("RAG_CLOUD_ENABLED", False),
            local_query_enabled=_env_bool("RAG_LOCAL_QUERY_ENABLED", False),
            retrieval_mode=os.getenv("RAG_RETRIEVAL_MODE", cls.retrieval_mode),
            local_answer_enabled=_env_bool("RAG_LOCAL_ANSWER_ENABLED", True),
            rerank_enabled=_env_bool("RAG_RERANK_ENABLED", False),
            mmr_enabled=_env_bool("RAG_MMR_ENABLED", False),
            query_rewrite_enabled=_env_bool("RAG_QUERY_REWRITE_ENABLED", False),
            cloud_fallback_enabled=_env_bool("RAG_CLOUD_FALLBACK_ENABLED", False),
            inline_ingestion_enabled=_env_bool("RAG_INLINE_INGESTION", True),
            host=os.getenv("RAG_HOST", cls.host),
            port=port,
            storage_root=Path(os.getenv("RAG_STORAGE_ROOT", str(cls.storage_root))),
            database_url=os.getenv("RAG_DATABASE_URL", cls.database_url),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_chat_model=os.getenv("OLLAMA_CHAT_MODEL", cls.ollama_chat_model),
            ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", cls.ollama_embedding_model),
            monthly_cloud_budget_microunits=int(os.getenv("RAG_MONTHLY_CLOUD_BUDGET_MICROUNITS", str(cls.monthly_cloud_budget_microunits))),
            langfuse_enabled=_env_bool("RAG_LANGFUSE_ENABLED", False),
            langfuse_capture_content=_env_bool("RAG_LANGFUSE_CAPTURE_CONTENT", False),
            langfuse_base_url=os.getenv("LANGFUSE_BASE_URL", cls.langfuse_base_url).rstrip("/"),
            max_upload_bytes=int(os.getenv("RAG_MAX_UPLOAD_BYTES", str(cls.max_upload_bytes))),
            max_chunk_chars=max_chunk_chars,
            chunk_overlap=chunk_overlap,
            ingestion_lease_seconds=ingestion_lease_seconds,
        )
