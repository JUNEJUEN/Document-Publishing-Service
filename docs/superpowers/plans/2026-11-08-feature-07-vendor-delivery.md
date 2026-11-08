# Feature 7: Vendor Delivery and Quick Retries, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Order:** this plan runs **before** feature 6b (session cookie). The 6b plan (`2026-11-08-feature-06b-session-cookie.md`) is on hold, and will be regenerated on top of this plan's files before it runs. It replaces some of the same files (`config.py`, `test_config.py`, `test_auth.py`, `main.py`).

**Later change (after this plan ran):** the default for `SOURCE_MAX_ATTEMPTS` and `VENDOR_MAX_ATTEMPTS` went from 5 to 3, at the owner's request. The code below records what this plan built; the current values are in the code and in spec sections 5.1 and 10. The affected spots are:
- `DEFAULT_MAX_ATTEMPTS = 3`, and `test_defaults` expects `(3, 3)`;
- `test_delivers_once_after_failures` uses fail, fail-after-store, ok (3 attempts);
- `test_gives_up_after_three_failures_and_keeps_the_archive_copy` uses three failures;
- `test_temporary_source_failures_are_retried` uses `failures=2`;
- with a 30% failure rate, a delivery now fails all its attempts about 2.7% of the time.

**Goal:**
- **Step 5:** after the original is deleted, the job reads the archive copy, checks its sha256, and uploads it to the Vendor with `Idempotency-Key: {job id}`. The vendor's receipt is saved, and the job shows how many attempts it took.
- **Quick retries (spec 5.1):**
  - **What is retried:** temporary failures (network errors, timeouts, 429, any 5xx) of Source and Vendor calls inside a run.
  - **How:** up to 5 attempts, with exponential backoff and full jitter.
  - **What is not:** permanent failures (any other 4xx, a bad answer, our own errors) fail the step at once.

**Architecture:**
- **`clients.py`:**
  - `UpstreamError` gets a `temporary` flag;
  - one shared `_send()` sorts every failure into temporary or permanent. Temporary means timeouts, network errors, a dropped connection, 429 and 5xx. Other transport errors, such as a bad URL, will never pass on a repeat;
  - new `VendorClient.deliver()` returns the receipt id.
- **`backoff.py`, new:**
  - `RetryPolicy(attempts, base_delay, max_delay)`;
  - `.run(call)` repeats a call after temporary failures;
  - the wait before retry *n* is `uniform(0, min(max_delay, base_delay × 2^(n-1)))`;
  - `sleep` and `uniform` can be replaced in tests.
- **`runner.py`:**
  - every Source call in steps 1–4 runs through the Source policy;
  - step 5 (`_deliver`) runs through the Vendor policy. Each attempt adds 1 to `vendor_attempts` and saves the job, so the UI shows the count while it climbs.
- **`archive.py`:** `read()` returns the verified bytes, or `None`; `matches()` now uses it.
- **`main.py`:** builds both HTTP clients, or takes them from tests, and closes the ones it built.
- **Tests:**
  - the shared event log fixture is renamed from `source_events` to `events`, because the Vendor stub writes to it too;
  - a `vendor_app` fixture runs the real Vendor stub, with answers scripted by a `vendor_outcomes` fixture that tests override with `parametrize`;
  - `make_api` takes `source=` and `vendor=` keyword arguments (deferred from feature 6);
  - retry waits are 0.

**Interim behaviour, until feature 8:** a run that passes step 5 ends `done`, and a failure ends `failed`. The status write-back to Source (step 6) comes next.

**Tech stack:** FastAPI 0.115.6, httpx 0.28.1 (multipart upload), Pydantic 2.14, Vue 3 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
- 1 (R1, R2);
- 3 (units `clients`, `backoff`, `runner`);
- 4.1 (`vendor_attempts`, `vendor_receipt`), 4.2 (step 5), 4.7;
- 5.1, 5.2;
- 7.1 (`vendorAttempts`, `vendorReceipt`), 7.4;
- 8 (job details);
- 9 (`test_backoff`, `test_vendor_failure`, the "1 delivery" checks in `test_idempotency`, the growing call order in `test_pipeline`);
- 10 (`VENDOR_API_URL`, `SOURCE_MAX_ATTEMPTS`, `VENDOR_MAX_ATTEMPTS`, `RETRY_BASE_DELAY`, `RETRY_MAX_DELAY`);
- 12, feature 7.

**Deferred:** in `test_vendor_failure.py`, "Error in Source" comes with feature 8, and "`nextRetryAt` shows the next cron run" with feature 11.

---

## File structure

| File | Change |
|---|---|
| `api/app/config.py` | `vendor_api_url`, `source_max_attempts`, `vendor_max_attempts`, `retry_base_delay`, `retry_max_delay` |
| `api/app/backoff.py` | New: `RetryPolicy` |
| `api/app/clients.py` | `UpstreamError.temporary`; shared `_send`; `VendorClient` |
| `api/app/models/vendor.py` | New: `Receipt` |
| `api/app/models/jobs.py` | `vendor_attempts`, `vendor_receipt` on `Job` and `JobOut` |
| `api/app/archive.py` | `read()`; `matches()` uses it |
| `api/app/runner.py` | Retries; step 5; takes `settings` |
| `api/app/main.py` | `vendor_http`; owns and closes the clients it builds |
| `api/tests/conftest.py` | `events`, `vendor_outcomes`, `vendor_app`; `make_api(source=, vendor=)`; zero retry waits |
| `api/tests/test_config.py` | The new settings |
| `api/tests/test_backoff.py`, `test_vendor_failure.py` | New |
| `api/tests/test_pipeline.py`, `test_jobs.py`, `test_idempotency.py` | Vendor in the call order, the Job object and the "1 delivery" checks; retry tests |
| `api/tests/test_auth.py`, `test_documents.py` | Renamed fixture; `make_api(source=…)` |
| `docker-compose.yml` | `api`: `VENDOR_API_URL` |
| `web/src/api.ts`, `web/src/components/JobList.vue` | `vendorAttempts`, `vendorReceipt` in the job details |
| `docs/superpowers/specs/2026-11-08-document-publishing-service.md` | Sections 3, 7.1, 12 |

