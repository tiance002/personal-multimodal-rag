"""Composition root.

Every concrete adapter is constructed here and nowhere else, so the dependency
direction stays `api -> application -> domain/ports` with infrastructure wired
only at the outermost edge. `backend.app.main` is the process entry point; this
module is the only place that knows which implementation satisfies which port.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from sqlalchemy import Engine, create_engine

from backend.app.adapters.langfuse_tracing import LangfuseObservability
from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.models.deepseek import DeepSeekGateway
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.domain.adaptive_chunking import ChunkingConfig
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.adapters.answer_audit import LocalAnswerAudit
from backend.app.application.budget import PostgresBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval_profile import reference_profile
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.retrieval import HybridRetriever
from backend.app.adapters.office_preview import OfficePreviewAdapter
from backend.app.adapters.parsers import ParserRegistry
from backend.app.application.caption import CaptionEnricher
from backend.app.ports.office_preview import OfficePreviewProtocol
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
    office_preview: OfficePreviewProtocol | None = None
    cloud_gateway: Any | None = None
    follow_up_enabled: bool = False
    provider_factory: ProviderFactory | None = None


def build_container(settings: Settings, *, model: Any = _MODEL_UNSET, agent_model: Any = _MODEL_UNSET,
                    cloud_model: Any = _MODEL_UNSET, follow_up_enabled: bool = False,
                    caption_provider=None, caption_usage_guard=None,
                    embedding_provider=_MODEL_UNSET, model_usage_guards=None,
                    embedding_admission=None) -> Container:
    """Build the object graph once, eagerly, with no request-time assembly.

    Building eagerly (rather than lazily on first request) removes the previous
    unguarded lazy initialisation, which could construct two engines and two
    budget gates under concurrent first requests.
    """
    profile = reference_profile()["parameters"]
    context_pool_options = ({"context_candidate_k": settings.context_pool_k,
                             "context_max_items": settings.context_max_items}
                            if settings.context_pool_enabled else {})
    unavailable = [name for name in ("mmr_enabled", "query_rewrite_enabled")
                   if getattr(settings, name)]
    if unavailable:
        raise ValueError("optional adapters are not implemented: " + ", ".join(unavailable))
    enabled_roles = {"embedding"} if settings.embedding_egress_enabled else set()
    if settings.cloud_enabled:
        if settings.chat_egress_enabled:
            enabled_roles.update({"chat_cheap", "chat_expensive"})
        if settings.rerank_egress_enabled:
            enabled_roles.add("rerank")
        if settings.vision_egress_enabled:
            enabled_roles.add("vision")
    provider_factory = ProviderFactory(settings.model_registry(), enabled_roles=enabled_roles,
        usage_guards=model_usage_guards, embedding_admission=embedding_admission,
        chunking_config=ChunkingConfig(general_size=settings.max_chunk_chars, general_overlap=settings.chunk_overlap))
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    budget_gate = PostgresBudgetGate(engine, settings.monthly_cloud_budget_microunits)
    if settings.cloud_cost_estimate_microunits > 0:
        for role in enabled_roles:
            provider_factory.usage_guards.setdefault(role, BudgetUsageGuard(budget_gate,
                provider_factory.registry.select(role), settings.cloud_cost_estimate_microunits))
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
    if embedding_provider is _MODEL_UNSET:
        # Preserve explicit legacy test injection; normal production selection
        # is independent of local Chat / Query Expansion.
        embedding_provider = (model if model is not _MODEL_UNSET else
            ollama if settings.embedding_backend == "ollama" else provider_factory.build("embedding"))
    store = PostgresKnowledgeRepository(
        engine,
        storage,
        embedding_provider=embedding_provider,
        parsers=ParserRegistry(xlsx_first_row_as_header=settings.xlsx_first_row_as_header,
            caption_enricher=CaptionEnricher(caption_provider,usage_guard=caption_usage_guard,enabled=True)
                if caption_provider is not None else None),
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
    knowledge_gateway = KnowledgeGateway(
        HybridRetriever(store, embedding_provider=embedding_provider, mode=settings.retrieval_mode,
                        top_k=profile["top_k"], candidate_k=profile["candidate_k"],
                        rrf_k=profile["rrf_k"], source_weights=profile["source_weights"],
                        ranker=provider_factory.build('rerank') if settings.rerank_enabled else None,
                        **context_pool_options),
        evidence_service=EvidenceService(context_builder=ContextBuilder(profile["context_max_chars"]),
                                        answer_audit=LocalAnswerAudit(settings.storage_root / "answer-audit")),
        observability=langfuse,
    )
    # Default attempt gate fails closed on the tagged validation ledger. A real
    # authorized validation entry must inject its registered gate; never bypass it.
    cloud_gateway = (DeepSeekGateway(api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        model=settings.cloud_model, cloud_enabled=True, attempt_gate=SessionAttemptGate(), gate_types=DEEPSEEK_GATE_TYPES)
        if settings.cloud_enabled and settings.chat_egress_enabled else None) if cloud_model is _MODEL_UNSET else cloud_model
    quick_chain = LangChainQuickChain(
        knowledge_gateway,
        answer_gateway=ollama if settings.local_answer_enabled else None,
        cloud_answer_gateway=cloud_gateway,
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
        office_preview=OfficePreviewAdapter(),
        cloud_gateway=cloud_gateway,
        follow_up_enabled=follow_up_enabled,
        provider_factory=provider_factory,
    )


__all__ = ["Container", "build_container"]
