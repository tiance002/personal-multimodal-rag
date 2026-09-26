from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_gateway import KnowledgeGateway
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
    """Closed set of read-only knowledge tools, wired with explicit dependencies.

    Each capability is injected rather than discovered at runtime, so the
    supported surface is visible from the constructor and a missing capability
    fails loudly instead of silently returning an empty result.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever | None = None,
        knowledge_gateway: KnowledgeGateway | None = None,
        content_reader: Callable[[str], str] | None = None,
        document_lister: Callable[[str], list[dict[str, Any]]] | None = None,
        document_resolver: Callable[[str], dict[str, Any] | None] | None = None,
        graph_query: Callable[[Scope, str, int], Any] | None = None,
        evidence_accumulator: EvidenceAccumulator | None = None,
        on_retrieval: Callable[[Any], None] | None = None,
        max_document_chars: int = 8_000,
    ) -> None:
        self.knowledge_gateway = knowledge_gateway or (KnowledgeGateway(retriever) if retriever is not None else None)
        self.retriever = retriever or (self.knowledge_gateway.retriever if self.knowledge_gateway is not None else None)
        self.content_reader = content_reader
        self.document_lister = document_lister
        self.document_resolver = document_resolver
        self.graph_query = graph_query
        self.evidence_accumulator = evidence_accumulator
        self.on_retrieval = on_retrieval
        self.max_document_chars = max(1, max_document_chars)

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
        if self.knowledge_gateway is None:
            return []
        result = self.knowledge_gateway.search(scope, str(args.get("query", args.get("question", ""))))
        if self.on_retrieval is not None:
            self.on_retrieval(result.items)
        if self.evidence_accumulator is not None:
            self.evidence_accumulator.add(item.chunk for item in result.items)
        return [
            {
                "chunk_id": item.chunk.chunk_id,
                "document_id": item.chunk.document_id,
                "version_id": item.chunk.version_id,
                "locator": item.chunk.locator,
                "quote": item.chunk.content,
                **({"citation_label": self.evidence_accumulator.label_for(item.chunk)} if self.evidence_accumulator is not None else {}),
            }
            for item in result.items
        ]

    def _read_document(self, args: dict[str, Any], scope: Scope) -> dict[str, Any]:
        document_id = str(args.get("document_id", ""))
        if self.content_reader is None:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        if not document_id:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        if self.document_resolver is None:
            supplied_kb = str(args.get("knowledge_base_id", ""))
            scope.assert_contains(supplied_kb, document_id)
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        document = self.document_resolver(document_id)
        if not document or document.get("deleted_at") is not None:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        knowledge_base_id = str(document.get("knowledge_base_id", ""))
        supplied_kb = str(args.get("knowledge_base_id", ""))
        if supplied_kb and supplied_kb != knowledge_base_id:
            raise ToolDenied("SCOPE_VIOLATION")
        scope.assert_contains(knowledge_base_id, document_id)
        active_version_id = str(document.get("active_version_id", ""))
        if not active_version_id or document.get("index_status") != "ready":
            raise ToolDenied("DOCUMENT_NOT_READY")
        requested_version_id = str(args.get("version_id", ""))
        if requested_version_id and requested_version_id != active_version_id:
            raise ToolDenied("VERSION_NOT_AUTHORIZED")
        content = self.content_reader(document_id)
        truncated = len(content) > self.max_document_chars
        return {
            "document_id": document_id,
            "version_id": active_version_id,
            "content": content[: self.max_document_chars],
            "truncated": truncated,
        }

    def _query_graph(self, args: dict[str, Any], scope: Scope) -> list[Any]:
        if self.graph_query is None:
            raise ToolDenied("GRAPH_UNAVAILABLE")
        result = self.graph_query(scope, str(args.get("entity_name", "")), int(args.get("depth", 1)))
        return [getattr(edge, "__dict__", edge) for edge in result]
