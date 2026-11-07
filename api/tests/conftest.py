"""Shared fixtures: the API and the Source stub run in-process over ASGITransport."""
import httpx
import pytest

import source_api
from app.auth import issue_token
from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        client_id="test-client",
        client_secret="test-secret",
        token_secret="test-token-secret-0123456789abcdef",
        data_dir=tmp_path,
    )


@pytest.fixture
def source_events() -> list:
    """Every call the stubs receive, as (service, action, id)."""
    return []


@pytest.fixture
def source_app(source_events):
    return source_api.create_app(seed=5, events=source_events)


@pytest.fixture
async def make_api(settings):
    """Builds the API against any Source transport (real stub or a failing mock)."""
    clients: list[httpx.AsyncClient] = []

    async def build(source_transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
        source_http = httpx.AsyncClient(transport=source_transport, base_url="http://source")
        clients.append(source_http)  # closed even if create_app fails
        app = create_app(settings, source_http=source_http)
        api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")
        clients.append(api)
        return api

    yield build
    for client in clients:
        await client.aclose()


@pytest.fixture
async def api(make_api, source_app):
    return await make_api(httpx.ASGITransport(app=source_app))


@pytest.fixture
def auth_headers(settings) -> dict:
    return {"Authorization": f"Bearer {issue_token(settings)}"}
