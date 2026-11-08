# API Structure, Document Model and Ruff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Goal:** the owner's three requests:
1. **Ruff:** a configured linter (`ruff.toml`), run with `docker compose run --rm -T lint` and later in CI.
2. **`Document` model:** a Pydantic model for Source's document metadata, replacing the `list[dict]` pass-through and the hand-written shape check.
3. **Routers:** every endpoint moves into a FastAPI `APIRouter` module under `app/routers/`. Shared objects reach routes through dependencies in `app/dependencies.py`.

**Architecture:**
- `main.py` keeps only `create_app()`: it builds settings and the Source client, stores them on `app.state`, adds CORS and the lifespan, and includes the routers.
- `dependencies.py` hands `app.state` objects to routes: `get_settings`, `get_source`.
- `auth.require_token` takes its settings through `get_settings` instead of reading `app.state` directly.
- `SourceClient.list_documents()` returns `list[Document]`, validated with a `TypeAdapter`. Any validation failure becomes the existing 502 reason "answer is not a document list".
- The JSON field names stay camelCase (`contentType`, `statusMessage`…) through a camel-case alias generator, so the API response is unchanged.

**Tech stack:** FastAPI 0.115.6, Pydantic v2, ruff 0.8.4.

**Spec:** `docs/specs/2026-11-08-document-publishing-service.md`, section 3 (units: `routers/`, `models/`, `dependencies`, `main`), section 9 (lint command) and section 11 (`lint` service, `ruff.toml`, CI).

**Behaviour change:** one. Source answers whose items miss a field or have a wrong type now give 502, where before only a missing or non-string `id` did. The model validates in strict mode, so `"size": "46"` is rejected, not coerced, and fields the model doesn't define are dropped. Task 2 adds tests for it. Everything else is a pure refactor, so the existing tests must keep passing unchanged.

---

## File structure

| File | Change |
|---|---|
| `docker/tests-requirements.txt` | Add `ruff==0.8.4` |
| `ruff.toml` | New: line length, target, rules, first-party packages |
| `docker-compose.yml` | New `lint` service |
| `api/app/models/__init__.py`, `api/app/models/documents.py` | New: `Document` |
| `api/app/clients.py` | Return `list[Document]`; drop `_is_document_list` |
| `api/app/dependencies.py` | New: `get_settings`, `get_source` |
| `api/app/auth.py` | `require_token` takes settings via `get_settings` |
| `api/app/routers/__init__.py`, `health.py`, `auth.py`, `documents.py` | New: the endpoints |
| `api/app/main.py` | Only `create_app()` |
| `api/tests/test_documents.py` | One new failing-answer case |

Commands are written for Git Bash, run from the repository root.

---

### Task 1: Ruff as a project tool

**Files:**
- Modify: `docker/tests-requirements.txt`, `docker-compose.yml`
- Create: `ruff.toml`

- [ ] **Step 1: Pin ruff.** `docker/tests-requirements.txt`:

```
pytest==8.3.4
pytest-asyncio==0.25.0
ruff==0.8.4
```

- [ ] **Step 2: Write `ruff.toml`**

```toml
line-length = 120
target-version = "py312"
# Lets import sorting treat these as this project's own modules.
src = ["api", "stubs"]

[lint]
select = ["E", "F", "W", "B", "UP", "SIM", "I"]
```

- [ ] **Step 3: Add the `lint` service** to `docker-compose.yml`, directly after `tests`:

```yaml
  lint:
    image: publishing-tests
    build:
      context: .
      dockerfile: docker/tests.Dockerfile
    profiles: ["test"]
    entrypoint: ["ruff", "check"]
    working_dir: /workspace
    environment:
      RUFF_NO_CACHE: "true"  # no .ruff_cache in the project folder
    volumes:
      - .:/workspace
```

Also add `image: publishing-tests` to the `tests` service, above its `build`, so both services use one image.

- [ ] **Step 4: Run lint and the tests**

Run: `docker compose build tests && docker compose run --rm -T lint && docker compose run --rm -T tests`
Expected: `All checks passed!`, then `59 passed`. The code already meets every rule, including `I`, because `src` makes ruff treat `app` and the stub modules as this project's own.

- [ ] **Step 5: Remove the cache left by the earlier manual ruff runs**

Run: `rm -rf .ruff_cache`

Suggested commit: `build: add ruff lint service and config`

---

### Task 2: `Document` model

**Files:**
- Modify: `api/tests/test_documents.py`
- Create: `api/app/models/__init__.py`, `api/app/models/documents.py`
- Modify: `api/app/clients.py`

- [ ] **Step 1: Write the failing test cases.** In `api/tests/test_documents.py`, add one entry to `FAILING_ANSWERS`, after `"id-not-string"`:

```python
    "missing-field": lambda: httpx.Response(200, json=[{"id": "doc-1"}]),
```

and one case to the parametrize list of `test_502_names_the_reason_without_internals`:

```python
        ("missing-field", "answer is not a document list"),
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_documents.py`
Expected: both `missing-field` cases fail, because the old check accepts `[{"id": "doc-1"}]` and answers 200.

