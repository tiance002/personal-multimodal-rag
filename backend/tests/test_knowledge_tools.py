import pytest

from backend.app.application.knowledge_tools import KnowledgeToolGateway, ToolDenied
from backend.app.domain.scope import Scope


def test_list_documents_requires_an_injected_lister():
    with pytest.raises(ToolDenied, match="DOCUMENT_LIST_UNAVAILABLE"):
        KnowledgeToolGateway().invoke("list_documents", {}, Scope.from_ids(["kb"]))


def test_read_document_requires_an_injected_reader():
    with pytest.raises(ToolDenied, match="DOCUMENT_NOT_AVAILABLE"):
        KnowledgeToolGateway().invoke("read_document", {"knowledge_base_id": "kb", "document_id": "doc"}, Scope.from_ids(["kb"]))


def test_graph_tool_requires_an_injected_query():
    with pytest.raises(ToolDenied, match="GRAPH_UNAVAILABLE"):
        KnowledgeToolGateway().invoke("query_knowledge_graph", {"entity_name": "x"}, Scope.from_ids(["kb"]))


def test_search_knowledge_without_a_retriever_returns_no_items():
    result = KnowledgeToolGateway().invoke("search_knowledge", {"question": "q"}, Scope.from_ids(["kb"]))

    assert result.data == {"items": []}


def test_list_documents_is_bounded_by_scope_and_limit():
    gateway = KnowledgeToolGateway(document_lister=lambda kb_id: [{"id": f"{kb_id}-{index}"} for index in range(10)])

    result = gateway.invoke("list_documents", {"limit": 3}, Scope.from_ids(["kb-a"]))

    assert len(result.data["items"]) == 3
    assert all(item["id"].startswith("kb-a") for item in result.data["items"])


def test_list_documents_applies_the_document_filter():
    gateway = KnowledgeToolGateway(document_lister=lambda kb_id: [{"id": "doc-a"}, {"id": "doc-b"}])

    result = gateway.invoke("list_documents", {}, Scope.from_ids(["kb"], ["doc-b"]))

    assert result.data["items"] == [{"id": "doc-b"}]
