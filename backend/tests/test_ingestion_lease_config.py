from __future__ import annotations

from backend.app.config import Settings


def test_ingestion_lease_seconds_are_configurable(monkeypatch) -> None:
    monkeypatch.setenv("RAG_INGESTION_LEASE_SECONDS", "37")

    settings = Settings.from_env()

    assert settings.ingestion_lease_seconds == 37