- [ ] **Step 3: Write the model.** `api/app/models/__init__.py` is empty. `api/app/models/documents.py`:

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Document(BaseModel):
    """A document's metadata as the Source API reports it."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True)

    id: str
    filename: str
    content_type: str
    size: int | None
    deleted: bool
    status: str
    status_message: str | None
    archive_uri: str | None
    archive_sha256: str | None
```

- [ ] **Step 4: Validate with it in `api/app/clients.py`.** Replace the file:

```python
"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document

_DOCUMENT_LIST = TypeAdapter(list[Document])


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how."""


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[Document]:
        try:
            response = await self._http.get("/documents")
        except httpx.HTTPError as exc:
            raise UpstreamError(type(exc).__name__) from exc
        if not response.is_success:
            raise UpstreamError(f"HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise UpstreamError("answer is not JSON") from exc
        try:
            return _DOCUMENT_LIST.validate_python(data)
        except ValidationError as exc:
            raise UpstreamError("answer is not a document list") from exc
```

In `api/app/main.py`, change the documents route's return type from `list[dict]` to `list[Document]`, and add `from .models.documents import Document`. The route moves to a router in Task 3.

- [ ] **Step 5: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `61 passed`, then `All checks passed!`.

`test_returns_source_documents_unchanged` also proves the camelCase JSON is unchanged: it compares the response with the stub's own metadata dicts.

- [ ] **Step 6: Update spec section 9.** In the `test_documents.py` row, change "a list item that isn't an object with a string `id`" to "a list item that isn't a valid `Document`".

Suggested commit: `feat(api): validate source documents with a pydantic model`

---

### Task 3: Routers and dependencies

**Files:**
- Create: `api/app/dependencies.py`, `api/app/routers/__init__.py`, `api/app/routers/health.py`, `api/app/routers/auth.py`, `api/app/routers/documents.py`
- Modify: `api/app/auth.py`, `api/app/main.py`

This is a pure refactor. No new tests are needed: the existing 61 tests cover every route, and the test-only `/guarded` route covers `require_token`. They must all pass unchanged.

- [ ] **Step 1: `api/app/dependencies.py`**

```python
"""Hands shared objects from app.state to routes."""
from fastapi import Request

from .clients import SourceClient
from .config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_source(request: Request) -> SourceClient:
    return request.app.state.source
```

- [ ] **Step 2: `require_token` takes its settings through the dependency.** In `api/app/auth.py`:
  - change the FastAPI import to `from fastapi import Depends, HTTPException, Request`;
  - add `from typing import Annotated` after `import time`, and `from .dependencies import get_settings` after the config import;
  - change the function head and delete its first body line (`settings: Settings = request.app.state.settings`):

```python
def require_token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> str:
    """Dependency: the client id from a valid bearer token, else 401."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
```

- [ ] **Step 3: The routers.** `api/app/routers/__init__.py` is empty.

`api/app/routers/health.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
```

`api/app/routers/auth.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..auth import credentials_valid, issue_token, unauthorized
from ..config import Settings
from ..dependencies import get_settings

router = APIRouter()


@router.post("/oauth/token")
async def token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
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
```

`api/app/routers/documents.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_token
from ..clients import SourceClient, UpstreamError
from ..dependencies import get_source
from ..models.documents import Document

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/documents")
async def list_documents(source: Annotated[SourceClient, Depends(get_source)]) -> list[Document]:
    try:
        return await source.list_documents()
    except UpstreamError as exc:
        raise HTTPException(502, f"Source API unavailable: {exc}") from None
```

The token check is on the router, so every route added to it later is protected too.

- [ ] **Step 4: Slim `api/app/main.py` down to the factory**

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .clients import SourceClient
from .config import Settings
from .routers import auth, documents, health


def create_app(settings: Settings | None = None, *, source_http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_http = source_http is None
    if owns_http:
        source_http = httpx.AsyncClient(base_url=settings.source_api_url, timeout=settings.http_timeout)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield
        if owns_http:
            await source_http.aclose()

    app = FastAPI(title="Document publishing service", lifespan=lifespan)
    app.state.settings = settings
    app.state.source = SourceClient(source_http)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    for router in (health.router, auth.router, documents.router):
        app.include_router(router)
    return app
```

- [ ] **Step 5: Run everything**

Run: `docker compose run --rm -T lint && docker compose run --rm -T tests && (cd web && npm run build)`
Expected: `All checks passed!`, `61 passed`, and a successful web build.

- [ ] **Step 6: Check the running API**

```bash
docker compose up -d --wait --build api
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
curl -s localhost:8000/health; echo
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/documents
curl -s localhost:8000/documents -H "Authorization: Bearer $T" | grep -o '"id":"doc-1","filename":"doc-1.txt","contentType":"text/plain"'
curl -s localhost:8000/openapi.json | grep -o '"Document":{"properties":{"id"'
```
Expected:
- `{"ok":true}`;
- `401`;
- the doc-1 fragment with camelCase `contentType`;
- `"Document":{"properties":{"id"`: the `Document` schema is in OpenAPI.

- [ ] **Step 7: Update spec section 3.** In the units table's "Uses" column, `auth` becomes `config`, `dependencies`, and `dependencies` becomes `config`, `clients`.

Suggested commit: `refactor(api): move endpoints into routers`

---

### Task 4: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with the diff of this plan's changes and CLAUDE.md rule 4. Fix what it finds, re-run lint and tests, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the user with the outputs of `lint`, `tests` and Task 3 Step 6, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address api structure review findings`
