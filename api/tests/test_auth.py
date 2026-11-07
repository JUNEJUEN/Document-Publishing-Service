import base64
import json
import time

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI

from app.auth import AUDIENCE, issue_token, require_token

GOOD = {"client_id": "test-client", "client_secret": "test-secret"}


async def test_valid_credentials_get_a_token(api, settings):
    response = await api.post("/oauth/token", data=GOOD)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    claims = jwt.decode(body["access_token"], settings.token_secret, algorithms=["HS256"], audience=AUDIENCE)
    assert claims["sub"] == "test-client"
    assert claims["exp"] - claims["iat"] == 300
    assert abs(claims["iat"] - time.time()) < 5
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"data": {"client_id": "test-client", "client_secret": "wrong"}},
        {"data": {"client_id": "someone-else", "client_secret": "test-secret"}},
        {"data": {"client_id": "test-client"}},
        {"data": {}},
        {"json": GOOD},
        {"data": {"client_id": "tést-client", "client_secret": "sécret"}},
        {"content": b"\xff\xfe not a form", "headers": {"Content-Type": "application/x-www-form-urlencoded"}},
        {"files": {"client_id": ("a.txt", b"test-client"), "client_secret": ("b.txt", b"test-secret")}},
    ],
    ids=[
        "wrong-secret", "wrong-id", "missing-secret", "empty-form",
        "json-body", "non-ascii", "garbage-body", "file-fields",
    ],
)
async def test_bad_credentials_get_401(api, request_kwargs):
    response = await api.post("/oauth/token", **request_kwargs)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "invalid client credentials"}


@pytest.fixture
async def guarded(settings):
    """A route that exists only in tests, guarded by the real dependency."""
    app = FastAPI()
    app.state.settings = settings

    @app.get("/guarded")
    def guarded_route(client_id: str = Depends(require_token)) -> dict:
        return {"client": client_id}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://guarded") as client:
        yield client


def forge(settings, secret: str | None = None, **changes) -> str:
    """A token with some claims changed; a change to None removes the claim."""
    now = int(time.time())
    claims = {"sub": settings.client_id, "aud": AUDIENCE, "iat": now, "exp": now + 300} | changes
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secret or settings.token_secret, algorithm="HS256")


def b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def get(guarded, token: str):
    return guarded.get("/guarded", headers={"Authorization": f"Bearer {token}"})


async def test_valid_token_passes(guarded, settings):
    response = await get(guarded, issue_token(settings))
    assert response.status_code == 200
    assert response.json() == {"client": "test-client"}


async def test_token_from_the_endpoint_passes_with_any_scheme_case(api, guarded):
    token = (await api.post("/oauth/token", data=GOOD)).json()["access_token"]
    response = await guarded.get("/guarded", headers={"Authorization": f"bearer {token}"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer", "Bearer ", "Bearer not-a-jwt", "Bearer a.b.c", "Basic dGVzdDp0ZXN0", "Token abc"],
    ids=["no-header", "empty", "scheme-only", "scheme-space", "not-jwt", "garbage-jwt", "basic", "other-scheme"],
)
async def test_bad_authorization_header_gets_401(guarded, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = await guarded.get("/guarded", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "invalid or expired token"}


async def test_unsigned_token_gets_401(guarded, settings):
    now = int(time.time())
    claims = {"sub": settings.client_id, "aud": AUDIENCE, "iat": now, "exp": now + 300}
    # Every claim is valid, so only alg=none can cause the rejection.
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    assert (await get(guarded, unsigned)).status_code == 401


async def test_expired_token_gets_401(guarded, settings):
    assert (await get(guarded, issue_token(settings, now=time.time() - 3600))).status_code == 401


@pytest.mark.parametrize(
    "secret, changes",
    [
        ("another-secret-0123456789abcdefghij", {}),
        (None, {"aud": "someone-else"}),
        (None, {"sub": None}),
        (None, {"iat": None}),
        (None, {"exp": None}),
    ],
    ids=["other-secret", "wrong-audience", "no-sub", "no-iat", "no-exp"],
)
async def test_forged_or_incomplete_token_gets_401(guarded, settings, secret, changes):
    assert (await get(guarded, forge(settings, secret, **changes))).status_code == 401


WEB = "http://localhost:8080"
PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}


async def test_cors_allows_the_web_app(api):
    response = await api.options("/oauth/token", headers={"Origin": WEB, **PREFLIGHT})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == WEB
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


async def test_cors_refuses_other_origins(api):
    response = await api.options("/oauth/token", headers={"Origin": "http://evil.test", **PREFLIGHT})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_rejected_login_is_readable_by_the_web_app(api):
    response = await api.post("/oauth/token", data={"client_id": "x", "client_secret": "y"}, headers={"Origin": WEB})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == WEB


@pytest.mark.parametrize("method, path", [("GET", "/documents")])
async def test_protected_endpoints_need_a_token(api, source_events, method, path):
    response = await api.request(method, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert source_events == []  # rejected before any work