Commands are written for Git Bash, run from the repository root. Test counts: 117 before this plan, 141 after.

---

### Task 1: Settings

**Files:** Modify `api/tests/test_config.py`, `api/app/config.py`

- [ ] **Step 1: Write the failing tests.** Replace `api/tests/test_config.py`:

```python
from pathlib import Path

import pytest

from app.config import Settings

REQUIRED = {
    "CLIENT_ID": "demo-client",
    "CLIENT_SECRET": "demo-secret",
    "TOKEN_SECRET": "demo-token-secret-0123456789abcdef",
}


OPTIONAL = (
    "DATA_DIR", "TOKEN_TTL_SECONDS", "CORS_ORIGINS", "SOURCE_API_URL", "VENDOR_API_URL", "HTTP_TIMEOUT",
    "MAX_CONCURRENT_JOBS", "SOURCE_MAX_ATTEMPTS", "VENDOR_MAX_ATTEMPTS", "RETRY_BASE_DELAY", "RETRY_MAX_DELAY",
)


@pytest.fixture
def env(monkeypatch):
    for name in OPTIONAL:
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
    assert settings.source_api_url == "http://source-stub:8001"
    assert settings.vendor_api_url == "http://vendor-stub:8002"
    assert settings.http_timeout == 10.0
    assert settings.max_concurrent_jobs == 10
    assert (settings.source_max_attempts, settings.vendor_max_attempts) == (5, 5)
    assert (settings.retry_base_delay, settings.retry_max_delay) == (0.5, 8.0)


def test_optional_settings_come_from_environment(env, tmp_path):
    env.setenv("DATA_DIR", str(tmp_path))
    env.setenv("TOKEN_TTL_SECONDS", "60")
    env.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    env.setenv("SOURCE_API_URL", "http://src.test")
    env.setenv("HTTP_TIMEOUT", "2.5")
    env.setenv("MAX_CONCURRENT_JOBS", "3")
    env.setenv("VENDOR_API_URL", "http://vendor.test")
    env.setenv("SOURCE_MAX_ATTEMPTS", "2")
    env.setenv("VENDOR_MAX_ATTEMPTS", "7")
    env.setenv("RETRY_BASE_DELAY", "0.1")
    env.setenv("RETRY_MAX_DELAY", "2")
    settings = Settings.from_env()
    assert settings.data_dir == tmp_path
    assert settings.token_ttl_seconds == 60
    assert settings.cors_origins == ("http://a.test", "http://b.test")
    assert settings.source_api_url == "http://src.test"
    assert settings.http_timeout == 2.5
    assert settings.max_concurrent_jobs == 3
    assert settings.vendor_api_url == "http://vendor.test"
    assert (settings.source_max_attempts, settings.vendor_max_attempts) == (2, 7)
    assert (settings.retry_base_delay, settings.retry_max_delay) == (0.1, 2.0)


def test_empty_values_fall_back_to_defaults(env):
    env.setenv("SOURCE_API_URL", "")
    env.setenv("VENDOR_API_URL", "")
    env.setenv("DATA_DIR", "")
    settings = Settings.from_env()
    assert settings.source_api_url == "http://source-stub:8001"
    assert settings.vendor_api_url == "http://vendor-stub:8002"
    assert settings.data_dir == Path("/data")


def test_empty_cors_origins_fall_back_to_default(env):
    env.setenv("CORS_ORIGINS", " , ")
    assert Settings.from_env().cors_origins == ("http://localhost:8080",)


@pytest.mark.parametrize("ttl", ["0", "-5", "abc"])
def test_token_lifetime_must_be_positive(env, ttl):
    env.setenv("TOKEN_TTL_SECONDS", ttl)
    with pytest.raises(ValueError, match="TOKEN_TTL_SECONDS"):
        Settings.from_env()


@pytest.mark.parametrize("timeout", ["0", "-1", "abc", "nan"])
def test_http_timeout_must_be_positive(env, timeout):
    env.setenv("HTTP_TIMEOUT", timeout)
    with pytest.raises(ValueError, match="HTTP_TIMEOUT"):
        Settings.from_env()


def test_missing_required_settings_stop_startup(env):
    for name in REQUIRED:
        env.delenv(name)
    with pytest.raises(ValueError, match="CLIENT_ID, CLIENT_SECRET, TOKEN_SECRET"):
        Settings.from_env()


@pytest.mark.parametrize("limit", ["0", "-1", "abc"])
def test_max_concurrent_jobs_must_be_positive(env, limit):
    env.setenv("MAX_CONCURRENT_JOBS", limit)
    with pytest.raises(ValueError, match="MAX_CONCURRENT_JOBS"):
        Settings.from_env()


@pytest.mark.parametrize("name", ["SOURCE_MAX_ATTEMPTS", "VENDOR_MAX_ATTEMPTS", "RETRY_BASE_DELAY", "RETRY_MAX_DELAY"])
def test_retry_settings_must_be_positive(env, name):
    env.setenv(name, "0")
    with pytest.raises(ValueError, match=name):
        Settings.from_env()
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `7 failed, 114 passed`:
- `test_defaults`;
- `test_optional_settings_come_from_environment`;
- `test_empty_values_fall_back_to_defaults`;
- the 4 new cases.

- [ ] **Step 3: Add the settings.** Replace `api/app/config.py`:

```python
"""Settings read from environment variables."""
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("CLIENT_ID", "CLIENT_SECRET", "TOKEN_SECRET")
DEFAULT_TOKEN_TTL = 300
DEFAULT_DATA_DIR = Path("/data")
DEFAULT_ORIGINS = ("http://localhost:8080",)
DEFAULT_SOURCE_API_URL = "http://source-stub:8001"
DEFAULT_VENDOR_API_URL = "http://vendor-stub:8002"
DEFAULT_HTTP_TIMEOUT = 10.0
DEFAULT_MAX_CONCURRENT_JOBS = 10
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RETRY_BASE_DELAY = 0.5
DEFAULT_RETRY_MAX_DELAY = 8.0


