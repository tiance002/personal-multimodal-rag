from backend.app.application.knowledge_tools import KnowledgeToolGateway, ToolDenied
from backend.app.domain.scope import Scope


def test_document_tool_cannot_escape_conversation_scope():
    gateway = KnowledgeToolGateway(content_reader=lambda document_id: "private")

    try:
        gateway.invoke("read_document", {"document_id": "doc-b", "knowledge_base_id": "kb-b"}, Scope.from_ids(["kb-a"], ["doc-a"]))
    except ToolDenied as exc:
        assert "SCOPE_VIOLATION" in str(exc)
    else:
        raise AssertionError("out-of-scope document was accepted")
