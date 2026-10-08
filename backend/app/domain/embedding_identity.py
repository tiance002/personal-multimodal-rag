"""Effective vector space identity; equal dimensions do not imply compatibility."""
from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class EmbeddingIdentity:
    provider: str
    model_id: str
    resolved_revision_or_unknown: str
    dimension: int
    distance_metric: str
    chunking_index_identity: str
    embedding_input_semantics_version: str = "context-header-child/v1"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def effective_identity(provider, chunking_index_identity: str) -> EmbeddingIdentity:
    return EmbeddingIdentity(
        getattr(provider, "provider_name", "ollama"),
        getattr(provider, "embedding_model", "bge-m3:latest"),
        getattr(provider, "resolved_revision", "UNKNOWN"),
        getattr(provider, "embedding_dimension", 1024), "cosine", chunking_index_identity,
        getattr(provider, "embedding_input_semantics_version", "context-header-child/v1"))
