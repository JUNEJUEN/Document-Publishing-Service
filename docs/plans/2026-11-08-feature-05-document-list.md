# Feature 5: Document List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Goal:**
- `GET /documents` needs a token. It returns Source's document list unchanged, or 502 for any Source failure, never 500.
- The web app shows a documents table that uses `authFetch`. This is the first real check of silent token renewal.

**Architecture:**
- **`api/app/clients.py` (new):** holds `SourceClient.list_documents()` and an `UpstreamError`. Every way a Source call can fail becomes an `UpstreamError`: transport errors, timeouts, non-2xx answers, and bodies that aren't a list of objects with a string `id`. Features 6 and 7 extend this module with the job calls and the temporary/permanent split.
- **`create_app`:** takes an optional `source_http` (`httpx.AsyncClient`). Tests pass one wired to the real Source stub app with `httpx.ASGITransport`, or to an `httpx.MockTransport` that fails on purpose. Production builds its own client from `SOURCE_API_URL` and `HTTP_TIMEOUT`, and closes it on shutdown.
- **Test harness (`conftest.py`):**
  - `source_events`: a shared event list;
  - `source_app`: the in-process Source stub, seeded with 5 documents;
  - `make_api(transport)`: builds the API against any Source transport;
  - `api`: the API against the real stub;
  - `auth_headers`: a valid token header.

  Spec section 9 requires shared test helpers to live in `conftest.py` as fixtures, with one event log shared by both stubs; these follow that rule.
- **Web app:** `src/api.ts` gets `fetchDocuments()`, and `DocumentsTable.vue` shows the list, with Refresh, loading, empty and error states.

**Tech stack:** FastAPI 0.115.6, httpx 0.28.1 (now an API runtime package), Vue 3 + TypeScript.