def _positive_number(
    env: Mapping[str, str], name: str, default: float, parse: Callable[[str], float], kind: str
) -> float:
    try:
        value = parse(env.get(name, str(default)))
    except ValueError:
        value = 0
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive {kind}")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    return int(_positive_number(env, name, default, int, "integer"))


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    return _positive_number(env, name, default, float, "number")


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    token_secret: str
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL
    data_dir: Path = DEFAULT_DATA_DIR
    cors_origins: tuple[str, ...] = DEFAULT_ORIGINS
    source_api_url: str = DEFAULT_SOURCE_API_URL
    vendor_api_url: str = DEFAULT_VENDOR_API_URL
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS
    source_max_attempts: int = DEFAULT_MAX_ATTEMPTS
    vendor_max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        missing = [name for name in REQUIRED if not env.get(name)]
        if missing:
            raise ValueError(f"missing required settings: {', '.join(missing)}")
        origins = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            client_id=env["CLIENT_ID"],
            client_secret=env["CLIENT_SECRET"],
            token_secret=env["TOKEN_SECRET"],
            token_ttl_seconds=_positive_int(env, "TOKEN_TTL_SECONDS", DEFAULT_TOKEN_TTL),
            data_dir=Path(env.get("DATA_DIR") or DEFAULT_DATA_DIR),
            cors_origins=origins or DEFAULT_ORIGINS,
            source_api_url=env.get("SOURCE_API_URL") or DEFAULT_SOURCE_API_URL,
            vendor_api_url=env.get("VENDOR_API_URL") or DEFAULT_VENDOR_API_URL,
            http_timeout=_positive_float(env, "HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
            max_concurrent_jobs=_positive_int(env, "MAX_CONCURRENT_JOBS", DEFAULT_MAX_CONCURRENT_JOBS),
            source_max_attempts=_positive_int(env, "SOURCE_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            vendor_max_attempts=_positive_int(env, "VENDOR_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            retry_base_delay=_positive_float(env, "RETRY_BASE_DELAY", DEFAULT_RETRY_BASE_DELAY),
            retry_max_delay=_positive_float(env, "RETRY_MAX_DELAY", DEFAULT_RETRY_MAX_DELAY),
        )
```

- [ ] **Step 4: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `121 passed`, then `All checks passed!`.

Suggested commit: `feat(api): add vendor and retry settings`

---

### Task 2: Retry policy

**Files:** Create `api/tests/test_backoff.py`, `api/app/backoff.py`; modify `api/app/clients.py`

- [ ] **Step 1: Write the failing tests.** `api/tests/test_backoff.py`:

```python
import httpx
import pytest

from app.backoff import RetryPolicy
from app.clients import SourceClient, UpstreamError


def failing(*errors: Exception):
    """A call that raises each error in turn, then answers "ok"; it counts its calls."""
    calls = []

    async def call() -> str:
        calls.append(None)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return "ok"

    return call, calls


async def no_sleep(seconds: float) -> None:
    pass


def policy(attempts: int = 5, base_delay: float = 0.5, max_delay: float = 8, **changes) -> RetryPolicy:
    changes.setdefault("sleep", no_sleep)
    return RetryPolicy(attempts, base_delay, max_delay, **changes)


TEMPORARY = UpstreamError("HTTP 503", temporary=True)


async def test_temporary_errors_are_retried_until_success():
    call, calls = failing(TEMPORARY, TEMPORARY)
    assert await policy().run(call) == "ok"
    assert len(calls) == 3


async def test_gives_up_after_the_last_attempt():
    call, calls = failing(*[TEMPORARY] * 5)
    with pytest.raises(UpstreamError, match="HTTP 503"):
        await policy(attempts=5).run(call)
    assert len(calls) == 5


@pytest.mark.parametrize("error", [UpstreamError("HTTP 404"), ValueError("a bug")], ids=["permanent", "other"])
async def test_other_errors_are_not_retried(error):
    call, calls = failing(error)
    with pytest.raises(type(error)):
        await policy().run(call)
    assert len(calls) == 1


async def test_waits_use_full_jitter_up_to_a_growing_cap():
    ranges, waits = [], []

    def uniform(low: float, high: float) -> float:
        ranges.append((low, high))
        return high

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    call, _ = failing(*[TEMPORARY] * 6)
    with pytest.raises(UpstreamError):
        await policy(attempts=6, base_delay=0.5, max_delay=3, sleep=sleep, uniform=uniform).run(call)
    assert ranges == [(0, 0.5), (0, 1), (0, 2), (0, 3), (0, 3)]  # from 0, doubling, capped
    assert waits == [0.5, 1, 2, 3, 3]


@pytest.mark.parametrize(
    "answer, temporary",
    [
        (lambda: httpx.Response(429), True),
        (lambda: httpx.Response(500), True),
        (lambda: httpx.Response(503), True),
        (lambda: httpx.ConnectError("refused"), True),
        (lambda: httpx.ReadTimeout("slow"), True),
        (lambda: httpx.Response(400), False),
        (lambda: httpx.Response(404), False),
        (lambda: httpx.UnsupportedProtocol("bad URL"), False),
    ],
    ids=["429", "500", "503", "connect-error", "timeout", "400", "404", "bad-url"],
)
async def test_failures_are_sorted_into_temporary_or_permanent(answer, temporary):
    def handler(request: httpx.Request) -> httpx.Response:
        result = answer()
        if isinstance(result, Exception):
            raise result
        return result

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://source") as http:
        with pytest.raises(UpstreamError) as caught:
            await SourceClient(http).get_document("doc-1")
    assert caught.value.temporary is temporary
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_backoff.py`
Expected: a collection error, `ModuleNotFoundError: No module named 'app.backoff'`.

- [ ] **Step 3: Temporary or permanent.** Replace `api/app/clients.py`:

```python
"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document

_DOCUMENT = TypeAdapter(Document)
_DOCUMENT_LIST = TypeAdapter(list[Document])
# Transport failures that may pass on a repeat; others (e.g. a bad URL) never will.
_TEMPORARY_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how.

    `temporary` failures (network, timeout, 429, 5xx) may succeed when repeated.
    """

    def __init__(self, message: str, *, temporary: bool = False):
        super().__init__(message)
        self.temporary = temporary


async def _send(http: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__, temporary=isinstance(exc, _TEMPORARY_TRANSPORT_ERRORS)) from exc
    if not response.is_success:
        temporary = response.status_code == 429 or response.is_server_error
        raise UpstreamError(f"HTTP {response.status_code}", temporary=temporary)
    return response


def _parse[T](response: httpx.Response, adapter: TypeAdapter[T], what: str) -> T:
    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError("answer is not JSON") from exc
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        raise UpstreamError(f"answer is not {what}") from exc


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[Document]:
        return _parse(await _send(self._http, "GET", "/documents"), _DOCUMENT_LIST, "a document list")

    async def get_document(self, document_id: str) -> Document:
        return _parse(await _send(self._http, "GET", f"/documents/{document_id}"), _DOCUMENT, "a document")

    async def get_content(self, document_id: str) -> bytes:
        return (await _send(self._http, "GET", f"/documents/{document_id}/content")).content

    async def set_archive_link(self, document_id: str, uri: str, sha256: str) -> None:
        body = {"uri": uri, "sha256": sha256}
        await _send(self._http, "PUT", f"/documents/{document_id}/archive-link", json=body)

    async def delete_document(self, document_id: str) -> None:
        await _send(self._http, "DELETE", f"/documents/{document_id}")
```

What changes:
- **`UpstreamError`** gets its `temporary` flag.
- **`SourceClient._call`** moves to module level as `_send()`, so Task 3's Vendor client can share it. While moving, it learns which failures are temporary.
- **Answers that can't be parsed stay permanent,** because repeating the call won't fix them.

- [ ] **Step 4: The policy.** `api/app/backoff.py`:

```python
"""Quick retries inside a run: exponential backoff with full jitter."""
import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .clients import UpstreamError


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    base_delay: float
    max_delay: float
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    uniform: Callable[[float, float], float] = random.uniform

    def delay(self, retry: int) -> float:
        """The wait before retry `retry` (1 = the second attempt)."""
        return self.uniform(0, min(self.max_delay, self.base_delay * 2 ** (retry - 1)))

    async def run[T](self, call: Callable[[], Awaitable[T]]) -> T:
        """Runs `call`, repeating it after temporary upstream failures, up to `attempts` times."""
        for retry in range(1, self.attempts):
            try:
                return await call()
            except UpstreamError as exc:
                if not exc.temporary:
                    raise
            await self.sleep(self.delay(retry))
        return await call()
```

- The loop makes `attempts - 1` tries that may be retried, then one last call whose error goes to the caller.
- Only an `UpstreamError` with `temporary=True` is retried. Everything else, our own bugs included, fails at once.

- [ ] **Step 5: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `134 passed`, then `All checks passed!`.

Suggested commit: `feat(api): retry temporary upstream failures with backoff`

---

### Task 3: Vendor delivery

**Files:**
- Modify: `api/tests/conftest.py`, `api/tests/test_auth.py`, `api/tests/test_documents.py`, `api/tests/test_pipeline.py`, `api/tests/test_jobs.py`, `api/tests/test_idempotency.py`
- Create: `api/tests/test_vendor_failure.py`, `api/app/models/vendor.py`
- Modify: `api/app/models/jobs.py`, `api/app/clients.py`, `api/app/archive.py`, `api/app/runner.py`, `api/app/main.py`

- [ ] **Step 1: Rename the event log fixture**, because the Vendor stub writes to it too:

Run: `sed -i 's/source_events/events/g' api/tests/*.py`

- [ ] **Step 2: Test helpers.** Replace `api/tests/conftest.py`:

```python
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
```

- **`vendor_outcomes`:** scripts the Vendor stub's answers. A test overrides it with `@pytest.mark.parametrize("vendor_outcomes", [[...]])`, so `published()` still works unchanged.
- **`make_api(source=…, vendor=…, **changes)`:** a missing transport falls back to the in-process stub.

In `api/tests/test_documents.py`, change both `await make_api(transport)` to `await make_api(source=transport)`.

- [ ] **Step 3: Write the failing tests.**

`api/tests/test_vendor_failure.py` (the brief's vendor-failure rule):

```python
"""The brief's vendor-failure rule: the Vendor fails often, and each document is delivered once."""
import pytest


@pytest.mark.parametrize("vendor_outcomes", [["fail", "fail", "fail_after_store", "ok"]])
async def test_delivers_once_after_failures(published, vendor_app):
    finished = await published("doc-1")
    assert (finished["state"], finished["vendorAttempts"]) == ("done", 4)
    assert list(vendor_app.state.deliveries) == [finished["id"]]  # one delivery, keyed by the job id
    assert finished["vendorReceipt"] == vendor_app.state.deliveries[finished["id"]].receipt_id


@pytest.mark.parametrize("vendor_outcomes", [["fail_after_store", "fail_after_store"]])
async def test_lost_answers_do_not_deliver_twice(published, vendor_app):
    finished = await published("doc-1")
    assert (finished["state"], finished["vendorAttempts"]) == ("done", 3)
    assert len(vendor_app.state.deliveries) == 1


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 5])
async def test_gives_up_after_five_failures_and_keeps_the_archive_copy(published, settings, source_app, vendor_app):
    finished = await published("doc-1")
    assert finished["state"] == "failed"
    assert finished["error"] == "Delivering to the Vendor failed: HTTP 503"
    assert (finished["vendorAttempts"], finished["vendorReceipt"]) == (5, None)
    assert vendor_app.state.deliveries == {}
    assert source_app.state.documents["doc-1"].content is None  # deleted before delivery
    assert (settings.data_dir / "archive" / finished["archivePath"]).exists()


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_vendor_attempts_follow_their_setting(make_api, auth_headers):
    api = await make_api(vendor_max_attempts=2)
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    await make_api.settle()
    finished = (await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)).json()
    assert (finished["state"], finished["vendorAttempts"]) == ("failed", 2)
