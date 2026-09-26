from __future__ import annotations

from pathlib import Path

from backend.app.bootstrap import build_container
from backend.app.config import Settings


def test_build_container_accepts_an_explicit_model_override(tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", storage_root=tmp_path)

    container = build_container(settings, model=None)

    assert container.ollama is None
    assert container.store.embedding_provider is None
    assert container.smart_agent is None
