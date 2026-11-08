from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .archive import Archive
from .clients import SourceClient
from .config import Settings
from .routers import auth, documents, health, jobs
from .runner import Runner
from .store import JobStore


def create_app(settings: Settings | None = None, *, source_http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_http = source_http is None
    if owns_http:
        source_http = httpx.AsyncClient(base_url=settings.source_api_url, timeout=settings.http_timeout)
    source = SourceClient(source_http)
    store = JobStore(settings.data_dir / "jobs")
    runner = Runner(store, Archive(settings.data_dir / "archive"), source, settings.max_concurrent_jobs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield
        await runner.shutdown()
        if owns_http:
            await source_http.aclose()

    app = FastAPI(title="Document publishing service", lifespan=lifespan)
    app.state.settings = settings
    app.state.source = source
    app.state.store = store
    app.state.runner = runner
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    for router in (health.router, auth.router, documents.router, jobs.router):
        app.include_router(router)
    return app
