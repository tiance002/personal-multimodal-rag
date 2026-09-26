from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


class RouteStore:
    def __init__(self) -> None:
        self.conversation = {
            "id": "conversation-1",
            "title": "Conversation",
            "knowledge_base_scope": ["kb-a"],
            "document_scope": [],
        }

    def get_document(self, document_id: str) -> dict[str, object] | None:
        return {"id": document_id, "knowledge_base_id": "kb-a"} if document_id == "doc-a" else None

    def get_document_access(self, document_id: str) -> dict[str, object] | None:
        if document_id == "doc-b":
            return {"id": document_id, "knowledge_base_id": "kb-b", "deleted_at": None}
        return {"id": document_id, "knowledge_base_id": "kb-a", "deleted_at": None} if document_id == "doc-a" else None

    def get_knowledge_base(self, kb_id: str) -> dict[str, object] | None:
        return {"id": kb_id, "graph_enabled": False} if kb_id in {"kb-a", "kb-b"} else None

    def create_conversation(self, knowledge_base_scope, document_scope, title):
        return {**self.conversation, "knowledge_base_scope": knowledge_base_scope, "document_scope": document_scope, "title": title}

    def get_conversation(self, conversation_id: str):
        return self.conversation if conversation_id == self.conversation["id"] else None

    def update_conversation(self, conversation_id, **fields):
        self.conversation = {**self.conversation, **fields}
        return self.conversation


class GraphStub:
    def get_document_graph(self, document_id: str):
        raise AssertionError("disabled graph must not be queried")


def _client(store: RouteStore) -> TestClient:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    container = SimpleNamespace(settings=settings, store=store, graph=GraphStub())
    return TestClient(create_app(settings, container=container))


def test_graph_route_returns_structured_disabled_error_by_default() -> None:
    with _client(RouteStore()) as client:
        response = client.get("/api/v1/documents/doc-a/graph")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "GRAPH_DISABLED",
        "message": "graph feature is disabled for this knowledge base",
        "details": {"knowledge_base_id": "kb-a"},
    }


def test_create_conversation_rejects_document_from_another_knowledge_base() -> None:
    with _client(RouteStore()) as client:
        response = client.post(
            "/api/v1/conversations",
            json={"knowledge_base_scope": ["kb-a"], "document_scope": ["doc-b"], "title": "bad scope"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DOCUMENT_SCOPE"


def test_patch_conversation_rejects_document_from_another_knowledge_base() -> None:
    with _client(RouteStore()) as client:
        response = client.patch(
            "/api/v1/conversations/conversation-1",
            json={"document_scope": ["doc-b"]},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DOCUMENT_SCOPE"


def test_message_rejects_a_scope_that_changed_after_the_browser_selected_it() -> None:
    with _client(RouteStore()) as client:
        response = client.post(
            "/api/v1/conversations/conversation-1/messages",
            json={
                "content": "question for B",
                "mode": "quick",
                "expected_knowledge_base_scope": ["kb-b"],
                "expected_document_scope": [],
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONVERSATION_SCOPE_CHANGED"
