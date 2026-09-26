from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.agent_policy import ALLOWED_READ_TOOLS
from backend.app.domain.errors import ScopeViolation
from backend.app.domain.scope import Scope


class ToolDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    data: Any


class KnowledgeToolGateway:
    def __init__(self, *, retriever: HybridRetriever | None = None, content_reader: Callable[[str], str] | None = None, document_lister: Callable[[str], list[dict[str, Any]]] | None = None, graph_query: Callable[[Scope, str, int], Any] | None = None) -> None:
        self.retriever = retriever
        self.content_reader = content_reader
        self.document_lister = document_lister
        self.graph_query = graph_query

    def invoke(self, tool_name: str, args: dict[str, Any], scope: Scope) -> ToolResult:
        if tool_name not in ALLOWED_READ_TOOLS:
            raise ToolDenied("TOOL_NOT_ALLOWED")
        try:
            if tool_name == "list_documents":
                lister = self.document_lister
                if lister is None and self.retriever is not None and hasattr(self.retriever.repository, "list_documents"):
                    lister = self.retriever.repository.list_documents
                if lister is None:
                    return ToolResult(tool_name, {"items": []})
                limit = max(1, min(int(args.get("limit", 50)), 100))
                items = []
                for knowledge_base_id in scope.knowledge_base_ids:
                    items.extend(lister(knowledge_base_id))
                if scope.document_ids:
                    items = [item for item in items if str(item.get("id")) in scope.document_ids]
                return ToolResult(tool_name, {"items": items[:limit]})
            if tool_name == "search_knowledge":
                if self.retriever is None:
                    return ToolResult(tool_name, {"items": []})
                result = self.retriever.retrieve(scope, str(args.get("query", args.get("question", ""))))
                return ToolResult(tool_name, {"items": [{"chunk_id": item.chunk.chunk_id, "document_id": item.chunk.document_id, "version_id": item.chunk.version_id, "locator": item.chunk.locator, "quote": item.chunk.content} for item in result.items]})
            if tool_name == "read_document":
                knowledge_base_id = str(args.get("knowledge_base_id", ""))
                document_id = str(args.get("document_id", ""))
                if not knowledge_base_id and self.retriever is not None:
                    for chunk in self.retriever.repository.list_active_chunks(scope):
                        if chunk.document_id == document_id:
                            knowledge_base_id = chunk.knowledge_base_id
                            break
                scope.assert_contains(knowledge_base_id, document_id)
                if self.content_reader is None:
                    raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
                return ToolResult(tool_name, {"document_id": document_id, "content": self.content_reader(document_id)})
            if tool_name == "query_knowledge_graph":
                if self.graph_query is None:
                    raise ToolDenied("GRAPH_UNAVAILABLE")
                result = self.graph_query(scope, str(args.get("entity_name", "")), int(args.get("depth", 1)))
                return ToolResult(tool_name, {"items": [getattr(edge, "__dict__", edge) for edge in result]})
        except ScopeViolation as exc:
            raise ToolDenied("SCOPE_VIOLATION") from exc
