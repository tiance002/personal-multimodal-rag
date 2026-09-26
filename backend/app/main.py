from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from backend.app.api.routes import router
from backend.app.bootstrap import Container, build_container
from backend.app.config import Settings


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    resolved = settings or (container.settings if container is not None else Settings.from_env())
    resolved_container = container or build_container(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        langfuse = getattr(app.state.container, "langfuse", None)
        if langfuse is not None:
            langfuse.shutdown()

    app = FastAPI(title="Personal RAG", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved
    app.state.container = resolved_container
    app.include_router(router)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = str(uuid4())
        return await call_next(request)

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, object]:
        return {
            "data": {
                "service": resolved.service_name,
                "status": "ok",
                "cloud_enabled": resolved.cloud_enabled,
                "local_query_enabled": resolved.local_query_enabled,
            },
            "meta": {"request_id": getattr(request.state, "request_id", str(uuid4()))},
        }

    return app


app = create_app()