```

Replace `api/tests/test_pipeline.py`. It adds the Vendor to the call order and three new tests, and changes the two `make_api(intercept(…))` calls to `make_api(source=intercept(…))`:

```python
import asyncio

import httpx
import pytest

from app.archive import Archive


def intercept(source_app, before) -> httpx.MockTransport:
    """The Source stub, with `await before(request)` run before each call reaches it."""
    stub = httpx.ASGITransport(app=source_app)

    async def handler(request: httpx.Request) -> httpx.Response:
        await before(request)
        return await stub.handle_async_request(request)

    return httpx.MockTransport(handler)


async def test_steps_run_in_order(published, source_app, events):
    finished = await published("doc-1")
    assert finished["state"] == "done"
    assert events == [
        ("source", "get", "doc-1"),
        ("source", "get_content", "doc-1"),
        ("source", "archive_link", "doc-1"),
        ("source", "delete", "doc-1"),
        ("vendor", "post", finished["id"]),
    ]
    stored = source_app.state.documents["doc-1"]
    assert stored.content is None
    assert stored.archive_uri == f"archive://{finished['archivePath']}"


async def test_at_most_max_concurrent_jobs_run_at_once(make_api, source_app, auth_headers):
    gate = asyncio.Event()
    inside, most = 0, 0

    async def hold_reads(request: httpx.Request) -> None:
        nonlocal inside, most
        if request.url.path.endswith("/content"):
            inside += 1
            most = max(most, inside)
            await gate.wait()
            inside -= 1

    api = await make_api(source=intercept(source_app, hold_reads), max_concurrent_jobs=2)
    for n in range(1, 6):
        await api.post("/publish", json={"documentId": f"doc-{n}", "requestId": "r"}, headers=auth_headers)
    async with asyncio.timeout(2):
        while inside < 2:
            await asyncio.sleep(0.01)
    states = sorted(j["state"] for j in (await api.get("/jobs", headers=auth_headers)).json())
    assert states == ["queued", "queued", "queued", "running", "running"]
    gate.set()
    await make_api.settle()
    states = {j["state"] for j in (await api.get("/jobs", headers=auth_headers)).json()}
    assert (states, most) == ({"done"}, 2)


