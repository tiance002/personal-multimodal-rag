"""Composition root.

Every concrete adapter is constructed here and nowhere else, so the dependency
direction stays `api -> application -> domain/ports` with infrastructure wired
only at the outermost edge. `backend.app.main` is the process entry point; this
module is the only place that knows which implementation satisfies which port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, create_engine

from backend.app.adapters.langfuse_tracing import LangfuseObservability
from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.budget import PostgresBudgetGate
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval_profile import reference_profile
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.retrieval import HybridRetriever
from backend.app.config import Settings


_MODEL_UNSET = object()


@dataclass(frozen=True)
class Container:
    """Fully wired object graph for one application instance."""

    settings: Settings
    engine: Engine
    storage: ContentAddressedStorage
    store: PostgresKnowledgeRepository
    graph: PostgresGraphRepository
    agent: PostgresAgentRepository
    ollama: Any
    budget_gate: PostgresBudgetGate
    knowledge_gateway: KnowledgeGateway
    quick_chain: LangChainQuickChain
    smart_agent: LangChainAgentAdapter | None
    langfuse: LangfuseObservability | None = None


def build_container(settings: Settings, *, model: Any = _MODEL_UNSET, agent_model: Any = _MODEL_UNSET) -> Container:
    """Build the object graph once, eagerly, with no request-time assembly.

    Building eagerly (rather than lazily on first request) removes the previous
    unguarded lazy initialisation, which could construct two engines and two
    budget gates under concurrent first requests.
    """
    profile = reference_profile()["parameters"]
    unavailable = [name for name in ("rerank_enabled", "mmr_enabled", "query_rewrite_enabled", "cloud_fallback_enabled")
                   if getattr(settings, name)]
    if unavailable:
        raise ValueError("optional adapters are not implemented: " + ", ".join(unavailable))
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    storage = ContentAddressedStorage(settings.storage_root)
    langfuse = LangfuseObservability(
        enabled=settings.langfuse_enabled,
        cloud_egress_enabled=settings.cloud_enabled,
        capture_content=settings.langfuse_capture_content,
        base_url=settings.langfuse_base_url,
    )
    ollama = (
        OllamaGateway(
            settings.ollama_base_url,
            settings.ollama_chat_model,
            settings.ollama_embedding_model,
            observability=langfuse,
        )
        if model is _MODEL_UNSET
        else model
    )
    store = PostgresKnowledgeRepository(
        engine,
        storage,
        embedding_provider=ollama,
        max_chunk_chars=settings.max_chunk_chars,
        chunk_overlap=settings.chunk_overlap,
    )
    if agent_model is _MODEL_UNSET:
        if model is None:
            agent_model_value = None
        else:
            from langchain_ollama import ChatOllama

            agent_model_value = ChatOllama(
                model=settings.ollama_chat_model,
                base_url=settings.ollama_base_url,
                temperature=0,
                reasoning=False,
                seed=0,
                num_predict=512,
                num_ctx=8192,
            )
    else:
        agent_model_value = agent_model
    budget_gate = PostgresBudgetGate(engine, settings.monthly_cloud_budget_microunits)
    knowledge_gateway = KnowledgeGateway(
        HybridRetriever(store, embedding_provider=ollama, mode=settings.retrieval_mode,
                        top_k=profile["top_k"], candidate_k=profile["candidate_k"],
                        rrf_k=profile["rrf_k"], source_weights=profile["source_weights"]),
        evidence_service=EvidenceService(context_builder=ContextBuilder(profile["context_max_chars"])),
        observability=langfuse,
    )
    quick_chain = LangChainQuickChain(
        knowledge_gateway,
        answer_gateway=ollama if settings.local_answer_enabled else None,
        budget_gate=budget_gate,
    )
    return Container(
        settings=settings,
        engine=engine,
        storage=storage,
        store=store,
        graph=PostgresGraphRepository(engine, storage),
        agent=PostgresAgentRepository(engine),
        ollama=ollama,
        budget_gate=budget_gate,
        knowledge_gateway=knowledge_gateway,
        quick_chain=quick_chain,
        smart_agent=LangChainAgentAdapter(agent_model_value, observability=langfuse)
        if agent_model_value is not None and settings.local_answer_enabled
        else None,
        langfuse=langfuse,
    )


__all__ = ["Container", "build_container"]
