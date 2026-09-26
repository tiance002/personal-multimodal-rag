from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.agent_policy import ALLOWED_READ_TOOLS
from backend.app.domain.errors import ScopeViolation
from backend.app.domain.scope import Scope

# Upper bound for the document-scope lookup fallback. It exists only to resolve
# a knowledge base id for `read_document`; it must never scan a whole corpus.
_SCOPE_LOOKUP_LIMIT = 200


class ToolDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    data: Any


class KnowledgeToolGateway:
    """Closed set of read-only knowledge tools, wired with explicit dependencies.

    Each capability is injected rather than discovered at runtime, so the
    supported surface is visible from the constructor and a missing capability
    fails loudly instead of silently returning an empty result.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever | None = None,
        content_reader: Callable[[str], str] | None = None,
        document_lister: Callable[[str], list[dict[str, Any]]] | None = None,
        graph_query: Callable[[Scope, str, int], Any] | None = None,
    ) -> None:
        self.retriever = retriever
        self.content_reader = content_reader
        self.document_lister = document_lister
        self.graph_query = graph_query

    def invoke(self, tool_name: str, args: dict[str, Any], scope: Scope) -> ToolResult:
        if tool_name not in ALLOWED_READ_TOOLS:
            raise ToolDenied("TOOL_NOT_ALLOWED")
        try:
            if tool_name == "list_documents":
                return ToolResult(tool_name, {"items": self._list_documents(args, scope)})
            if tool_name == "search_knowledge":
                return ToolResult(tool_name, {"items": self._search_knowledge(args, scope)})
            if tool_name == "read_document":
                return ToolResult(tool_name, self._read_document(args, scope))
            if tool_name == "query_knowledge_graph":
                return ToolResult(tool_name, {"items": self._query_graph(args, scope)})
        except ScopeViolation as exc:
            raise ToolDenied("SCOPE_VIOLATION") from exc
        raise ToolDenied("TOOL_NOT_ALLOWED")

    def _list_documents(self, args: dict[str, Any], scope: Scope) -> list[dict[str, Any]]:
        if self.document_lister is None:
            raise ToolDenied("DOCUMENT_LIST_UNAVAILABLE")
        limit = max(1, min(int(args.get("limit", 50)), 100))
        items: list[dict[str, Any]] = []
        for knowledge_base_id in sorted(scope.knowledge_base_ids):
            items.extend(self.document_lister(knowledge_base_id))
        if scope.document_ids:
            items = [item for item in items if str(item.get("id")) in scope.document_ids]
        return items[:limit]

    def _search_knowledge(self, args: dict[str, Any], scope: Scope) -> list[dict[str, Any]]:
        if self.retriever is None:
            return []
        result = self.retriever.retrieve(scope, str(args.get("query", args.get("question", ""))))
        return [
            {
                "chunk_id": item.chunk.chunk_id,
                "document_id": item.chunk.document_id,
                "version_id": item.chunk.version_id,
                "locator": item.chunk.locator,
                "quote": item.chunk.content,
            }
            for item in result.items
        ]

    def _read_document(self, args: dict[str, Any], scope: Scope) -> dict[str, Any]:
        knowledge_base_id = str(args.get("knowledge_base_id", ""))
        document_id = str(args.get("document_id", ""))
        if not knowledge_base_id:
            if self.retriever is None:
                raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
            for chunk in self.retriever.repository.list_active_chunks(scope, limit=_SCOPE_LOOKUP_LIMIT):
                if chunk.document_id == document_id:
                    knowledge_base_id = chunk.knowledge_base_id
                    break
        scope.assert_contains(knowledge_base_id, document_id)
        if self.content_reader is None:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        return {"document_id": document_id, "content": self.content_reader(document_id)}

    def _query_graph(self, args: dict[str, Any], scope: Scope) -> list[Any]:
        if self.graph_query is None:
            raise ToolDenied("GRAPH_UNAVAILABLE")
        result = self.graph_query(scope, str(args.get("entity_name", "")), int(args.get("depth", 1)))
        return [getattr(edge, "__dict__", edge) for edge in result]
