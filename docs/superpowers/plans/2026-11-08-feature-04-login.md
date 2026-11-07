# Feature 4: Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Goal:**
- `POST /oauth/token` issues a short-lived JWT for the one configured client.
- A reusable `require_token` dependency rejects every bad token with 401, never 500.
- CORS lets the web app call the API.
- The web app has a login page. It keeps the token, and the credentials for silent renewal, in memory only.

**Architecture:**
- **`config`:** gains the required auth settings. It refuses to start without them, and refuses a token lifetime of 0 or less.
- **`auth`:** a new module for issuing tokens, checking credentials, and the `require_token` dependency, which reads the `Authorization` header itself. Every token problem gets the same 401 text, `invalid or expired token`.
- **`main`:** adds the CORS middleware and the token endpoint. The endpoint reads the form inside a `try`, so any unreadable body counts as wrong credentials.
- **Web app:** `session.ts` holds the reactive session state and provides `login`, `logout` and `authFetch`.
  - `authFetch` adds the bearer header. On a 401 it renews the token once and retries.
  - Concurrent 401s share one renewal.
  - A renewal that finishes after a logout is ignored.
  - `authFetch` gets its first caller in feature 5.

**Tech stack:** FastAPI 0.115.6, PyJWT 2.10.1, python-multipart 0.0.20, Vue 3 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`, sections 6 (login), 7.2 (`POST /oauth/token`, CORS), 8 (login screen part), 10 (`CLIENT_ID`, `CLIENT_SECRET`, `TOKEN_SECRET`, `TOKEN_TTL_SECONDS`, `CORS_ORIGINS`) and section 12, feature 4.

**Deferred tests (spec section 12):**
- `test_auth.py` here covers the token endpoint and the token check.
- There is no protected endpoint yet, so the check is tested on a route that exists only in the test, built with the real `require_token`.
- Feature 5 adds the 401 cases for `/documents`, plus an `auth_headers` fixture in `conftest.py` for all later tests. Feature 6 adds the 401 cases for `/publish` (including the brief's required rejected-call test), `/jobs` and `/jobs/{id}`. Feature 10 adds them for `/jobs/{id}/retry`.

**Review unit:** the CLAUDE.md flow applies per task: tests first, see them fail, implement, see them pass. The AI code review and the user's sign-off happen once for the whole feature, in Task 7.

---

## File structure

| File | Change |
|---|---|
| `api/requirements.txt` | Add `PyJWT`, `python-multipart` |
| `api/app/config.py` | Required auth settings, TTL, CORS origins |
| `api/app/auth.py` | New: tokens, credential check (Task 2), `require_token` (Task 3) |
| `api/app/main.py` | `POST /oauth/token`, CORS middleware |
| `api/tests/conftest.py` | `settings` fixture gets test credentials |
| `api/tests/test_config.py` | Required settings, defaults, edge cases |
| `api/tests/test_auth.py` | New: token endpoint, token check, CORS |
| `docker-compose.yml` | `api` environment; `web` build argument |
| `docker/web.Dockerfile` | `VITE_API_URL` build argument |
| `web/src/env.d.ts` | New: typing for `VITE_API_URL` |
| `web/src/config.ts` | New: API address |
| `web/src/session.ts` | New: session state, `login`, `logout`, `authFetch` |
| `web/src/components/LoginForm.vue` | New: login form |
| `web/src/App.vue` | Login or main screen |
| `web/src/style.css`, `web/src/main.ts` | New basic styles, imported in `main.ts` |

Commands are written for Git Bash, run from the repository root.

---

### Task 1: Settings for auth and CORS

**Files:**
- Modify: `api/tests/conftest.py`, `api/tests/test_config.py`, then `api/app/config.py`

- [ ] **Step 1: Give the `settings` fixture test credentials.** In `api/tests/conftest.py`, replace the `settings` fixture:

```python
@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        client_id="test-client",
        client_secret="test-secret",
        token_secret="test-token-secret-0123456789abcdef",
        data_dir=tmp_path,
    )
```

- [ ] **Step 2: Write the failing settings tests.** Replace `api/tests/test_config.py`:

```python
from pathlib import Path

import pytest

from app.config import Settings

REQUIRED = {
    "CLIENT_ID": "demo-client",
    "CLIENT_SECRET": "demo-secret",
    "TOKEN_SECRET": "demo-token-secret-0123456789abcdef",
}


