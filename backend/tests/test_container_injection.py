from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


class StubStore:
    def list_knowledge_bases(self) -> list[dict[str, str]]:
        return [{"id": "injected-kb", "name": "injected"}]


def test_create_app_injected_container_controls_route_dependencies() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    container = SimpleNamespace(settings=settings, store=StubStore())

    app = create_app(settings, container=container)

    with TestClient(app) as client:
        response = client.get("/api/v1/knowledge-bases")

    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "injected-kb", "name": "injected"}]
    assert app.state.container is container
