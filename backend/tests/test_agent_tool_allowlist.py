import pytest

from backend.app.application.knowledge_tools import KnowledgeToolGateway, ToolDenied
from backend.app.domain.scope import Scope


def test_shell_and_unknown_tools_are_rejected():
    gateway = KnowledgeToolGateway()

    with pytest.raises(ToolDenied, match="TOOL_NOT_ALLOWED"):
        gateway.invoke("shell_exec", {}, Scope.from_ids(["kb"]))
    with pytest.raises(ToolDenied, match="TOOL_NOT_ALLOWED"):
        gateway.invoke("unknown", {}, Scope.from_ids(["kb"]))
