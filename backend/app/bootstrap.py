"""Composition root.

Every concrete adapter is constructed here and nowhere else, so the dependency
direction stays `api -> application -> domain/ports` with infrastructure wired
only at the outermost edge. `backend.app.main` is the process entry point; this
module is the only place that knows which implementation satisfies which port.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.budget import PostgresBudgetGate
from backend.app.config import Settings


@dataclass(frozen=True)
class Container:
    """Fully wired object graph for one application instance."""

    settings: Settings
    engine: Engine
    storage: ContentAddressedStorage
    store: PostgresKnowledgeRepository
    graph: PostgresGraphRepository
    agent: PostgresAgentRepository
    ollama: OllamaGateway
    budget_gate: PostgresBudgetGate


def build_container(settings: Settings) -> Container:
    """Build the object graph once, eagerly, with no request-time assembly.

    Building eagerly (rather than lazily on first request) removes the previous
    unguarded lazy initialisation, which could construct two engines and two
    budget gates under concurrent first requests.
    """
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    storage = ContentAddressedStorage(settings.storage_root)
    ollama = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_embedding_model)
    store = PostgresKnowledgeRepository(
        engine,
        storage,
        embedding_provider=ollama,
        max_chunk_chars=settings.max_chunk_chars,
        chunk_overlap=settings.chunk_overlap,
    )
    return Container(
        settings=settings,
        engine=engine,
        storage=storage,
        store=store,
        graph=PostgresGraphRepository(engine, storage),
        agent=PostgresAgentRepository(engine),
        ollama=ollama,
        budget_gate=PostgresBudgetGate(engine, settings.monthly_cloud_budget_microunits),
    )


__all__ = ["Container", "build_container"]
