from __future__ import annotations

from fastapi import FastAPI

from backend.app.config import Settings
from backend.app.main import create_app


def build_app(settings: Settings | None = None) -> FastAPI:
    """Composition-root entry point kept separate from the HTTP module."""
    return create_app(settings)