**Spec:** `docs/specs/2026-11-08-document-publishing-service.md`:
- D14;
- 7.2 (`GET /documents`);
- 7.3 (the stub's list);
- 8 ("Documents" row and waiting states);
- 9 (`test_documents.py`, `test_auth.py` `/documents` case, helpers);
- 10 (`SOURCE_API_URL`, `HTTP_TIMEOUT`);
- 12, feature 5. The table's Publish buttons come in feature 6.

**Deferred:**
- The table's per-row Publish buttons, and its reload when a job finishes, come in feature 6.

---

## File structure

| File | Change |
|---|---|
| `api/requirements.txt` | Add `httpx` (runtime now) |
| `docker/tests-requirements.txt` | Remove `httpx` (comes from the API requirements) |
| `api/app/config.py` | `source_api_url`, `http_timeout`; one positive-number helper |
| `api/app/clients.py` | New: `UpstreamError`, `SourceClient.list_documents()` |
| `api/app/main.py` | `source_http` parameter, lifespan, `GET /documents` |
| `api/tests/conftest.py` | Source stub wiring, `make_api`, `auth_headers` |
| `api/tests/test_config.py` | New settings |
| `api/tests/test_documents.py` | New |
| `api/tests/test_auth.py` | `/documents` needs a token |
| `docker-compose.yml` | `api`: `SOURCE_API_URL`; `TOKEN_TTL_SECONDS` override for testing renewal |
| `.env.example` | `TOKEN_TTL_SECONDS` |
| `web/src/api.ts` | New: `SourceDocument`, `ApiError`, `fetchDocuments()` |
| `web/src/components/DocumentsTable.vue` | New |
| `web/src/App.vue` | Show the table when logged in |
| `web/src/style.css` | Table styles |

Commands are written for Git Bash, run from the repository root.

---

### Task 1: Settings for the Source API

**Files:**
- Modify: `api/tests/test_config.py`, then `api/app/config.py`

- [ ] **Step 1: Write the failing tests.** In `api/tests/test_config.py`:
  - add `"SOURCE_API_URL", "HTTP_TIMEOUT"` to the names the `env` fixture deletes;
  - extend `test_defaults` with
    ```python
        assert settings.source_api_url == "http://source-stub:8001"
        assert settings.http_timeout == 10.0
    ```
  - extend `test_optional_settings_come_from_environment` with `env.setenv("SOURCE_API_URL", "http://src.test")` and `env.setenv("HTTP_TIMEOUT", "2.5")` before `from_env()`, and these asserts after it:
    ```python
        assert settings.source_api_url == "http://src.test"
        assert settings.http_timeout == 2.5
    ```
  - append:
    ```python
    @pytest.mark.parametrize("timeout", ["0", "-1", "abc", "nan"])
    def test_http_timeout_must_be_positive(env, timeout):
        env.setenv("HTTP_TIMEOUT", timeout)
        with pytest.raises(ValueError, match="HTTP_TIMEOUT"):
            Settings.from_env()
    ```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_config.py`
Expected: failures, with `AttributeError: 'Settings' object has no attribute 'source_api_url'` and `DID NOT RAISE`.

- [ ] **Step 3: Implement.** In `api/app/config.py`, replace `_positive_int` with one helper for whole and decimal numbers:

```python
def _positive(env, name: str, default: float, cast=int):
    kind = "integer" if cast is int else "number"
    try:
        value = cast(env.get(name, default))
    except ValueError:
        value = 0
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive {kind}")
    return value
```

Add `import math` at the top of `config.py`.

Add two fields after `cors_origins`:

```python
    source_api_url: str = "http://source-stub:8001"
    http_timeout: float = 10.0
```

In `from_env`, change the TTL line to `ttl = _positive(env, "TOKEN_TTL_SECONDS", 300)`, and pass these two extra arguments to `cls(...)`:

```python
            source_api_url=env.get("SOURCE_API_URL", "http://source-stub:8001"),
            http_timeout=_positive(env, "HTTP_TIMEOUT", 10.0, cast=float),
```

- [ ] **Step 4: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `42 passed` (38 + 4 new timeout cases).

Suggested commit: `feat(api): add source api url and http timeout settings`

---

### Task 2: Test harness against the Source stub

**Files:**
- Modify: `api/requirements.txt`, `docker/tests-requirements.txt`, `api/tests/conftest.py`

- [ ] **Step 1: Move `httpx` to the API packages.** `api/requirements.txt`:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
PyJWT==2.10.1
python-multipart==0.0.20
httpx==0.28.1
```

`docker/tests-requirements.txt`:

```
pytest==8.3.4
pytest-asyncio==0.25.0
```

Run: `docker compose build tests`

- [ ] **Step 2: Replace `api/tests/conftest.py`.** The `api` fixture keeps its name, so the existing tests are unchanged.

```python
"""Shared fixtures: the API and the Source stub run in-process over ASGITransport."""
import httpx
import pytest

import source_api
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
async def auth_headers(api, settings) -> dict:
    response = await api.post(
        "/oauth/token",
        data={"client_id": settings.client_id, "client_secret": settings.client_secret},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
```

- [ ] **Step 3: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: every test that uses `api` errors with `TypeError: create_app() got an unexpected keyword argument 'source_http'`.

- [ ] **Step 4: Accept the client in `create_app`.** In `api/app/main.py`:
  - add `import httpx` and `from contextlib import asynccontextmanager` at the top;
  - change the signature and the first lines:

```python
def create_app(settings: Settings | None = None, *, source_http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_http = source_http is None
    if source_http is None:
        source_http = httpx.AsyncClient(base_url=settings.source_api_url, timeout=settings.http_timeout)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if owns_http:
            await source_http.aclose()

    app = FastAPI(title="Document publishing service", lifespan=lifespan)
```

  The rest of `create_app` (state, CORS, routes) stays as it is.

- [ ] **Step 5: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `42 passed`.

Suggested commit: `test(api): wire the source stub into the test harness`

---

### Task 3: `GET /documents`

**Files:**
- Create: `api/tests/test_documents.py`
- Modify: `api/tests/test_auth.py`
- Create: `api/app/clients.py`
- Modify: `api/app/main.py`

- [ ] **Step 1: Write the failing tests.** Create `api/tests/test_documents.py`:

```python
import httpx
import pytest

import source_api
from app.auth import issue_token


async def test_needs_a_token_and_does_not_call_source(api, source_events):
    response = await api.get("/documents")
    assert response.status_code == 401
    assert source_events == []


async def test_returns_source_documents_unchanged(api, auth_headers, source_app, source_events):
    source_app.state.documents["doc-2"].content = None  # deleted in Source
    response = await api.get("/documents", headers=auth_headers)
    assert response.status_code == 200
    expected = [source_api.metadata(doc) for doc in source_app.state.documents.values()]
    assert response.json() == expected
    assert [doc["id"] for doc in response.json()] == ["doc-1", "doc-2", "doc-3", "doc-4", "doc-5"]
    assert response.json()[1]["deleted"] is True
    assert source_events.count(("source", "list", None)) == 1


def failing(kind: str):
    """A Source that fails in one way, counting the calls it receives."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if kind == "unreachable":
            raise httpx.ConnectError("connection refused", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("too slow", request=request)
        bodies = {
            "404": httpx.Response(404, json={"detail": "nope"}),
            "503": httpx.Response(503, text="down"),
            "not-json": httpx.Response(200, text="<html>"),
            "object": httpx.Response(200, json={"id": "doc-1"}),
            "item-not-object": httpx.Response(200, json=["doc-1"]),
            "item-without-id": httpx.Response(200, json=[{"filename": "a.txt"}]),
            "id-not-string": httpx.Response(200, json=[{"id": 1}]),
        }
        return bodies[kind]

    return httpx.MockTransport(handler), calls


KINDS = ["unreachable", "timeout", "404", "503", "not-json", "object", "item-not-object", "item-without-id", "id-not-string"]


@pytest.mark.parametrize("kind", KINDS)
async def test_source_failure_gives_502_after_one_attempt(make_api, settings, kind):
    transport, calls = failing(kind)
    api = await make_api(transport)
    headers = {"Authorization": f"Bearer {issue_token(settings)}"}
    response = await api.get("/documents", headers=headers)
    assert response.status_code == 502
    assert response.json()["detail"].startswith("Source API unavailable: ")
    assert calls == ["/documents"]


async def test_502_names_the_reason(make_api, settings):
    api = await make_api(failing("503")[0])
    response = await api.get("/documents", headers={"Authorization": f"Bearer {issue_token(settings)}"})
    assert response.json() == {"detail": "Source API unavailable: HTTP 503"}
```

In `api/tests/test_auth.py`, append:

```python
@pytest.mark.parametrize("method, path", [("GET", "/documents")])
async def test_protected_endpoints_need_a_token(api, method, path):
    response = await api.request(method, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
```

Features 6 and 10 add their `(method, path)` pairs to this list.

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: the 13 new tests fail. The endpoint doesn't exist, so they get 404 instead of 401, 200 or 502.

- [ ] **Step 3: Write `api/app/clients.py`**

```python
"""HTTP clients for the external systems."""
import httpx


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how."""


def _is_document_list(data) -> bool:
    return isinstance(data, list) and all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in data)


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[dict]:
        try:
            response = await self._http.get("/documents")
        except httpx.HTTPError as exc:
            raise UpstreamError(type(exc).__name__) from exc
        if not response.is_success:
            raise UpstreamError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise UpstreamError("answer is not JSON") from None
        if not _is_document_list(data):
            raise UpstreamError("answer is not a document list")
        return data
```

- [ ] **Step 4: Add the endpoint to `api/app/main.py`**

Imports:
- extend the FastAPI import to `from fastapi import Depends, FastAPI, HTTPException, Request`;
- add `from .clients import SourceClient, UpstreamError`;
- extend the auth import with `require_token`.

After the `source_http` setup, add `source = SourceClient(source_http)`. Then add this route after the token endpoint:

```python
    @app.get("/documents")
    async def list_documents(_client: str = Depends(require_token)) -> list[dict]:
        try:
            return await source.list_documents()
        except UpstreamError as exc:
            raise HTTPException(502, f"Source API unavailable: {exc}") from None
```

- [ ] **Step 5: Run and confirm pass**

Run: `docker compose run --rm -T tests`
Expected: `55 passed` (42 + 12 in `test_documents.py` + 1 in `test_auth.py`).

Suggested commit: `feat(api): list source documents behind a token`

---

### Task 4: Compose settings

**Files:**
- Modify: `docker-compose.yml` (`api`), `.env.example`

- [ ] **Step 1: In `docker-compose.yml`, add two lines to the `api` service's `environment`:**

```yaml
      SOURCE_API_URL: http://source-stub:8001
      TOKEN_TTL_SECONDS: ${TOKEN_TTL_SECONDS:-300}
```

- [ ] **Step 2: In `.env.example`, add after `TOKEN_SECRET`:**

```bash
# Login token lifetime in seconds. A short value (e.g. 20) helps test silent token renewal.
TOKEN_TTL_SECONDS=300
```

- [ ] **Step 3: Update spec section 10.**
  - **Listed overrides:** in the `.env.example` paragraph, add `TOKEN_TTL_SECONDS` to the values worth overriding.
  - **`HTTP_TIMEOUT` row:** change its meaning to "Seconds allowed for each step of an upstream call (connect, read, write)". httpx applies the timeout per step, not to the whole call.

- [ ] **Step 4: Check the running API**

```bash
docker compose up -d --wait --build api
TOKEN=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/documents
curl -s localhost:8000/documents -H "Authorization: Bearer $TOKEN" | grep -o '"id":"doc-[0-9]*"' | head -3
docker compose stop source-stub
curl -s -w ' %{http_code}\n' localhost:8000/documents -H "Authorization: Bearer $TOKEN"
docker compose start source-stub
```
Expected:
- `401`;
- `"id":"doc-1"`, `"id":"doc-2"`, `"id":"doc-3"`;
- `{"detail":"Source API unavailable: ConnectError"} 502`. The name can differ: `ReadError` or `RemoteProtocolError` if a pooled connection is reused, `ConnectTimeout` if name lookup hangs.

Suggested commit: `build: point api at the source stub`

---

### Task 5: Documents table

**Files:**
- Create: `web/src/api.ts`, `web/src/components/DocumentsTable.vue`
- Modify: `web/src/App.vue`, `web/src/style.css`

The frontend has no unit tests (spec section 9). It is checked by `vue-tsc`, the build, and the browser.

- [ ] **Step 1: `web/src/api.ts`**

```ts
import { authFetch } from './session'

export interface SourceDocument {
  id: string
  filename: string
  contentType: string
  size: number | null
  deleted: boolean
  status: string
  statusMessage: string | null
  archiveUri: string | null
  archiveSha256: string | null
}

export class ApiError extends Error {}

async function errorText(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null)
  const detail = (body as { detail?: unknown } | null)?.detail
  return typeof detail === 'string' ? detail : `HTTP ${res.status}`
}

export async function fetchDocuments(): Promise<SourceDocument[]> {
  const res = await authFetch('/documents')
  // A 401 here means renewal failed temporarily; the session is still active.
  if (res.status === 401) throw new ApiError('Could not refresh your login. Please try again.')
  if (!res.ok) throw new ApiError(await errorText(res))
  return (await res.json()) as SourceDocument[]
}
```

- [ ] **Step 2: `web/src/components/DocumentsTable.vue`**

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchDocuments, type SourceDocument } from '../api'

