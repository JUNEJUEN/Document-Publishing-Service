"""Shared fixtures: the API runs in-process over ASGITransport."""
import httpx
import pytest

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
async def api(settings):
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client
