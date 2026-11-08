"""Shared fixtures: the API and both stubs run in-process over ASGITransport."""
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

import source_api
import vendor_api
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
        retry_base_delay=0,  # quick retries don't wait in tests
    )


@pytest.fixture
def events() -> list:
    """Every call the stubs receive, as (service, action, id)."""
    return []


@pytest.fixture
def source_app(events):
    return source_api.create_app(seed=5, events=events)


@pytest.fixture
def vendor_outcomes() -> list[str]:
    """The Vendor's scripted answers; override with parametrize. Once used up, every call is OK."""
    return []


@pytest.fixture
def vendor_app(events, vendor_outcomes):
    return vendor_api.create_app(outcomes=vendor_outcomes, events=events)


class ApiFactory:
    """Builds started APIs for one test, and shuts them down after it."""

    def __init__(self, settings: Settings, stack: AsyncExitStack, source_app: FastAPI, vendor_app: FastAPI):
        self._settings = settings
        self._stack = stack
        self._stubs = {"source": source_app, "vendor": vendor_app}
        self._apps: list[FastAPI] = []

    async def __call__(
        self,
        *,
        source: httpx.AsyncBaseTransport | None = None,
        vendor: httpx.AsyncBaseTransport | None = None,
        **changes,
    ) -> httpx.AsyncClient:
        """An API against the stubs, or against the given transports, with some settings changed."""
        enter = self._stack.enter_async_context

        def http(name: str, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
            transport = transport or httpx.ASGITransport(app=self._stubs[name])
            return httpx.AsyncClient(transport=transport, base_url=f"http://{name}")

        source_http = await enter(http("source", source))
        vendor_http = await enter(http("vendor", vendor))
        app = create_app(replace(self._settings, **changes), source_http=source_http, vendor_http=vendor_http)
        await enter(app.router.lifespan_context(app))
        self._apps.append(app)
        return await enter(httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api"))

    async def settle(self) -> None:
        """Waits until every job task of every built API has finished."""
        for app in self._apps:
            await app.state.runner.wait_idle()


@pytest.fixture
async def make_api(settings, source_app, vendor_app):
    async with AsyncExitStack() as stack:
        yield ApiFactory(settings, stack, source_app, vendor_app)


@pytest.fixture
async def api(make_api):
    return await make_api()


@pytest.fixture
def settle(make_api):
    return make_api.settle


@pytest.fixture
def auth_headers(settings) -> dict:
    return {"Authorization": f"Bearer {issue_token(settings)}"}


@pytest.fixture
def publish(api, auth_headers):
    """Sends POST /publish; a fresh requestId unless one is given."""

    def send(document_id: str, request_id: str | None = None):
        body = {"documentId": document_id, "requestId": request_id or uuid.uuid4().hex}
        return api.post("/publish", json=body, headers=auth_headers)

    return send


@pytest.fixture
def published_on(make_api, auth_headers):
    """Publishes a document on an API built by `make_api` and returns its finished Job object."""

    async def run(api: httpx.AsyncClient, document_id: str = "doc-1") -> dict:
        body = {"documentId": document_id, "requestId": uuid.uuid4().hex}
        response = await api.post("/publish", json=body, headers=auth_headers)
        assert response.status_code == 202
        await make_api.settle()
        finished = await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)
        assert finished.status_code == 200
        return finished.json()

    return run


@pytest.fixture
def published(api, published_on):
    """Like `published_on`, on the default API."""
    return lambda document_id: published_on(api, document_id)
