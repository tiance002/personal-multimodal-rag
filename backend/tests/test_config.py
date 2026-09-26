import pytest

from backend.app.config import Settings


def test_chunk_overlap_must_be_smaller_than_the_chunk_size(monkeypatch):
    monkeypatch.setenv("RAG_MAX_CHUNK_CHARS", "500")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "500")

    with pytest.raises(ValueError, match="RAG_CHUNK_OVERLAP"):
        Settings.from_env()


def test_chunk_size_must_be_positive(monkeypatch):
    monkeypatch.setenv("RAG_MAX_CHUNK_CHARS", "0")

    with pytest.raises(ValueError, match="RAG_MAX_CHUNK_CHARS"):
        Settings.from_env()


def test_port_must_be_in_range(monkeypatch):
    monkeypatch.setenv("RAG_PORT", "70000")

    with pytest.raises(ValueError, match="RAG_PORT"):
        Settings.from_env()


def test_default_database_url_matches_the_compose_service_port():
    assert ":55432/" in Settings().database_url


def test_chunk_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("RAG_MAX_CHUNK_CHARS", "800")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "80")

    settings = Settings.from_env()

    assert settings.max_chunk_chars == 800
    assert settings.chunk_overlap == 80
