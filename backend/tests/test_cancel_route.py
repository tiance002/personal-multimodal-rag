from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


class CancelStore:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[str] = []

    def cancel_run(self, run_id: str) -> bool:
        self.calls.append(run_id)
        return self.result


def test_cancel_route_does_not_cancel_agent_when_rag_run_is_terminal() -> None:
    rag = CancelStore(False)
    agent = CancelStore(True)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    app = create_app(settings, container=SimpleNamespace(settings=settings, store=rag, agent=agent))

    with TestClient(app) as client:
        response = client.post("/api/v1/runs/run-1/cancel")

    assert response.status_code == 404
    assert rag.calls == ["run-1"]
    assert agent.calls == []
