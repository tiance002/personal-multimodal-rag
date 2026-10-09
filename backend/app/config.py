from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_context_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_context_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class Settings:
    memory_local_principal: str | None = None
    service_name: str = "personal-rag"
    cloud_enabled: bool = False
    model_registry_json: str | None = None
    embedding_backend: str = "siliconflow"
    embedding_egress_enabled: bool = False
    chat_egress_enabled: bool = False
    rerank_egress_enabled: bool = False
    vision_egress_enabled: bool = False
    prefer_cloud: bool = False
    cloud_model: str = "deepseek-flash"
    cloud_cost_estimate_microunits: int = 0
    local_query_enabled: bool = False
    retrieval_mode: str = "adaptive"
    local_answer_enabled: bool = True
    rerank_enabled: bool = False
    mmr_enabled: bool = False
    query_rewrite_enabled: bool = False
    cloud_fallback_enabled: bool = False
    rule_router_enabled: bool = False
    rule_router_quality_escalation: bool = True
    rule_router_abstention_escalation: bool = False
    rule_router_provider_fallback: bool = False
    redis_url: str | None = None
    live_run_ttl_seconds: int = 60
    stream_event_ttl_seconds: int = 86400
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
    max_chunk_chars: int = 512
    chunk_overlap: int = 80
    xlsx_first_row_as_header: bool = False
    ingestion_lease_seconds: int = 60
    langfuse_enabled: bool = False
    langfuse_capture_content: bool = False
    langfuse_base_url: str = "https://us.cloud.langfuse.com"
    context_pool_enabled: bool = False
    context_pool_k: int = 10
    context_max_items: int = 8

    def __post_init__(self) -> None:
        if type(self.live_run_ttl_seconds) is not int or self.live_run_ttl_seconds < 3 or type(self.stream_event_ttl_seconds) is not int or self.stream_event_ttl_seconds < 1:
            raise ValueError('STREAM_TTL_INVALID')
        if self.embedding_backend not in {"siliconflow", "ollama"}:
            raise ValueError("EMBEDDING_BACKEND_UNSUPPORTED")
        for name in ("rule_router_enabled", "rule_router_quality_escalation", "rule_router_abstention_escalation", "rule_router_provider_fallback"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("RULE_ROUTER_FLAG_INVALID")
        self.model_registry()
        for name in ("embedding_egress_enabled", "chat_egress_enabled", "rerank_egress_enabled", "vision_egress_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("MODEL_EGRESS_FLAG_INVALID")
        if type(self.xlsx_first_row_as_header) is not bool:
            raise ValueError('xlsx_first_row_as_header must be a boolean')
        if type(self.context_pool_enabled) is not bool:
            raise ValueError("context_pool_enabled must be a boolean")
        for name in ("context_pool_k", "context_max_items"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.context_max_items > self.context_pool_k:
            raise ValueError("context_max_items must not exceed context_pool_k")

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
            memory_local_principal=os.getenv('RAG_MEMORY_LOCAL_PRINCIPAL') or None,
            model_registry_json=os.getenv("RAG_MODEL_REGISTRY_JSON"),
            embedding_backend=os.getenv("RAG_EMBEDDING_BACKEND", cls.embedding_backend),
            embedding_egress_enabled=_env_context_bool("RAG_EMBEDDING_EGRESS_ENABLED", False),
            chat_egress_enabled=_env_context_bool("RAG_CHAT_EGRESS_ENABLED", False),
            rerank_egress_enabled=_env_context_bool("RAG_RERANK_EGRESS_ENABLED", False),
            vision_egress_enabled=_env_context_bool("RAG_VISION_EGRESS_ENABLED", False),
            service_name=os.getenv("RAG_SERVICE_NAME", cls.service_name),
            cloud_enabled=_env_bool("RAG_CLOUD_ENABLED", False),
            prefer_cloud=_env_bool("RAG_PREFER_CLOUD", False),
            cloud_model=os.getenv("DEEPSEEK_MODEL", cls.cloud_model),
            cloud_cost_estimate_microunits=int(os.getenv("RAG_CLOUD_COST_ESTIMATE_MICROUNITS", "0")),
            local_query_enabled=_env_bool("RAG_LOCAL_QUERY_ENABLED", False),
            retrieval_mode=os.getenv("RAG_RETRIEVAL_MODE", cls.retrieval_mode),
            local_answer_enabled=_env_bool("RAG_LOCAL_ANSWER_ENABLED", True),
            rerank_enabled=_env_bool("RAG_RERANK_ENABLED", False),
            mmr_enabled=_env_bool("RAG_MMR_ENABLED", False),
            query_rewrite_enabled=_env_bool("RAG_QUERY_REWRITE_ENABLED", False),
            cloud_fallback_enabled=_env_bool("RAG_CLOUD_FALLBACK_ENABLED", False),
            rule_router_enabled=_env_context_bool("RAG_RULE_ROUTER_ENABLED", False),
            redis_url=os.getenv('RAG_REDIS_URL') or None,
            live_run_ttl_seconds=_env_context_positive_int('RAG_LIVE_RUN_TTL_SECONDS',60),
            stream_event_ttl_seconds=_env_context_positive_int('RAG_STREAM_EVENT_TTL_SECONDS',86400),
            rule_router_quality_escalation=_env_context_bool("RAG_RULE_ROUTER_QUALITY_ESCALATION", True),
            rule_router_abstention_escalation=_env_context_bool("RAG_RULE_ROUTER_ABSTENTION_ESCALATION", False),
            rule_router_provider_fallback=_env_context_bool("RAG_RULE_ROUTER_PROVIDER_FALLBACK", False),
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
            xlsx_first_row_as_header=_env_context_bool('RAG_XLSX_FIRST_ROW_AS_HEADER', False),
            ingestion_lease_seconds=ingestion_lease_seconds,
            context_pool_enabled=_env_context_bool("RAG_CONTEXT_POOL_ENABLED", False),
            context_pool_k=_env_context_positive_int("RAG_CONTEXT_POOL_K", cls.context_pool_k),
            context_max_items=_env_context_positive_int("RAG_CONTEXT_MAX_ITEMS", cls.context_max_items),
        )

    def model_registry(self):
        from backend.app.domain.model_registry import ModelRegistry
        return (ModelRegistry.from_dict(json.loads(self.model_registry_json))
                if self.model_registry_json is not None else ModelRegistry.frozen_defaults())