async def test_original_is_kept_when_archiving_fails(published, settings, source_app, events):
    (settings.data_dir / "archive" / "doc-1").write_text("a file where a folder must go")
    finished = await published("doc-1")
    assert finished["state"] == "failed"
    assert finished["error"] == "Archiving failed: could not write the archive copy (NotADirectoryError)"
    assert ("source", "delete", "doc-1") not in events
    assert source_app.state.documents["doc-1"].content is not None


async def test_original_is_kept_when_the_archive_copy_is_damaged_before_the_delete(
    make_api, source_app, events, settings, auth_headers
):
    async def damage_archive(request: httpx.Request) -> None:
        if request.url.path.endswith("/archive-link"):
            for path in (settings.data_dir / "archive").rglob("doc-1.txt"):
                path.write_bytes(b"damaged")

    api = await make_api(source=intercept(source_app, damage_archive))
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    await make_api.settle()
    finished = (await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)).json()
    assert finished["state"] == "failed"
    assert finished["error"] == "Verifying the archive copy failed: archive copy is damaged or missing"
    assert ("source", "delete", "doc-1") not in events
    assert source_app.state.documents["doc-1"].content is not None


async def test_unknown_document_fails_after_one_read(published, events):
    finished = await published("doc-99")
    assert finished["state"] == "failed"
    assert finished["error"] == "Reading the document from Source failed: HTTP 404"
    assert events == [("source", "get", "doc-99")]


@pytest.mark.parametrize(
    "error, expected",
    [(OSError(), "Archiving failed: OSError"), (OSError("x" * 1000), ("Archiving failed: " + "x" * 1000)[:500])],
    ids=["empty-message", "long-message"],
)
async def test_error_message_is_never_empty_or_too_long(published, monkeypatch, error, expected):
    def broken_store(*args):
        raise error

    monkeypatch.setattr(Archive, "store", broken_store)
    finished = await published("doc-1")
    assert finished["error"] == expected


def flaky(source_app, failures: int) -> httpx.MockTransport:
    """The Source stub, but the first `failures` metadata reads answer 503."""
    stub = httpx.ASGITransport(app=source_app)
    failed = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/documents/doc-1" and len(failed) < failures:
            failed.append(request)
            return httpx.Response(503)
        return await stub.handle_async_request(request)

    return httpx.MockTransport(handler)


