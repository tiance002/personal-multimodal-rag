from __future__ import annotations

import pytest

from backend.app.application.knowledge_tools import KnowledgeToolGateway, ToolDenied
from backend.app.domain.scope import Scope


def _document(document_id: str = "doc-b", knowledge_base_id: str = "kb-b") -> dict[str, object]:
    return {
        "id": document_id,
        "knowledge_base_id": knowledge_base_id,
        "active_version_id": "version-current",
        "index_status": "ready",
        "deleted_at": None,
    }


def test_read_document_uses_database_ownership_not_tool_supplied_kb() -> None:
    gateway = KnowledgeToolGateway(
        content_reader=lambda document_id: "private",
        document_resolver=lambda document_id: _document(document_id),
    )

    with pytest.raises(ToolDenied, match="SCOPE_VIOLATION"):
        gateway.invoke(
            "read_document",
            {"knowledge_base_id": "kb-a", "document_id": "doc-b"},
            Scope.from_ids(["kb-a"]),
        )


def test_read_document_rejects_non_active_version_and_bounds_content() -> None:
    gateway = KnowledgeToolGateway(
        content_reader=lambda document_id: "x" * 101,
        document_resolver=lambda document_id: _document(document_id),
        max_document_chars=100,
    )

    with pytest.raises(ToolDenied, match="VERSION_NOT_AUTHORIZED"):
        gateway.invoke(
            "read_document",
            {"document_id": "doc-b", "version_id": "version-old"},
            Scope.from_ids(["kb-b"]),
        )

    result = gateway.invoke("read_document", {"document_id": "doc-b"}, Scope.from_ids(["kb-b"]))

    assert len(result.data["content"]) == 100
    assert result.data["truncated"] is True
    assert result.data["version_id"] == "version-current"
