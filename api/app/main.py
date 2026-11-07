from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import credentials_valid, issue_token, require_token, unauthorized
from .clients import SourceClient, UpstreamError
from .config import Settings


def create_app(settings: Settings | None = None, *, source_http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_http = source_http is None
    if owns_http:
        source_http = httpx.AsyncClient(base_url=settings.source_api_url, timeout=settings.http_timeout)
    source = SourceClient(source_http)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield
        if owns_http:
            await source_http.aclose()

    app = FastAPI(title="Document publishing service", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/oauth/token")
    async def token(request: Request) -> JSONResponse:
        try:
            async with request.form() as form:  # closes any uploaded temp files
                client_id, client_secret = form.get("client_id"), form.get("client_secret")
        except Exception:  # an unreadable body counts as wrong credentials
            client_id = client_secret = None
        if not (
            isinstance(client_id, str)
            and isinstance(client_secret, str)
            and credentials_valid(settings, client_id, client_secret)
        ):
            raise unauthorized("invalid client credentials")
        body = {"access_token": issue_token(settings), "token_type": "Bearer", "expires_in": settings.token_ttl_seconds}
        # Token responses must not be cached (RFC 6749, section 5.1).
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @app.get("/documents", dependencies=[Depends(require_token)])
    async def list_documents() -> list[dict]:
        try:
            return await source.list_documents()
        except UpstreamError as exc:
            raise HTTPException(502, f"Source API unavailable: {exc}") from None

    return app