const documents = ref<SourceDocument[]>([])
const loaded = ref(false)
const loading = ref(false)
const error = ref('')

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    documents.value = await fetchDocuments()
  } catch (e) {
    // Keep the last list; only show what went wrong.
    if (e instanceof TypeError) error.value = 'Cannot reach the API. Please try again.'
    else error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
    loaded.value = true
  }
}

onMounted(refresh)
</script>

<template>
  <section class="card">
    <div class="card-head">
      <h2>Documents in Source</h2>
      <button class="secondary" :disabled="loading" @click="refresh">{{ loading ? 'Refreshing…' : 'Refresh' }}</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="!loaded" class="muted">Loading documents…</p>
    <p v-else-if="documents.length === 0 && !error" class="muted">No documents in Source</p>
    <table v-else-if="documents.length">
      <thead>
        <tr><th>Id</th><th>Filename</th><th>Size</th><th>Source status</th></tr>
      </thead>
      <tbody>
        <tr v-for="doc in documents" :key="doc.id">
          <td>{{ doc.id }}</td>
          <td>{{ doc.filename }}</td>
          <td>{{ doc.size === null ? '—' : `${doc.size} B` }}</td>
          <td>{{ doc.status }}<span v-if="doc.deleted" class="muted"> · deleted</span></td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
