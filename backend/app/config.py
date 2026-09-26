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
    local_query_enabled: bool = True
    inline_ingestion_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    storage_root: Path = Path("var/storage")
    database_url: str = "postgresql+psycopg://rag:rag@127.0.0.1:5432/rag"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "ornith-1.5:9b"
    ollama_embedding_model: str = "bge-m3:latest"
    monthly_cloud_budget_microunits: int = 0
    max_upload_bytes: int = 50 * 1024 * 1024
    max_chunk_chars: int = 1200
    chunk_overlap: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        port_raw = os.getenv("RAG_PORT", str(cls.port))
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError("RAG_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("RAG_PORT must be between 1 and 65535")
        return cls(
            service_name=os.getenv("RAG_SERVICE_NAME", cls.service_name),
            cloud_enabled=_env_bool("RAG_CLOUD_ENABLED", False),
            local_query_enabled=_env_bool("RAG_LOCAL_QUERY_ENABLED", True),
            inline_ingestion_enabled=_env_bool("RAG_INLINE_INGESTION", True),
            host=os.getenv("RAG_HOST", cls.host),
            port=port,
            storage_root=Path(os.getenv("RAG_STORAGE_ROOT", str(cls.storage_root))),
            database_url=os.getenv("RAG_DATABASE_URL", cls.database_url),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_chat_model=os.getenv("OLLAMA_CHAT_MODEL", cls.ollama_chat_model),
            ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", cls.ollama_embedding_model),
            monthly_cloud_budget_microunits=int(os.getenv("RAG_MONTHLY_CLOUD_BUDGET_MICROUNITS", str(cls.monthly_cloud_budget_microunits))),
            max_upload_bytes=int(os.getenv("RAG_MAX_UPLOAD_BYTES", str(cls.max_upload_bytes))),
            max_chunk_chars=int(os.getenv("RAG_MAX_CHUNK_CHARS", str(cls.max_chunk_chars))),
            chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", str(cls.chunk_overlap))),
        )