@pytest.fixture
def env(monkeypatch):
    for name in ("DATA_DIR", "TOKEN_TTL_SECONDS", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_required_settings_come_from_environment(env):
    settings = Settings.from_env()
    assert (settings.client_id, settings.client_secret, settings.token_secret) == tuple(REQUIRED.values())


def test_defaults(env):
    settings = Settings.from_env()
    assert settings.data_dir == Path("/data")
    assert settings.token_ttl_seconds == 300
    assert settings.cors_origins == ("http://localhost:8080",)


def test_optional_settings_come_from_environment(env, tmp_path):
    env.setenv("DATA_DIR", str(tmp_path))
    env.setenv("TOKEN_TTL_SECONDS", "60")
    env.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    settings = Settings.from_env()
    assert settings.data_dir == tmp_path
    assert settings.token_ttl_seconds == 60
    assert settings.cors_origins == ("http://a.test", "http://b.test")


def test_empty_cors_origins_fall_back_to_default(env):
    env.setenv("CORS_ORIGINS", " , ")
    assert Settings.from_env().cors_origins == ("http://localhost:8080",)


@pytest.mark.parametrize("ttl", ["0", "-5"])
def test_token_lifetime_must_be_positive(env, ttl):
    env.setenv("TOKEN_TTL_SECONDS", ttl)
    with pytest.raises(ValueError, match="TOKEN_TTL_SECONDS"):
        Settings.from_env()


def test_missing_required_settings_stop_startup(env):
    for name in REQUIRED:
        env.delenv(name)
    with pytest.raises(ValueError, match="CLIENT_ID, CLIENT_SECRET, TOKEN_SECRET"):
        Settings.from_env()
```

- [ ] **Step 3: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: errors such as `TypeError: Settings.__init__() got an unexpected keyword argument 'client_id'`.

- [ ] **Step 4: Implement.** Replace `api/app/config.py`:

```python
"""Settings read from environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("CLIENT_ID", "CLIENT_SECRET", "TOKEN_SECRET")
DEFAULT_ORIGINS = ("http://localhost:8080",)


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    token_secret: str
    token_ttl_seconds: int = 300
    data_dir: Path = Path("/data")
    cors_origins: tuple[str, ...] = DEFAULT_ORIGINS

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        missing = [name for name in REQUIRED if not env.get(name)]
        if missing:
            raise ValueError(f"missing required settings: {', '.join(missing)}")
        ttl = int(env.get("TOKEN_TTL_SECONDS", "300"))
        if ttl <= 0:
            raise ValueError("TOKEN_TTL_SECONDS must be positive")
        origins = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            client_id=env["CLIENT_ID"],
            client_secret=env["CLIENT_SECRET"],
            token_secret=env["TOKEN_SECRET"],
            token_ttl_seconds=ttl,
            data_dir=Path(env.get("DATA_DIR", "/data")),
            cors_origins=origins or DEFAULT_ORIGINS,
        )
```

- [ ] **Step 5: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `8 passed` (7 config + 1 health).

- [ ] **Step 6: Give the `api` container its settings now,** so the stack keeps starting after this task. Do Task 5 Step 1 here, adding the `environment` block to `api`.

Suggested commit: `feat(api): require auth settings`

---

### Task 2: Token endpoint

**Files:**
- Modify: `api/requirements.txt`
- Create: `api/tests/test_auth.py` (first part), then `api/app/auth.py`
- Modify: `api/app/main.py`

- [ ] **Step 1: Add the packages to `api/requirements.txt`, then rebuild the tests image**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
PyJWT==2.10.1
python-multipart==0.0.20
```

Run: `docker compose build tests`

- [ ] **Step 2: Write the failing token endpoint tests.** Create `api/tests/test_auth.py`:

```python
import time

import jwt
import pytest

from app.auth import AUDIENCE

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
    ids=["wrong-secret", "wrong-id", "missing-secret", "empty-form", "json-body", "non-ascii", "garbage-body", "file-fields"],
)
async def test_bad_credentials_get_401(api, request_kwargs):
    response = await api.post("/oauth/token", **request_kwargs)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "invalid client credentials"}
```

- [ ] **Step 3: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_auth.py`
Expected: `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 4: Write `api/app/auth.py` (no `require_token` yet)**

```python
"""Client-credentials tokens. Any problem with a token is a 401, never a 500."""
import hmac
import time

import jwt
from fastapi import HTTPException

from .config import Settings

ALGORITHM = "HS256"
AUDIENCE = "publishing-api"


def credentials_valid(settings: Settings, client_id: str, client_secret: str) -> bool:
    # Bytes, so any characters compare in constant time without TypeError.
    id_ok = hmac.compare_digest(client_id.encode(), settings.client_id.encode())
    secret_ok = hmac.compare_digest(client_secret.encode(), settings.client_secret.encode())
    return id_ok and secret_ok


def issue_token(settings: Settings, now: float | None = None) -> str:
    issued = int(time.time() if now is None else now)
    claims = {"sub": settings.client_id, "aud": AUDIENCE, "iat": issued, "exp": issued + settings.token_ttl_seconds}
    return jwt.encode(claims, settings.token_secret, algorithm=ALGORITHM)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})
```

- [ ] **Step 5: Add the token endpoint.** Replace `api/app/main.py`:

```python
from fastapi import FastAPI, Request

from .auth import credentials_valid, issue_token, unauthorized
from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Document publishing service")
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/oauth/token")
    async def token(request: Request) -> dict:
        try:
            form = await request.form()
            client_id, client_secret = form.get("client_id"), form.get("client_secret")
        except Exception:  # an unreadable body counts as wrong credentials
            client_id = client_secret = None
        if not (
            isinstance(client_id, str)
            and isinstance(client_secret, str)
            and credentials_valid(settings, client_id, client_secret)
        ):
            raise unauthorized("invalid client credentials")
        return {
            "access_token": issue_token(settings),
            "token_type": "Bearer",
            "expires_in": settings.token_ttl_seconds,
        }

    return app
```

- [ ] **Step 6: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `17 passed` (8 + 9 auth).

Suggested commit: `feat(api): add oauth token endpoint`

---

### Task 3: Token check

**Files:**
- Modify: `api/tests/test_auth.py`, then `api/app/auth.py`

- [ ] **Step 1: Write the failing tests.** Replace the import block at the top of `api/tests/test_auth.py` with:

```python
import base64
import json
import time

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI

from app.auth import AUDIENCE, issue_token, require_token
```

Then append to the end of the file:

```python
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
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_auth.py`
Expected: `ImportError: cannot import name 'require_token' from 'app.auth'`.

- [ ] **Step 3: Implement `require_token`.** In `api/app/auth.py`, change the FastAPI import to `from fastapi import HTTPException, Request`. Then append:

```python
def require_token(request: Request) -> str:
    """Dependency: the client id from a valid bearer token, else 401."""
    settings: Settings = request.app.state.settings
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized("invalid or expired token")
    try:
        claims = jwt.decode(
            token.strip(),
            settings.token_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "aud", "iat", "exp"]},
        )
    except Exception:
        raise unauthorized("invalid or expired token") from None
    return claims["sub"]
```

- [ ] **Step 4: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `34 passed`: the 17 from before, plus 17 new (2 valid-token tests, 8 header cases, 1 unsigned, 1 expired, 5 forged).

Suggested commit: `feat(api): add bearer token check`

---

### Task 4: CORS

**Files:**
- Modify: `api/tests/test_auth.py` (append), then `api/app/main.py`

- [ ] **Step 1: Write the failing CORS tests.** Append to `api/tests/test_auth.py`:

```python
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
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_auth.py -k "cors or readable"`
Expected: all three fail. The preflights get 405 because no middleware handles `OPTIONS`, and the 401 has no CORS header.

- [ ] **Step 3: Add the middleware.** In `api/app/main.py`, add the import:

```python
from fastapi.middleware.cors import CORSMiddleware
```

and directly after `app.state.settings = settings`:

```python
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
```

- [ ] **Step 4: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `37 passed`.

Suggested commit: `feat(api): allow the web app via cors`

---

### Task 5: Run the API with its settings

**Files:**
- Modify: `docker-compose.yml` (`api` service)

- [ ] **Step 1: Add an `environment` block to `api`,** after `build`. This was already done in Task 1 Step 6.

```yaml
    environment:
      CLIENT_ID: ${CLIENT_ID:-demo-client}
      CLIENT_SECRET: ${CLIENT_SECRET:-demo-secret}
      TOKEN_SECRET: ${TOKEN_SECRET:-local-demo-token-secret-change-me-0123456789}
      CORS_ORIGINS: http://localhost:8080
```

- [ ] **Step 2: Check the running API**

```bash
docker compose up -d --wait --build api
curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret; echo
curl -s -w ' %{http_code}\n' -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=nope
curl -s -o /dev/null -D - -X OPTIONS localhost:8000/oauth/token -H 'Origin: http://localhost:8080' -H 'Access-Control-Request-Method: POST' | grep -i access-control-allow-origin
docker compose run --rm -T --no-deps -e CLIENT_ID= api 2>&1 | grep -o 'missing required settings: CLIENT_ID'
docker compose down
```
Expected:
- a JSON token answer with `"token_type":"Bearer","expires_in":300`;
- `{"detail":"invalid client credentials"} 401`;
- `access-control-allow-origin: http://localhost:8080`;
- `missing required settings: CLIENT_ID`: the API refuses to start without it.

Suggested commit: `build: configure api client credentials and cors`

---

### Task 6: Login page

**Files:**
- Create: `web/src/env.d.ts`, `web/src/config.ts`, `web/src/session.ts`, `web/src/components/LoginForm.vue`, `web/src/style.css`
- Modify: `web/src/App.vue`, `web/src/main.ts`, `docker/web.Dockerfile`, `docker-compose.yml` (`web` service)

The frontend has no unit tests (spec section 9). It is checked by `vue-tsc`, the build, and the browser.

- [ ] **Step 1: `web/src/env.d.ts` and `web/src/config.ts`**

```ts
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
}
```

```ts
// `||`, not `??`: an empty build argument also falls back.
export const API_URL: string = import.meta.env.VITE_API_URL || 'http://localhost:8000'
```

- [ ] **Step 2: `web/src/session.ts`**

```ts
import { reactive } from 'vue'
import { API_URL } from './config'

interface Credentials {
  clientId: string
  clientSecret: string
}

export const EXPIRED = 'Your session has expired. Please log in again.'

// Memory only: a page reload logs the user out.
export const session = reactive({ token: '', clientId: '', notice: '' })
let credentials: Credentials | null = null
// One renewal shared by all requests that got a 401 at the same time.
let renewal: { creds: Credentials; token: Promise<string> } | null = null

export class WrongCredentials extends Error {}

async function requestToken(c: Credentials): Promise<string> {
  const body = new URLSearchParams({ client_id: c.clientId, client_secret: c.clientSecret })
  const res = await fetch(`${API_URL}/oauth/token`, { method: 'POST', body })
  if (res.status === 401) throw new WrongCredentials('Wrong client id or secret')
  if (!res.ok) throw new Error(`Login failed (HTTP ${res.status})`)
  return ((await res.json()) as { access_token: string }).access_token
}

export async function login(clientId: string, clientSecret: string): Promise<void> {
  const c = { clientId, clientSecret }
  session.token = await requestToken(c)
  credentials = c
  session.clientId = clientId
  session.notice = ''
}

export function logout(notice = ''): void {
  session.token = ''
  session.clientId = ''
  session.notice = notice
  credentials = null
  renewal = null
}

async function renewToken(): Promise<boolean> {
  const c = credentials
  if (c === null) return false
  if (renewal === null || renewal.creds !== c) renewal = { creds: c, token: requestToken(c) }
  const current = renewal
  try {
    const token = await current.token
    if (credentials !== c) return false // logged out meanwhile
    session.token = token
    return true
  } catch {
    if (credentials === c) logout(EXPIRED)
    return false
  } finally {
    if (renewal === current) renewal = null
  }
}

/** Calls the API with the token; on a 401 renews the token once and retries. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string) => {
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${token}`)
    return fetch(`${API_URL}${path}`, { ...init, headers })
  }
  const sent = session.token
  const first = await send(sent)
  if (first.status !== 401) return first
  // Another request may already have renewed the token.
  if (session.token === sent && !(await renewToken())) return first
  if (!session.token) return first
  const retried = session.token
  const second = await send(retried)
  // Only end this session; a new login may have happened meanwhile.
  if (second.status === 401 && session.token === retried) logout(EXPIRED)
  return second
}
```

- [ ] **Step 3: `web/src/components/LoginForm.vue`**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { login, WrongCredentials } from '../session'

const clientId = ref('')
const clientSecret = ref('')
const busy = ref(false)
const error = ref('')

function describe(e: unknown): string {
  if (e instanceof WrongCredentials) return e.message
  // fetch() throws a TypeError when the network request itself fails.
  if (e instanceof TypeError) return 'Cannot reach the API. Please try again.'
  return e instanceof Error ? e.message : String(e)
}

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await login(clientId.value, clientSecret.value)
  } catch (e) {
    error.value = describe(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="card" @submit.prevent="submit">
    <h2>Log in</h2>
    <label>Client id <input v-model="clientId" autocomplete="username" required /></label>
    <label>Client secret <input v-model="clientSecret" type="password" autocomplete="current-password" required /></label>
    <button :disabled="busy">{{ busy ? 'Logging in…' : 'Log in' }}</button>
    <p v-if="error" class="error">{{ error }}</p>
  </form>
</template>
```

- [ ] **Step 4: `web/src/App.vue`**

```vue
<script setup lang="ts">
import LoginForm from './components/LoginForm.vue'
import { logout, session } from './session'
</script>

<template>
  <main>
    <header>
      <h1>Document publishing</h1>
      <div v-if="session.token" class="who">
        <span>Logged in as {{ session.clientId }}</span>
        <button class="secondary" @click="logout()">Log out</button>
      </div>
    </header>
    <p v-if="session.notice" class="notice">{{ session.notice }}</p>
    <LoginForm v-if="!session.token" />
    <section v-else class="card">
      <p class="muted">Documents and jobs will appear here.</p>
    </section>
  </main>
</template>
```

- [ ] **Step 5: `web/src/style.css`, imported in `web/src/main.ts`**

```css
:root {
  --bg: #f6f7f9;
  --card: #ffffff;
  --text: #1d2330;
  --muted: #667085;
  --border: #e3e6eb;
  --accent: #2f5bea;
  --bad: #b42318;
  --wait-bg: #fff4dc;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color: var(--text);
}
body { margin: 0; background: var(--bg); }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px; }
header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
h1 { font-size: 1.5rem; }
h2 { font-size: 1.1rem; margin: 0 0 12px; }
.who { display: flex; align-items: center; gap: 12px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 0.9rem; margin-bottom: 12px; }
input { padding: 8px; border: 1px solid var(--border); border-radius: 6px; font: inherit; max-width: 320px; }
button { padding: 8px 16px; border: 0; border-radius: 6px; background: var(--accent); color: #fff; font: inherit; cursor: pointer; }
button:disabled { opacity: 0.6; cursor: default; }
button.secondary { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
.error { color: var(--bad); }
.muted { color: var(--muted); }
.notice { background: var(--wait-bg); padding: 8px 12px; border-radius: 6px; }
```

In `web/src/main.ts`, add `import './style.css'` after the `App` import.

- [ ] **Step 6: Bake the API address into the build.** In `docker/web.Dockerfile`, add these two lines directly before `RUN npm run build`:

```dockerfile
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
```

In `docker-compose.yml`, add an `args` entry under the `web` service's `build`:

```yaml
      args:
        VITE_API_URL: http://localhost:8000
```

- [ ] **Step 7: Type-check and build**

Run: `(cd web && npm run build)`
Expected: `vue-tsc` reports no errors, and the Vite build succeeds.

- [ ] **Step 8: Run everything and check in the browser**

```bash
docker compose up -d --wait --build
docker compose ps --format '{{.Service}}: {{.Status}}'
```
Expected: all four services `Up`, with the three Python ones `(healthy)`.

Then, in a browser at http://localhost:8080:
1. **Login form:** it shows client id, client secret and a Log in button.
2. **Wrong login:** `demo-client` / `wrong` shows "Wrong client id or secret".
3. **Right login:** `demo-client` / `demo-secret` shows "Logged in as demo-client", a Log out button, and the placeholder card.
4. **Log out:** returns to the login form.
5. **Reload:** after logging in again, reloading the page returns to the login form, because nothing is stored.
6. **API down:** `docker compose stop api`, then try to log in. The form shows "Cannot reach the API. Please try again." Restart with `docker compose start api`.

Silent renewal in `authFetch` can't be seen yet. It gets its first caller, and its check, in feature 5.

Suggested commit: `feat(web): add login page and session handling`

---

### Task 7: Reviews

- [ ] **Step 1: Full test run.** `docker compose run --rm -T tests` must show `37 passed`.

- [ ] **Step 2: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with only the files in "File structure" and spec sections 6, 7.2, 8 and 10. Fix what it finds, re-run the tests, and review again until it comes back clean.

- [ ] **Step 3: User review.** Hand the result to the user with the test output and the Task 5 output. Ask them to do the browser checks from Task 6 Step 8, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address login review findings`
