from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_healthz_reports_local_service_without_external_calls():
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["service"] == "personal-rag"
    assert payload["data"]["cloud_enabled"] is False
