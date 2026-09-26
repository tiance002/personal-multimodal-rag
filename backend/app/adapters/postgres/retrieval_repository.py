from __future__ import annotations

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository


class PostgresRetrievalRepository(PostgresKnowledgeRepository):
    """Named adapter for the retrieval port; scope filtering lives in the SQL query."""


__all__ = ["PostgresRetrievalRepository"]
