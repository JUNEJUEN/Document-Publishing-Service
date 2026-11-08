from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .archive import Archive
from .clients import SourceClient, VendorClient
from .config import Settings
from .routers import auth, documents, health, jobs
from .runner import Runner
from .store import JobStore


def create_app(
    settings: Settings | None = None,
    *,
    source_http: httpx.AsyncClient | None = None,
    vendor_http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Tests pass their own HTTP clients; otherwise the app builds and closes its own."""
    settings = settings or Settings.from_env()
    owned: list[httpx.AsyncClient] = []

    def own(url: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(base_url=url, timeout=settings.http_timeout)
        owned.append(client)
        return client

    source = SourceClient(source_http or own(settings.source_api_url))
    vendor = VendorClient(vendor_http or own(settings.vendor_api_url))
    store = JobStore(settings.data_dir / "jobs")
    runner = Runner(store, Archive(settings.data_dir / "archive"), source, vendor, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield
        await runner.shutdown()
        for client in owned:
            await client.aclose()

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