```

- [ ] **Step 3: Show it in `web/src/App.vue`.** Add `import DocumentsTable from './components/DocumentsTable.vue'`, and replace the placeholder section with:

```vue
    <DocumentsTable v-else />
```

The table is created on login and destroyed on logout, so no request outlives the session.

- [ ] **Step 4: Table styles.** Append to `web/src/style.css`:

```css
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; }
.card-head h2 { margin: 0; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }
td { overflow-wrap: anywhere; }
```

Long values wrap, so the table fits at phone width with every column shown (spec 8).

- [ ] **Step 5: Type-check and build**

Run: `(cd web && npm run build)`
Expected: no `vue-tsc` errors, and the build succeeds.

Suggested commit: `feat(web): show source documents after login`

---

### Task 6: Run it and check renewal

- [ ] **Step 1: Start with a 20-second token**

```bash
docker compose down -v   # fresh documents for the checks below
TOKEN_TTL_SECONDS=20 docker compose up -d --wait --build
docker compose ps --format '{{.Service}}: {{.Status}}'
```

- [ ] **Step 2: Browser checks at http://localhost:8080 (user)**
  1. **Table loads:** log in with `demo-client` / `demo-secret`. The table shows `doc-1` … `doc-20`, size 46 or 47 B, status `Released`.
  2. **Deleted rows:** in Git Bash, `curl -s -X DELETE localhost:8001/documents/doc-3`, then click **Refresh**. `doc-3` shows `Released · deleted` and size `—`. It stays deleted until the next `docker compose down -v`.
  3. **Silent renewal:** wait more than 20 s, then click **Refresh**. The table reloads without a logout. In the browser's network tab, `/documents` got a 401, then `/oauth/token` was called, then `/documents` succeeded.
  4. **Source down:** run `docker compose stop source-stub` and click **Refresh**. The page shows "Source API unavailable: …" and keeps the last list. Then run `docker compose start source-stub`.
  5. **Log out:** returns to the login form.

- [ ] **Step 3: Back to normal**

Run: `docker compose up -d --wait` (without the override, so the token lifetime is 300 s again).

---

### Task 7: Reviews

- [ ] **Step 1: Full test run.** `docker compose run --rm -T tests` must show `55 passed`.

- [ ] **Step 2: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with only the files in "File structure" and the spec sections above. Fix what it finds, re-run the tests, and review again until it comes back clean.

- [ ] **Step 3: User review.** Hand the result to the user with the test output and the Task 4 output, ask them to do the browser checks in Task 6, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address document list review findings`