async def test_temporary_source_failures_are_retried(make_api, source_app, auth_headers):
    api = await make_api(source=flaky(source_app, failures=4))
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    await make_api.settle()
    assert (await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)).json()["state"] == "done"


async def test_source_failing_every_attempt_fails_the_job(make_api, source_app, auth_headers, events):
    # Two attempts, set apart from the vendor's: proves the setting reaches the Source policy.
    api = await make_api(source=flaky(source_app, failures=2), source_max_attempts=2)
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    await make_api.settle()
    finished = (await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)).json()
    assert finished["error"] == "Reading the document from Source failed: HTTP 503"
    assert events == []  # the 503s never reached the stub, and nothing else was called


async def test_damaged_archive_copy_after_the_delete_is_never_delivered(
    make_api, source_app, settings, auth_headers, events
):
    async def damage_archive(request: httpx.Request) -> None:
        if request.method == "DELETE":
            for path in (settings.data_dir / "archive").rglob("doc-1.txt"):
                path.write_bytes(b"damaged")

    api = await make_api(source=intercept(source_app, damage_archive))
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    await make_api.settle()
    finished = (await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)).json()
    assert finished["state"] == "failed"
    assert finished["error"] == (
        "Archive copy damaged or missing after the original was deleted; manual action needed"
    )
    assert not [event for event in events if event[0] == "vendor"]
```

Replace `api/tests/test_jobs.py` (the Job object's new fields; a finished job shows its receipt):

```python
import hashlib


async def test_publish_answers_202_with_the_job_object(publish):
    response = await publish("doc-1", "req-1")
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "id", "requestId", "documentId", "state", "error", "createdAt", "updatedAt", "archivePath",
        "vendorAttempts", "vendorReceipt",
    }
    assert (body["requestId"], body["documentId"], body["state"]) == ("req-1", "doc-1", "queued")
    assert body["createdAt"].endswith("Z")


async def test_finished_job_shows_its_archive_path_and_receipt(published, source_app, vendor_app):
    content = source_app.state.documents["doc-1"].content
    finished = await published("doc-1")
    assert finished["state"] == "done"
    assert finished["error"] is None
    assert finished["archivePath"] == f"doc-1/{hashlib.sha256(content).hexdigest()}/doc-1.txt"
    assert finished["vendorAttempts"] == 1
    assert finished["vendorReceipt"] == vendor_app.state.deliveries[finished["id"]].receipt_id


async def test_job_list_is_newest_first(api, auth_headers, published):
    first, second = await published("doc-1"), await published("doc-2")
    response = await api.get("/jobs", headers=auth_headers)
    assert response.status_code == 200
    assert [j["id"] for j in response.json()] == [second["id"], first["id"]]


async def test_unknown_job_is_404(api, auth_headers):
    response = await api.get("/jobs/unknown", headers=auth_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "job not found"}


