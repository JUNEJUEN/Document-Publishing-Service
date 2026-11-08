"""Shared fixtures: the API and the Source stub run in-process over ASGITransport."""
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

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


class ApiFactory:
    """Builds started APIs for one test, and shuts them down after it."""

    def __init__(self, settings: Settings, stack: AsyncExitStack):
        self._settings = settings
        self._stack = stack
        self._apps: list[FastAPI] = []

    async def __call__(self, source_transport: httpx.AsyncBaseTransport, **changes) -> httpx.AsyncClient:
        enter = self._stack.enter_async_context
        source_http = await enter(httpx.AsyncClient(transport=source_transport, base_url="http://source"))
        app = create_app(replace(self._settings, **changes), source_http=source_http)
        await enter(app.router.lifespan_context(app))
        self._apps.append(app)
        return await enter(httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api"))

    async def settle(self) -> None:
        """Waits until every job task of every built API has finished."""
        for app in self._apps:
            await app.state.runner.wait_idle()


@pytest.fixture
async def make_api(settings):
    async with AsyncExitStack() as stack:
        yield ApiFactory(settings, stack)


@pytest.fixture
async def api(make_api, source_app):
    return await make_api(httpx.ASGITransport(app=source_app))


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
def job(api, auth_headers):
    """The current Job object for a job id."""

    async def fetch(job_id: str) -> dict:
        response = await api.get(f"/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        return response.json()

    return fetch


@pytest.fixture
def published(publish, settle, job):
    """Publishes a document and returns its finished Job object."""

    async def run(document_id: str) -> dict:
        response = await publish(document_id)
        assert response.status_code == 202
        await settle()
        return await job(response.json()["id"])

    return run