async def test_publish_body_is_documented(api):
    body = (await api.get("/openapi.json")).json()["paths"]["/publish"]["post"]["requestBody"]
    schema = body["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {"documentId", "requestId"}
```

Replace `api/tests/test_idempotency.py` (the "1 delivery" checks deferred from feature 6):

```python
import asyncio


async def test_same_request_twice_gives_the_same_job(publish, settle, events):
    first = await publish("doc-1", "req-1")
    second = await publish("doc-1", "req-1")
    await settle()
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert events.count(("source", "delete", "doc-1")) == 1
    assert events.count(("vendor", "post", first.json()["id"])) == 1  # delivered once


async def test_simultaneous_duplicates_create_one_job(api, auth_headers, publish, settle):
    responses = await asyncio.gather(*(publish("doc-1", "req-1") for _ in range(10)))
    await settle()
    assert {r.json()["id"] for r in responses} == {responses[0].json()["id"]}
    assert len((await api.get("/jobs", headers=auth_headers)).json()) == 1


async def test_duplicate_after_the_job_finished_gives_the_same_job(published, publish, events):
    finished = await published("doc-1")
    again = await publish("doc-1")
    assert again.json() == finished
    assert events.count(("source", "get", "doc-1")) == 1
    assert events.count(("vendor", "post", finished["id"])) == 1  # delivered once


async def test_new_request_id_for_the_same_document_gives_the_same_job(publish, settle):
    first = await publish("doc-1", "req-1")
    second = await publish("doc-1", "req-2")
    await settle()
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["requestId"] == "req-1"
```

- [ ] **Step 4: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `22 failed, 66 passed, 53 errors`. The errors come from fixture setup, `TypeError: create_app() got an unexpected keyword argument 'vendor_http'`.

- [ ] **Step 5: Models.** `api/app/models/vendor.py`:

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Receipt(BaseModel):
    """The Vendor's answer to a delivery."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True)

    receipt_id: str
```

Replace `api/app/models/jobs.py`:

```python
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

JobState = Literal["queued", "running", "done", "failed"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Job(BaseModel):
    """The job record, saved as /data/jobs/{document_id}.json."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    request_id: str
    document_id: str
    state: JobState = "queued"
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    # Checkpoints of steps 2-5.
    filename: str | None = None
    content_type: str | None = None
    archive_path: str | None = None
    sha256: str | None = None
    archive_linked: bool = False
    source_deleted: bool = False
    vendor_receipt: str | None = None
    vendor_attempts: int = 0  # a counter, never reset


class JobOut(BaseModel):
    """A job as the API shows it."""

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True, from_attributes=True)

    id: str
    request_id: str
    document_id: str
    state: JobState
    error: str | None
    created_at: datetime
    updated_at: datetime
    archive_path: str | None
    vendor_attempts: int
    vendor_receipt: str | None


class PublishRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    # It becomes a folder name, so it can't be ".." or contain slashes.
    document_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")]
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
```

- [ ] **Step 6: Clients.** Replace `api/app/clients.py`:

```python
"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document
from .models.vendor import Receipt

_DOCUMENT = TypeAdapter(Document)
_DOCUMENT_LIST = TypeAdapter(list[Document])
_RECEIPT = TypeAdapter(Receipt)
# Transport failures that may pass on a repeat; others (e.g. a bad URL) never will.
_TEMPORARY_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how.

    `temporary` failures (network, timeout, 429, 5xx) may succeed when repeated.
    """

    def __init__(self, message: str, *, temporary: bool = False):
        super().__init__(message)
        self.temporary = temporary


async def _send(http: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__, temporary=isinstance(exc, _TEMPORARY_TRANSPORT_ERRORS)) from exc
    if not response.is_success:
        temporary = response.status_code == 429 or response.is_server_error
        raise UpstreamError(f"HTTP {response.status_code}", temporary=temporary)
    return response


def _parse[T](response: httpx.Response, adapter: TypeAdapter[T], what: str) -> T:
    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError("answer is not JSON") from exc
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        raise UpstreamError(f"answer is not {what}") from exc


class SourceClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list_documents(self) -> list[Document]:
        return _parse(await _send(self._http, "GET", "/documents"), _DOCUMENT_LIST, "a document list")

    async def get_document(self, document_id: str) -> Document:
        return _parse(await _send(self._http, "GET", f"/documents/{document_id}"), _DOCUMENT, "a document")

    async def get_content(self, document_id: str) -> bytes:
        return (await _send(self._http, "GET", f"/documents/{document_id}/content")).content

    async def set_archive_link(self, document_id: str, uri: str, sha256: str) -> None:
        body = {"uri": uri, "sha256": sha256}
        await _send(self._http, "PUT", f"/documents/{document_id}/archive-link", json=body)

    async def delete_document(self, document_id: str) -> None:
        await _send(self._http, "DELETE", f"/documents/{document_id}")


class VendorClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def deliver(self, key: str, filename: str, content_type: str, content: bytes) -> str:
        """Uploads the file and returns the receipt id; the same key never stores it twice."""
        files = {"file": (filename, content, content_type)}
        response = await _send(self._http, "POST", "/files", files=files, headers={"Idempotency-Key": key})
        return _parse(response, _RECEIPT, "a receipt").receipt_id
```

- **New:** `Receipt` and `VendorClient`; the rest is unchanged since Task 2.
- **No sha256 check on the vendor's answer:** only our own sha256 counts (spec D7).

- [ ] **Step 7: Archive.** In `api/app/archive.py`, replace `matches()` with:

```python
    def read(self, relative: str, digest: str) -> bytes | None:
        """The archive copy, or None when it can't be read or its sha256 differs."""
        try:
            content = self._path(relative).read_bytes()
        except OSError:
            return None
        return content if _sha256(content) == digest else None

    def matches(self, relative: str, digest: str) -> bool:
        """True when the archive copy can be read and has this sha256."""
        return self.read(relative, digest) is not None
```

- [ ] **Step 8: Runner.** Replace `api/app/runner.py`:

```python
"""Runs each job as an asyncio task, a limited number at a time."""
import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from .archive import Archive, ArchiveError
from .backoff import RetryPolicy
from .clients import SourceClient, VendorClient
from .config import Settings
from .models.jobs import Job, JobState
from .store import JobStore

MAX_MESSAGE = 500
DAMAGED_AFTER_DELETE = "Archive copy damaged or missing after the original was deleted; manual action needed"


class StepFailed(Exception):
    pass


def _reason(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__)[:MAX_MESSAGE]


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Names the failed step in the error message."""
    try:
        yield
    except Exception as exc:
        raise StepFailed(f"{name} failed: {_reason(exc)}") from exc


class Runner:
    def __init__(
        self, store: JobStore, archive: Archive, source: SourceClient, vendor: VendorClient, settings: Settings
    ):
        self._store = store
        self._archive = archive
        self._source = source
        self._vendor = vendor
        delays = (settings.retry_base_delay, settings.retry_max_delay)
        self._source_retry = RetryPolicy(settings.source_max_attempts, *delays)
        self._vendor_retry = RetryPolicy(settings.vendor_max_attempts, *delays)
        self._slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def publish(self, document_id: str, request_id: str) -> Job:
        """The document's job: the existing one, or a new one that starts now."""
        job = self._store.by_document(document_id)
        if job is None:
            job = Job(document_id=document_id, request_id=request_id)
            self._store.save(job)
            self._tasks[job.id] = asyncio.create_task(self._run(job))
        return job

    async def wait_idle(self) -> None:
        """Waits until every job task has finished."""
        while pending := [task for task in self._tasks.values() if not task.done()]:
            await asyncio.wait(pending)

    async def shutdown(self) -> None:
        """Cancels the running tasks; job states are left as they are."""
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, job: Job) -> None:
        async with self._slots:
            self._set_state(job, "running")
            try:
                await self._steps(job)
            except Exception as exc:
                self._set_state(job, "failed", _reason(exc))
            else:
                self._set_state(job, "done")

    def _set_state(self, job: Job, state: JobState, error: str | None = None) -> None:
        job.state, job.error = state, error
        self._store.save(job)

    async def _steps(self, job: Job) -> None:
        document_id = job.document_id
        with _step("Reading the document from Source"):
            document = await self._source_retry.run(lambda: self._source.get_document(document_id))
            content = await self._source_retry.run(lambda: self._source.get_content(document_id))
        with _step("Archiving"):
            job.archive_path, job.sha256 = await asyncio.to_thread(
                self._archive.store, document_id, document.filename, content
            )
        job.filename, job.content_type = document.filename, document.content_type
        self._store.save(job)
        uri = f"archive://{job.archive_path}"
        with _step("Linking the archive copy in Source"):
            await self._source_retry.run(lambda: self._source.set_archive_link(document_id, uri, job.sha256))
        job.archive_linked = True
        self._store.save(job)
        with _step("Verifying the archive copy"):
            if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                raise ArchiveError("archive copy is damaged or missing")
        with _step("Deleting the original in Source"):
            await self._source_retry.run(lambda: self._source.delete_document(document_id))
        job.source_deleted = True
        self._store.save(job)
        await self._deliver(job)

    async def _deliver(self, job: Job) -> None:
        """Step 5: sends the archive copy, now the only copy, to the Vendor."""
        content = await asyncio.to_thread(self._archive.read, job.archive_path, job.sha256)
        if content is None:
            raise ArchiveError(DAMAGED_AFTER_DELETE)

        async def attempt() -> str:
            job.vendor_attempts += 1
            self._store.save(job)  # the UI shows the attempts while they happen
            return await self._vendor.deliver(job.id, job.filename, job.content_type, content)

        with _step("Delivering to the Vendor"):
            job.vendor_receipt = await self._vendor_retry.run(attempt)
        self._store.save(job)
```

How it follows the spec:
- **Step 5 reads the archive copy, never Source (4.7).** If the copy is damaged or missing, the job fails with the spec's exact message, and the Vendor is never called.
- **Idempotency key = job id:** a repeat after a lost answer ("fail after store") gets the first receipt, so the file is stored once (R2).
- **`vendor_attempts`** counts every attempt and is never reset. Each attempt saves the job first, so polling shows the count climbing.
- **Settings:** the runner takes `settings` instead of a growing list of numbers; it reads the limit and both retry policies from it.

- [ ] **Step 9: Wire the Vendor client.** Replace `api/app/main.py`:

```python
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
```

`own()` builds a client for a URL and remembers it, so only the clients the app built get closed.

- [ ] **Step 10: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `141 passed`, then `All checks passed!`.

Run it twice more with warnings as errors: `docker compose run --rm -T tests -q -W error`
Expected: `141 passed` both times.

Suggested commit: `feat(api): deliver archive copies to the vendor`

---

### Task 4: Compose and web app

**Files:** Modify `docker-compose.yml`, `web/src/api.ts`, `web/src/components/JobList.vue`

- [ ] **Step 1: Compose.** In `docker-compose.yml`, in the `api` service's `environment`, after `SOURCE_API_URL`:

```yaml
      VENDOR_API_URL: http://vendor-stub:8002
```

- [ ] **Step 2: Job type.** In `web/src/api.ts`, in `interface Job`, after `archivePath`:

```ts
  vendorAttempts: number
  vendorReceipt: string | null
```

- [ ] **Step 3: Job details (spec 8).** In `web/src/components/JobList.vue`, replace `details()`:

```ts
function details(job: Job): string {
  // Jobs finished before vendor delivery existed have no receipt.
  if (job.state === 'done') return job.vendorReceipt ? `Vendor receipt ${job.vendorReceipt}` : ''
  if (job.state === 'failed') return job.error ?? ''
  return job.error ?? `Vendor attempts so far: ${job.vendorAttempts}`
}
```

- [ ] **Step 4: Build**

Run: `(cd web && npm run build)`
Expected: `vue-tsc` passes and Vite prints `✓ built`.

Suggested commit: `feat(web): show vendor attempts and receipt`

---

### Task 5: Live check and spec sync

- [ ] **Step 1: Deliver a document on the running stack.** The Vendor stub fails about 30% of the time, so retries are likely. `doc-19` is used, so `doc-1` stays for the browser check. **If `doc-19` was already published,** its old job comes back and nothing is delivered. Pick an id not yet published (check `GET /jobs`), or reset the stack with `docker compose down -v` first.

```bash
docker compose up -d --wait --build api web
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
H="Authorization: Bearer $T"
J=$(curl -s -X POST localhost:8000/publish -H "$H" -H 'Content-Type: application/json' -d '{"documentId":"doc-19","requestId":"live-7"}' | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
for i in $(seq 40); do curl -s localhost:8000/jobs/$J -H "$H" | grep -qE '"state":"(done|failed)"' && break; sleep 0.5; done
curl -s localhost:8000/jobs/$J -H "$H"; echo
curl -s localhost:8002/files | grep -o "\"idempotencyKey\":\"$J\""
```

Expected:
- the job is `done`, with `vendorAttempts` ≥ 1 and a `vendorReceipt`;
- the Vendor's `GET /files` lists exactly one record with `"idempotencyKey":"<job id>"`.

With a 30% failure rate, five failures in a row happen about 0.2% of the time. The job then ends `failed` with "Delivering to the Vendor failed: …"; publish another document to check again.

- [ ] **Step 2: Browser check (owner).** Publish a document at `http://localhost:8080`. While the job runs, its details show "Vendor attempts so far: N". When it is done, they show "Vendor receipt …".

- [ ] **Step 3: Spec sync.** In `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
  - **3, units table:**
    - `models/`: add "`vendor.py` (`Receipt`)";
    - `runner` "Uses": add `config`.
  - **7.1, `error` example:** `"Vendor API answered 503"` becomes `"Delivering to the Vendor failed: HTTP 503"`.
  - **12, feature 6 row, last column:** "Until features 7 and 8, a run that passes step 4 ends `done`." becomes "Until feature 7, a run that passes step 4 ends `done`."
  - **12, feature 7 row, last column:** append "Until feature 8, a run that passes step 5 ends `done`. `make_api` takes `source=` and `vendor=` transports; the event log fixture is `events`."

Suggested commit: `docs: sync spec with feature 7`

---

### Task 6: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with this feature's changed files and CLAUDE.md rule 4. Fix what it finds, re-run lint, tests and the web build, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the owner with the outputs of lint, tests, the web build and Task 5 Step 1, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address feature 7 review findings`
