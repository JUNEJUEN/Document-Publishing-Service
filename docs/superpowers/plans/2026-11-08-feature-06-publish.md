# Feature 6: Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Goal:**
- `POST /publish` starts a job for a document, or returns the job the document already has. The job runs in the background:
  1. read the document from Source;
  2. archive it;
  3. tell Source the archive link;
  4. delete the original in Source.
- `GET /jobs` and `GET /jobs/{id}` show the jobs.
- At most `MAX_CONCURRENT_JOBS` jobs run at once.
- The web app gets a document-id field, a Publish button on each document row, and a job list that polls every 2 s.

**Architecture:**
- **`archive`:** writes a file durably (`.partial`, fsync, rename, folder fsync) to `{documentId}/{sha256}/{safe filename}` and checks it by sha256. Runs in a thread (`asyncio.to_thread`).
- **`models/jobs.py`:** `Job` (the record saved on disk, snake_case), `JobOut` (the API's Job object, camelCase) and `PublishRequest` (the body, with the safe `documentId` pattern).
- **`store`:** jobs in memory, keyed by document, mirrored to `/data/jobs/{documentId}.json` with an atomic write.
- **`runner`:** `publish()` does the duplicate check and creates the job with no `await` in between (spec section 3, rule 1), then starts one asyncio task per job. Each task waits for a semaphore slot (`queued`), then runs steps 1–4 (`running`). Any failure gives `failed` with an error that names the step.
- **`clients`:** `SourceClient` gets the four calls the steps need. All Source calls share one `_call()`.
- **`routers/jobs.py`:** `POST /publish` reads its body only after the token check, so an anonymous broken body gets 401, not 422.
- **Tests:** `make_api` now starts the app's lifespan and shuts it down after the test, and accepts setting changes (`make_api(transport, max_concurrent_jobs=2)`). `settle()` waits for every job task.

**Interim behaviour, until later features:**
- **A run that passes step 4 ends `done`.** Delivery (feature 7) and the status write-back (feature 8) come later. Until then, publishing under Compose really deletes the original in the Source stub without delivering it.
- **Every run starts at step 1.** Skipping steps by checkpoint (spec 4.2, "Where a run starts") only matters for retries, so it comes with feature 10. The checkpoints are already saved.
- **Startup does not tidy job files.** Leftover `.json.tmp` files are already ignored, because only `*.json` is loaded. Unreadable files and interrupted jobs are handled in feature 9.
- **`UpstreamError` does not yet say temporary or permanent.** Quick retries need that, so it comes with feature 7.
- **A failed job-file save can leave a job stuck.** If the disk refuses the save that sets `running`, or the save that records a failure, the task ends and the job keeps its last saved state. The outer guard (spec 4.4) comes with feature 8.

**Tech stack:** FastAPI 0.115.6, Pydantic 2.14, httpx 0.28.1, Vue 3 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
- 1 (R2, R3, R5);
- 2 (D3, D5, D6, D8, D13);
- 3 (units, concurrency rules 1–3);
- 4.1 (job record), 4.2 (steps 1–4), 4.3 (error message), 4.7;
- 5.5;
- 7.1 (Job object, without the retry, vendor and status fields), 7.2 (`/publish`, `/jobs`, `/jobs/{id}`);
- 8 (publish field, row buttons, job list);
- 9 (`test_archive`, `test_store`, `test_idempotency`, `test_pipeline`, `test_auth` cases for feature 6);
- 10 (`MAX_CONCURRENT_JOBS`);
- 12, feature 6.

**Spec changes this plan makes (Task 6):**
- **4.3:** the error names the failed step, e.g. "Reading the document from Source failed: HTTP 404". A bare "HTTP 404" doesn't tell the user what failed.
- **9:** a new `test_jobs.py` row for the Job object and the job endpoints, which no listed test file covers.
- **3:** `dependencies` also hands out the store and the runner.
- **12, feature 6:** the interim behaviour above.

---

## File structure

| File | Change |
|---|---|
| `api/app/config.py` | `max_concurrent_jobs` (`MAX_CONCURRENT_JOBS`, default 10) |
| `api/app/archive.py` | New: `Archive`, `ArchiveError`, `safe_filename` |
| `api/app/models/jobs.py` | New: `Job`, `JobOut`, `PublishRequest`, `JobState`, `utc_now` |
| `api/app/store.py` | New: `JobStore` |
| `api/app/clients.py` | `get_document`, `get_content`, `set_archive_link`, `delete_document`; shared `_call` and `_parse` |
| `api/app/runner.py` | New: `Runner` |
| `api/app/dependencies.py` | `get_store`, `get_runner` |
| `api/app/routers/jobs.py` | New: `POST /publish`, `GET /jobs`, `GET /jobs/{id}` |
| `api/app/main.py` | Builds the store, archive and runner; shuts the runner down |
| `api/tests/conftest.py` | `ApiFactory` (lifespan, setting changes, `settle`), `publish`, `job`, `published` |
| `api/tests/test_config.py` | `MAX_CONCURRENT_JOBS` |
| `api/tests/test_archive.py`, `test_store.py`, `test_jobs.py`, `test_idempotency.py`, `test_pipeline.py` | New |
| `api/tests/test_auth.py` | `/publish` and `/jobs` cases |
| `web/src/api.ts` | `Job`, `fetchJobs`, `publish`; one shared `callApi` |
| `web/src/jobs.ts` | New: shared job list, polling, `publishDocument` |
| `web/src/components/PublishForm.vue`, `JobList.vue` | New |
| `web/src/components/DocumentsTable.vue` | Row Publish buttons; reload when a job finishes |
| `web/src/App.vue`, `web/src/style.css` | Layout; job state styles |
| `docs/superpowers/specs/2026-11-08-document-publishing-service.md` | Sections 3, 4.3, 9, 12 |

Commands are written for Git Bash, run from the repository root. Test counts: 61 before this plan, 116 after.

---

### Task 1: `MAX_CONCURRENT_JOBS` setting

**Files:** Modify `api/tests/test_config.py`, `api/app/config.py`

- [ ] **Step 1: Write the failing tests.** In `api/tests/test_config.py`:
  - move the list of optional names out of the `env` fixture into a constant, so the line stays under 120 characters, and add `MAX_CONCURRENT_JOBS`:

```python
OPTIONAL = ("DATA_DIR", "TOKEN_TTL_SECONDS", "CORS_ORIGINS", "SOURCE_API_URL", "HTTP_TIMEOUT", "MAX_CONCURRENT_JOBS")


@pytest.fixture
def env(monkeypatch):
    for name in OPTIONAL:
        monkeypatch.delenv(name, raising=False)
```

  - in `test_defaults`, after the `http_timeout` line: `assert settings.max_concurrent_jobs == 10`;
  - in `test_optional_settings_come_from_environment`, after `env.setenv("HTTP_TIMEOUT", "2.5")`: `env.setenv("MAX_CONCURRENT_JOBS", "3")`, and after the `http_timeout` assert: `assert settings.max_concurrent_jobs == 3`;
  - at the end of the file:

```python
@pytest.mark.parametrize("limit", ["0", "-1", "abc"])
def test_max_concurrent_jobs_must_be_positive(env, limit):
    env.setenv("MAX_CONCURRENT_JOBS", limit)
    with pytest.raises(ValueError, match="MAX_CONCURRENT_JOBS"):
        Settings.from_env()
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_config.py`
Expected: 5 failed (`test_defaults`, `test_optional_settings_come_from_environment` and the 3 new cases).

- [ ] **Step 3: Add the setting.** In `api/app/config.py`:
  - after `DEFAULT_HTTP_TIMEOUT = 10.0`: `DEFAULT_MAX_CONCURRENT_JOBS = 10`;
  - in `Settings`, after `http_timeout`: `max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS`;
  - in `from_env`, after the `http_timeout=` line: `max_concurrent_jobs=_positive_int(env, "MAX_CONCURRENT_JOBS", DEFAULT_MAX_CONCURRENT_JOBS),`.

- [ ] **Step 4: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `64 passed`, then `All checks passed!`.

Suggested commit: `feat(api): add MAX_CONCURRENT_JOBS setting`

---

### Task 2: Archive

**Files:** Create `api/tests/test_archive.py`, `api/app/archive.py`

- [ ] **Step 1: Write the failing tests.** `api/tests/test_archive.py`:

```python
import hashlib

import pytest

from app.archive import Archive, ArchiveError, safe_filename

CONTENT = b"Contract doc-1\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def archive(tmp_path) -> Archive:
    return Archive(tmp_path / "archive")


def test_stores_under_a_stable_path(archive, tmp_path):
    relative, digest = archive.store("doc-1", "doc-1.txt", CONTENT)
    assert (relative, digest) == (f"doc-1/{DIGEST}/doc-1.txt", DIGEST)
    assert (tmp_path / "archive" / relative).read_bytes() == CONTENT


def test_storing_twice_keeps_the_first_copy(archive, tmp_path):
    relative, _ = archive.store("doc-1", "doc-1.txt", CONTENT)
    written = (tmp_path / "archive" / relative).stat().st_mtime_ns
    assert archive.store("doc-1", "doc-1.txt", CONTENT) == (relative, DIGEST)
    assert (tmp_path / "archive" / relative).stat().st_mtime_ns == written


def test_leaves_no_partial_file(archive, tmp_path):
    relative, _ = archive.store("doc-1", "doc-1.txt", CONTENT)
    assert [p.name for p in (tmp_path / "archive" / relative).parent.iterdir()] == ["doc-1.txt"]


def test_replaces_a_damaged_copy(archive, tmp_path):
    relative, _ = archive.store("doc-1", "doc-1.txt", CONTENT)
    (tmp_path / "archive" / relative).write_bytes(b"damaged")
    archive.store("doc-1", "doc-1.txt", CONTENT)
    assert (tmp_path / "archive" / relative).read_bytes() == CONTENT


def test_detects_a_damaged_or_missing_copy(archive, tmp_path):
    relative, digest = archive.store("doc-1", "doc-1.txt", CONTENT)
    assert archive.matches(relative, digest)
    path = tmp_path / "archive" / relative
    path.write_bytes(b"damaged")
    assert not archive.matches(relative, digest)
    path.unlink()
    assert not archive.matches(relative, digest)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("doc-1.txt", "doc-1.txt"),
        ("my report (v2).pdf", "my_report__v2_.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\temp\\a.txt", "a.txt"),
        (".hidden", "hidden"),
        ("..", "document"),
        ("", "document"),
    ],
)
def test_cleans_filenames(name, expected):
    assert safe_filename(name) == expected


def test_refuses_paths_outside_the_root(archive):
    with pytest.raises(ArchiveError, match="leaves the archive root"):
        archive.store("..", "x.txt", CONTENT)
    with pytest.raises(ArchiveError, match="leaves the archive root"):
        archive.matches("../outside.txt", DIGEST)
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_archive.py`
Expected: a collection error, `ModuleNotFoundError: No module named 'app.archive'`.

- [ ] **Step 3: Write the archive.** `api/app/archive.py`:

```python
"""Our own archive copies, stored durably and checked by sha256."""
import hashlib
import os
import re
from pathlib import Path


class ArchiveError(Exception):
    pass


def safe_filename(name: str) -> str:
    """The last path part, limited to letters, digits, dot, underscore and hyphen."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".") or "document"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_durably(path: Path, data: bytes) -> None:
    partial = path.with_name(f"{path.name}.partial")
    with partial.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(partial, path)
    if os.name != "nt":  # Windows cannot fsync a folder
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


class Archive:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()

    def store(self, document_id: str, filename: str, content: bytes) -> tuple[str, str]:
        """Saves the content; returns its path relative to the root, and its sha256."""
        digest = _sha256(content)
        relative = f"{document_id}/{digest}/{safe_filename(filename)}"
        if not self.matches(relative, digest):
            path = self._path(relative)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_durably(path, content)
            except OSError as exc:  # the type only: no server paths in user-facing errors
                raise ArchiveError(f"could not write the archive copy ({type(exc).__name__})") from exc
            if not self.matches(relative, digest):
                raise ArchiveError("archive copy failed its hash check")
        return relative, digest

    def matches(self, relative: str, digest: str) -> bool:
        """True when the archive copy can be read and has this sha256."""
        try:
            return _sha256(self._path(relative).read_bytes()) == digest
        except OSError:
            return False

    def _path(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ArchiveError("archive path leaves the archive root")
        return path
```

How it meets spec 4.2 step 2:
- **Stable path:** the same content always lands at the same path, so storing twice keeps the first copy.
- **Durable write:** the `.partial` file is fsynced, renamed over the final name, and the folder is fsynced, so a crash leaves either no file or a complete one.
- **Read back:** after writing, the file is read again and its sha256 compared.
- **No server paths in errors:** a disk error becomes "could not write the archive copy (NotADirectoryError)". The job error shows in the UI, and from feature 8 also in Source.
- **`matches()` is False for any unreadable copy,** missing or not, so the caller never sees a raw disk error.
- **Second guard against escaping:** `_path()` refuses any path outside the archive root, even though `documentId` is already restricted.

- [ ] **Step 4: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `77 passed`, then `All checks passed!`.

Suggested commit: `feat(api): add durable sha256-checked archive`

---

### Task 3: Job record and store

**Files:** Create `api/tests/test_store.py`, `api/app/models/jobs.py`, `api/app/store.py`

- [ ] **Step 1: Write the failing tests.** `api/tests/test_store.py`:

```python
import json
from datetime import timedelta

from app.models.jobs import Job, utc_now
from app.store import JobStore


def test_finds_jobs_by_document_and_by_id(tmp_path):
    store = JobStore(tmp_path)
    job = Job(document_id="doc-1", request_id="r1")
    store.save(job)
    assert store.by_document("doc-1") is job
    assert store.get(job.id) is job
    assert store.by_document("doc-2") is None
    assert store.get("unknown") is None


def test_reloads_jobs_from_disk(tmp_path):
    job = Job(document_id="doc-1", request_id="r1", state="done", archive_path="doc-1/abc/doc-1.txt")
    JobStore(tmp_path).save(job)
    assert JobStore(tmp_path).by_document("doc-1") == job


def test_lists_newest_first(tmp_path):
    store = JobStore(tmp_path)
    now = utc_now()
    for n, age in enumerate([2, 0, 1]):
        store.save(Job(document_id=f"doc-{n}", request_id="r", created_at=now - timedelta(minutes=age)))
    assert [job.document_id for job in store.newest_first()] == ["doc-1", "doc-2", "doc-0"]


def test_writes_one_complete_file_per_document(tmp_path):
    store = JobStore(tmp_path)
    job = Job(document_id="doc-1", request_id="r1")
    store.save(job)
    job.state = "running"
    store.save(job)
    assert [p.name for p in tmp_path.iterdir()] == ["doc-1.json"]  # no .tmp left
    assert json.loads((tmp_path / "doc-1.json").read_text())["state"] == "running"
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests tests/test_store.py`
Expected: a collection error, `ModuleNotFoundError: No module named 'app.models.jobs'`.

- [ ] **Step 3: Write the models.** `api/app/models/jobs.py`:

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
    # Checkpoints of steps 2-4.
    filename: str | None = None
    content_type: str | None = None
    archive_path: str | None = None
    sha256: str | None = None
    archive_linked: bool = False
    source_deleted: bool = False


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


class PublishRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    # It becomes a folder name, so it can't be ".." or contain slashes.
    document_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")]
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
```

- `Job` has only the fields features 6 needs; features 7, 8 and 11 add theirs (`vendor_attempts`, `outcome`, `auto_retries`…).
- `JobOut` reads a `Job` (`from_attributes`). `validate_by_name` lets it read the snake_case attribute names, and FastAPI writes the camelCase aliases.

- [ ] **Step 4: Write the store.** `api/app/store.py`:

```python
"""Jobs in memory, mirrored to one JSON file per document."""
import os
from pathlib import Path

from .models.jobs import Job, utc_now


def _write_atomic(path: Path, text: str) -> None:
    temp = path.with_name(f"{path.name}.tmp")
    with temp.open("w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


class JobStore:
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self._folder = folder
        jobs = (Job.model_validate_json(path.read_text(encoding="utf-8")) for path in folder.glob("*.json"))
        self._jobs = {job.document_id: job for job in jobs}

    def by_document(self, document_id: str) -> Job | None:
        return self._jobs.get(document_id)

    def get(self, job_id: str) -> Job | None:
        return next((job for job in self._jobs.values() if job.id == job_id), None)

    def newest_first(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def save(self, job: Job) -> None:
        job.updated_at = utc_now()
        _write_atomic(self._folder / f"{job.document_id}.json", job.model_dump_json(indent=2))
        self._jobs[job.document_id] = job
```

- **No `await` anywhere:** every change and save is synchronous (spec section 3, rule 1).
- **Only `*.json` is loaded,** so a leftover `doc-1.json.tmp` is never read as a job.
- **One file per document:** a job's file name is its `document_id`, which the `PublishRequest` pattern keeps safe.

- [ ] **Step 5: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `81 passed`, then `All checks passed!`.

Suggested commit: `feat(api): add job record and file-backed job store`

---

### Task 4: Publish and the job endpoints

**Files:**
- Modify: `api/tests/conftest.py`, `api/tests/test_auth.py`
- Create: `api/tests/test_jobs.py`, `api/tests/test_idempotency.py`, `api/tests/test_pipeline.py`
- Modify: `api/app/clients.py`, `api/app/dependencies.py`, `api/app/main.py`
- Create: `api/app/runner.py`, `api/app/routers/jobs.py`

- [ ] **Step 1: Test helpers.** Replace `api/tests/conftest.py`:

```python
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
```

What changed:
- **`make_api` runs the app's lifespan** (`app.router.lifespan_context`), so the runner's shutdown runs after each test, as it does in production. Everything it builds is closed by one `AsyncExitStack`.
- **Setting changes:** `make_api(transport, max_concurrent_jobs=2)` builds an API with changed settings.
- **`settle()`** waits until every job task of the test's APIs has finished.
- **`publish`, `job`, `published`:** the spec 9 helpers. `published("doc-1")` publishes, settles and returns the finished Job object.

- [ ] **Step 2: Write the failing tests.**

`api/tests/test_jobs.py`:

```python
import hashlib


async def test_publish_answers_202_with_the_job_object(publish):
    response = await publish("doc-1", "req-1")
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "id", "requestId", "documentId", "state", "error", "createdAt", "updatedAt", "archivePath",
    }
    assert (body["requestId"], body["documentId"], body["state"]) == ("req-1", "doc-1", "queued")
    assert body["createdAt"].endswith("Z")


async def test_finished_job_shows_its_archive_path(published, source_app):
    content = source_app.state.documents["doc-1"].content
    finished = await published("doc-1")
    assert finished["state"] == "done"
    assert finished["error"] is None
    assert finished["archivePath"] == f"doc-1/{hashlib.sha256(content).hexdigest()}/doc-1.txt"


async def test_job_list_is_newest_first(api, auth_headers, published):
    first, second = await published("doc-1"), await published("doc-2")
    response = await api.get("/jobs", headers=auth_headers)
    assert response.status_code == 200
    assert [j["id"] for j in response.json()] == [second["id"], first["id"]]


async def test_unknown_job_is_404(api, auth_headers):
    response = await api.get("/jobs/unknown", headers=auth_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "job not found"}
```

`api/tests/test_idempotency.py` (the brief's retry rule; the "1 delivery" checks come with feature 7, "after a restart" with feature 9):

```python
import asyncio


async def test_same_request_twice_gives_the_same_job(publish, settle, source_events):
    first = await publish("doc-1", "req-1")
    second = await publish("doc-1", "req-1")
    await settle()
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert source_events.count(("source", "delete", "doc-1")) == 1


async def test_simultaneous_duplicates_create_one_job(api, auth_headers, publish, settle):
    responses = await asyncio.gather(*(publish("doc-1", "req-1") for _ in range(10)))
    await settle()
    assert {r.json()["id"] for r in responses} == {responses[0].json()["id"]}
    assert len((await api.get("/jobs", headers=auth_headers)).json()) == 1


async def test_duplicate_after_the_job_finished_gives_the_same_job(published, publish, source_events):
    finished = await published("doc-1")
    again = await publish("doc-1")
    assert again.json() == finished
    assert source_events.count(("source", "get", "doc-1")) == 1


async def test_new_request_id_for_the_same_document_gives_the_same_job(publish, settle):
    first = await publish("doc-1", "req-1")
    second = await publish("doc-1", "req-2")
    await settle()
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["requestId"] == "req-1"
```

`api/tests/test_pipeline.py`:

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


async def test_steps_run_in_order(published, source_app, source_events):
    finished = await published("doc-1")
    assert finished["state"] == "done"
    assert source_events == [
        ("source", "get", "doc-1"),
        ("source", "get_content", "doc-1"),
        ("source", "archive_link", "doc-1"),
        ("source", "delete", "doc-1"),
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

    api = await make_api(intercept(source_app, hold_reads), max_concurrent_jobs=2)
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


async def test_original_is_kept_when_archiving_fails(published, settings, source_app, source_events):
    (settings.data_dir / "archive" / "doc-1").write_text("a file where a folder must go")
    finished = await published("doc-1")
    assert finished["state"] == "failed"
    assert finished["error"] == "Archiving failed: could not write the archive copy (NotADirectoryError)"
    assert ("source", "delete", "doc-1") not in source_events
    assert source_app.state.documents["doc-1"].content is not None


async def test_original_is_kept_when_the_archive_copy_is_damaged_before_the_delete(
    make_api, source_app, source_events, settings, auth_headers
):
    async def damage_archive(request: httpx.Request) -> None:
        if request.url.path.endswith("/archive-link"):
            for path in (settings.data_dir / "archive").rglob("doc-1.txt"):
                path.write_bytes(b"damaged")

    api = await make_api(intercept(source_app, damage_archive))
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    await make_api.settle()
    finished = (await api.get(f"/jobs/{response.json()['id']}", headers=auth_headers)).json()
    assert finished["state"] == "failed"
    assert finished["error"] == "Deleting the original in Source failed: archive copy is damaged or missing"
    assert ("source", "delete", "doc-1") not in source_events
    assert source_app.state.documents["doc-1"].content is not None


async def test_unknown_document_fails_after_one_read(published, source_events):
    finished = await published("doc-99")
    assert finished["state"] == "failed"
    assert finished["error"] == "Reading the document from Source failed: HTTP 404"
    assert source_events == [("source", "get", "doc-99")]


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
```

- `intercept()` wraps the real Source stub, so a test can pause or damage things at an exact call.
- The concurrency test holds every content read at a gate. When two jobs are inside, the other three must be `queued`.

`api/tests/test_auth.py`: replace the `test_protected_endpoints_need_a_token` decorator with:

```python
@pytest.mark.parametrize(
    "method, path", [("GET", "/documents"), ("POST", "/publish"), ("GET", "/jobs"), ("GET", "/jobs/some-id")]
)
```

and add at the end of the file (the brief's required rejected-call test is the first one):

```python
async def test_anonymous_publish_is_rejected_and_does_nothing(api, auth_headers, source_events, settings):
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "req-1"})
    assert response.status_code == 401
    assert (await api.get("/jobs", headers=auth_headers)).json() == []
    assert not any((settings.data_dir / "jobs").iterdir())
    assert source_events == []


BROKEN_BODIES = [b"{not json", b"[]", b'{"documentId": "doc-1"}', b'{"documentId": 5, "requestId": "r"}']


@pytest.mark.parametrize("body", BROKEN_BODIES)
async def test_broken_publish_body_without_a_token_gets_401(api, body):
    response = await api.post("/publish", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 401


@pytest.mark.parametrize("body", BROKEN_BODIES)
async def test_broken_publish_body_with_a_token_gets_422(api, auth_headers, body):
    headers = {**auth_headers, "Content-Type": "application/json"}
    response = await api.post("/publish", content=body, headers=headers)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize(
    "document_id, request_id",
    [("..", "r"), ("../x", "r"), ("a/b", "r"), (".hidden", "r"), ("", "r"), ("x" * 129, "r"), ("doc-1", ""),
     ("doc-1", "r" * 201)],
    ids=["dot-dot", "parent", "slash", "leading-dot", "empty-id", "long-id", "empty-request", "long-request"],
)
async def test_unsafe_publish_values_get_422(api, auth_headers, source_events, document_id, request_id):
    body = {"documentId": document_id, "requestId": request_id}
    response = await api.post("/publish", json=body, headers=auth_headers)
    assert response.status_code == 422
    assert source_events == []
```

- [ ] **Step 3: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `35 failed, 81 passed`. Every new API test fails, because `/publish` and `/jobs` don't exist yet: most get 404 instead of 202, 401 or 422. `test_original_is_kept_when_archiving_fails` fails earlier, because the archive folder it writes into doesn't exist yet.

- [ ] **Step 4: Source calls for the steps.** Replace `api/app/clients.py`:

```python
"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document

_DOCUMENT = TypeAdapter(Document)
_DOCUMENT_LIST = TypeAdapter(list[Document])


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how."""


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
        return _parse(await self._call("GET", "/documents"), _DOCUMENT_LIST, "a document list")

    async def get_document(self, document_id: str) -> Document:
        return _parse(await self._call("GET", f"/documents/{document_id}"), _DOCUMENT, "a document")

    async def get_content(self, document_id: str) -> bytes:
        return (await self._call("GET", f"/documents/{document_id}/content")).content

    async def set_archive_link(self, document_id: str, uri: str, sha256: str) -> None:
        await self._call("PUT", f"/documents/{document_id}/archive-link", json={"uri": uri, "sha256": sha256})

    async def delete_document(self, document_id: str) -> None:
        await self._call("DELETE", f"/documents/{document_id}")

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamError(type(exc).__name__) from exc
        if not response.is_success:
            raise UpstreamError(f"HTTP {response.status_code}")
        return response
```

`list_documents()` keeps its exact behaviour and 502 reasons; the existing `test_documents.py` proves it.

- [ ] **Step 5: The runner.** `api/app/runner.py`:

```python
"""Runs each job as an asyncio task, a limited number at a time."""
import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from .archive import Archive, ArchiveError
from .clients import SourceClient
from .models.jobs import Job, JobState
from .store import JobStore

MAX_MESSAGE = 500


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
    def __init__(self, store: JobStore, archive: Archive, source: SourceClient, max_concurrent: int):
        self._store = store
        self._archive = archive
        self._source = source
        self._slots = asyncio.Semaphore(max_concurrent)
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
            document = await self._source.get_document(document_id)
            content = await self._source.get_content(document_id)
        with _step("Archiving"):
            job.archive_path, job.sha256 = await asyncio.to_thread(
                self._archive.store, document_id, document.filename, content
            )
        job.filename, job.content_type = document.filename, document.content_type
        self._store.save(job)
        with _step("Linking the archive copy in Source"):
            await self._source.set_archive_link(document_id, f"archive://{job.archive_path}", job.sha256)
        job.archive_linked = True
        self._store.save(job)
        with _step("Deleting the original in Source"):
            if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                raise ArchiveError("archive copy is damaged or missing")
            await self._source.delete_document(document_id)
        job.source_deleted = True
        self._store.save(job)
```

How it follows the spec:
- **Duplicates (D5, R2):** `publish()` checks, creates, saves and starts the task with no `await`, so ten simultaneous requests still create one job.
- **Concurrency limit (D13, rule 3):** the task waits for a semaphore slot while the job is `queued`, and switches to `running` once it has one.
- **Never delete an unsafe original (R3):** step 4 checks the archive copy's sha256 again, right before the delete.
- **Checkpoints:** each step's result is saved before the next step starts.
- **Errors (4.3):** `_step()` names the failed step. An empty exception text becomes its type name, and the error is cut to 500 characters. `except Exception` leaves cancellation alone, so shutdown still works.
- **Shutdown:** tasks are cancelled and job states are left as they are; feature 9 handles them at the next startup.

- [ ] **Step 6: Dependencies.** Replace `api/app/dependencies.py`:

```python
"""Hands shared objects from app.state to routes."""
from fastapi import Request

from .clients import SourceClient
from .config import Settings
from .runner import Runner
from .store import JobStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_source(request: Request) -> SourceClient:
    return request.app.state.source


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_runner(request: Request) -> Runner:
    return request.app.state.runner
```

- [ ] **Step 7: The routes.** `api/app/routers/jobs.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..auth import require_token
from ..dependencies import get_runner, get_store
from ..models.jobs import JobOut, PublishRequest
from ..runner import Runner
from ..store import JobStore

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/publish", status_code=202)
async def publish(request: Request, runner: Annotated[Runner, Depends(get_runner)]) -> JobOut:
    # Read after the token check, so an anonymous broken body gets 401, not 422.
    try:
        order = PublishRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from None
    return JobOut.model_validate(runner.publish(order.document_id, order.request_id))


@router.get("/jobs")
async def list_jobs(store: Annotated[JobStore, Depends(get_store)]) -> list[JobOut]:
    return [JobOut.model_validate(job) for job in store.newest_first()]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, store: Annotated[JobStore, Depends(get_store)]) -> JobOut:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return JobOut.model_validate(job)
```

- **Token first:** the token check sits on the router. The handler reads the body itself, after that check.
- **422 shape:** a bad body raises `RequestValidationError`, so FastAPI answers with its usual `{"detail": [...]}`.

- [ ] **Step 8: Wire it up.** Replace `api/app/main.py`:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .archive import Archive
from .clients import SourceClient
from .config import Settings
from .routers import auth, documents, health, jobs
from .runner import Runner
from .store import JobStore


def create_app(settings: Settings | None = None, *, source_http: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    owns_http = source_http is None
    if owns_http:
        source_http = httpx.AsyncClient(base_url=settings.source_api_url, timeout=settings.http_timeout)
    source = SourceClient(source_http)
    store = JobStore(settings.data_dir / "jobs")
    runner = Runner(store, Archive(settings.data_dir / "archive"), source, settings.max_concurrent_jobs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield
        await runner.shutdown()
        if owns_http:
            await source_http.aclose()

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

- [ ] **Step 9: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `116 passed`, then `All checks passed!`.

Run the suite twice more, with warnings as errors, to catch timing problems and unclosed tasks:
`docker compose run --rm -T tests -q -W error`
Expected: `116 passed` both times.

Suggested commit: `feat(api): publish documents through archive and delete`

---

### Task 5: Web app

**Files:**
- Modify: `web/src/api.ts`, `web/src/components/DocumentsTable.vue`, `web/src/App.vue`, `web/src/style.css`
- Create: `web/src/jobs.ts`, `web/src/components/PublishForm.vue`, `web/src/components/JobList.vue`

The frontend has no unit tests (spec 9); `npm run build` type-checks it, and Task 6 checks it in the browser.

- [ ] **Step 1: API calls.** Replace `web/src/api.ts`:

```ts
import { jsonField, readJson } from './http'
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

export type JobState = 'queued' | 'running' | 'done' | 'failed'

export interface Job {
  id: string
  requestId: string
  documentId: string
  state: JobState
  error: string | null
  createdAt: string
  updatedAt: string
  archivePath: string | null
}

class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function errorText(res: Response): Promise<string> {
  const detail = await jsonField(res, 'detail')
  return typeof detail === 'string' ? detail : `HTTP ${res.status}`
}

/** The JSON body of a successful call; otherwise throws a message for the user. */
async function callApi(path: string, init?: RequestInit): Promise<unknown> {
  const res = await authFetch(path, init)
  // A 401 here means renewal failed temporarily; the session is still active.
  if (res.status === 401) throw new Error('Could not refresh your login. Please try again.')
  if (!res.ok) throw new ApiError(res.status, await errorText(res))
  return readJson(res)
}

function asList<T>(data: unknown): T[] {
  if (!Array.isArray(data)) throw new Error('Unexpected response from the API')
  return data as T[]
}

function newRequestId(): string {
  // randomUUID exists only in secure contexts (https or localhost).
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export async function fetchDocuments(): Promise<SourceDocument[]> {
  return asList<SourceDocument>(await callApi('/documents'))
}

export async function fetchJobs(): Promise<Job[]> {
  return asList<Job>(await callApi('/jobs'))
}

export async function publish(documentId: string): Promise<void> {
  try {
    await callApi('/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ documentId, requestId: newRequestId() }),
    })
  } catch (e) {
    // FastAPI's 422 lists the invalid fields; the only typed one is the id.
    if (e instanceof ApiError && e.status === 422) {
      throw new Error('Invalid document id. Use letters, digits, ".", "_" or "-".')
    }
    throw e
  }
}
```

- **One `callApi`:** the 401 and error handling that `fetchDocuments` had is now shared by every call. It throws an `ApiError` that carries the HTTP status.
- **422:** `publish()` turns a 422 into a readable "Invalid document id" message, so the shared helper stays generic.
- **A fresh `requestId` per click** (spec 8), with a fallback where `crypto.randomUUID` is missing.

- [ ] **Step 2: Shared job list.** `web/src/jobs.ts`:

```ts
import { reactive } from 'vue'
import { fetchJobs, publish, type Job } from './api'
import { errorMessage } from './http'

const POLL_MS = 2000

/** The job list shared by the page; polled while the job list is shown. */
export const jobList = reactive({ jobs: [] as Job[], loaded: false, error: '' })
let latest = 0 // only the newest refresh may update the list
let generation = 0 // a new start or a stop ends the previous polling loop
let timer: ReturnType<typeof setTimeout> | undefined

export async function refreshJobs(): Promise<void> {
  const request = ++latest
  try {
    const jobs = await fetchJobs()
    if (request === latest) Object.assign(jobList, { jobs, error: '' })
  } catch (e) {
    // Keep the last list; only show what went wrong.
    if (request === latest) jobList.error = errorMessage(e)
  } finally {
    if (request === latest) jobList.loaded = true
  }
}

export function startPolling(): void {
  // Also clears anything a late answer wrote after the last stop.
  Object.assign(jobList, { jobs: [], loaded: false, error: '' })
  const current = ++generation
  const poll = async () => {
    await refreshJobs()
    if (current === generation) timer = setTimeout(poll, POLL_MS)
  }
  void poll()
}

export function stopPolling(): void {
  generation++
  latest++ // ignore answers still on their way
  clearTimeout(timer)
}

/** Shared by the document-id field and the row buttons. */
export async function publishDocument(documentId: string): Promise<void> {
  await publish(documentId)
  await refreshJobs()
}
```

- **Polling:** the next poll is scheduled only after the previous answer, so a slow API never piles up requests.
- **Stale answers:** `latest` drops answers older than the newest refresh, and `stopPolling()` drops answers still on their way. `startPolling()` starts from an empty list, so nothing written after a logout survives into the next login.
- **Shared publish:** the document-id field and the row buttons both use `publishDocument()`, which refreshes the list right away.

- [ ] **Step 3: Publish form.** `web/src/components/PublishForm.vue`:

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { errorMessage } from '../http'
import { publishDocument } from '../jobs'

const documentId = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await publishDocument(documentId.value.trim())
    documentId.value = ''
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="card" @submit.prevent="submit">
    <h2>Start a publication</h2>
    <label>Document id <input v-model="documentId" placeholder="doc-1" required /></label>
    <button :disabled="busy">{{ busy ? 'Publishing…' : 'Publish' }}</button>
    <p v-if="error" class="error">{{ error }}</p>
  </form>
</template>
```

It follows `LoginForm.vue`. Any id can be tried, so it also works when the documents table can't load.

- [ ] **Step 4: Job list.** `web/src/components/JobList.vue`:

```vue
<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import type { Job, JobState } from '../api'
import { jobList, startPolling, stopPolling } from '../jobs'

const LABELS: Record<JobState, string> = { queued: 'Waiting', running: 'Running', done: 'Done', failed: 'Failed' }

function details(job: Job): string {
  return job.state === 'done' ? `Archived as ${job.archivePath}` : (job.error ?? '')
}

onMounted(startPolling)
onUnmounted(stopPolling)
</script>

<template>
  <section class="card">
    <h2>Jobs</h2>
    <p v-if="jobList.error" class="error">{{ jobList.error }}</p>
    <p v-if="!jobList.loaded" class="muted">Loading jobs…</p>
    <p v-else-if="jobList.jobs.length === 0 && !jobList.error" class="muted">No jobs yet</p>
    <div v-else-if="jobList.jobs.length" class="table-scroll">
      <table>
        <thead>
          <tr><th>Started</th><th>Document</th><th>State</th><th>Details</th></tr>
        </thead>
        <tbody>
          <tr v-for="job in jobList.jobs" :key="job.id">
            <td>{{ new Date(job.createdAt).toLocaleString() }}</td>
            <td>{{ job.documentId }}</td>
            <td><span :class="['state', job.state]">{{ LABELS[job.state] }}</span></td>
            <td>{{ details(job) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
```

- **Polling runs only while the list is shown.** It is shown only while logged in, so logging out stops it.
- **Details:** `done` shows the archive path until feature 7 adds the vendor receipt; the other states show `error`.

- [ ] **Step 5: Row buttons.** Replace `web/src/components/DocumentsTable.vue`:

```vue
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { fetchDocuments, type SourceDocument } from '../api'
import { errorMessage } from '../http'
import { jobList, publishDocument } from '../jobs'

const documents = ref<SourceDocument[]>([])
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const publishError = ref('')
const publishing = reactive(new Set<string>())
let latest = 0 // only the newest refresh may update the table

async function refresh() {
  const request = ++latest
  busy.value = true
  error.value = ''
  try {
    const result = await fetchDocuments()
    if (request === latest) documents.value = result
  } catch (e) {
    // Keep the last list; only show what went wrong.
    if (request === latest) error.value = errorMessage(e)
  } finally {
    if (request === latest) {
      busy.value = false
      loaded.value = true
    }
  }
}

async function publishRow(documentId: string) {
  publishing.add(documentId)
  publishError.value = ''
  try {
    await publishDocument(documentId)
  } catch (e) {
    publishError.value = errorMessage(e)
  } finally {
    publishing.delete(documentId)
  }
}

// A finished job has changed the document in Source.
const finishedJobs = computed(() =>
  jobList.jobs
    .filter((job) => job.state === 'done' || job.state === 'failed')
    .map((job) => `${job.id}:${job.state}`)
    .join(),
)
watch(finishedJobs, refresh)

onMounted(refresh)
</script>

<template>
  <section class="card">
    <div class="card-head">
      <h2>Documents in Source</h2>
      <button class="secondary" :disabled="busy" @click="refresh">{{ busy ? 'Refreshing…' : 'Refresh' }}</button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="publishError" class="error">{{ publishError }}</p>
    <p v-if="!loaded" class="muted">Loading documents…</p>
    <p v-else-if="documents.length === 0 && !error" class="muted">No documents in Source</p>
    <div v-else-if="documents.length" class="table-scroll">
      <table>
        <thead>
          <tr><th>Id</th><th>Filename</th><th>Size</th><th>Source status</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.id">
            <td>{{ doc.id }}</td>
            <td>{{ doc.filename }}</td>
            <td>{{ doc.size === null ? '—' : `${doc.size} B` }}</td>
            <td>{{ doc.status }}<span v-if="doc.deleted" class="muted"> · deleted</span></td>
            <td>
              <button :disabled="doc.deleted || publishing.has(doc.id)" @click="publishRow(doc.id)">
                {{ publishing.has(doc.id) ? 'Publishing…' : 'Publish' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
```

- **Disabled** for a deleted document, or while that row's request is in flight.
- **Reloads** whenever a job in the list becomes `done` or `failed` (spec 8). When finished jobs already exist, the first job list after login also counts as a change, so the table loads once more at login.
- **Two error lines:** a failed load and a failed row publish each have their own, so a reload never hides a publish error.

- [ ] **Step 6: Layout.** Replace `web/src/App.vue`:

```vue
<script setup lang="ts">
import DocumentsTable from './components/DocumentsTable.vue'
import JobList from './components/JobList.vue'
import LoginForm from './components/LoginForm.vue'
import PublishForm from './components/PublishForm.vue'
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
    <template v-else>
      <PublishForm />
      <DocumentsTable />
      <JobList />
    </template>
  </main>
</template>
```

- [ ] **Step 7: Styles.** In `web/src/style.css`, add `--good: #067647;` after `--bad`, and append:

```css
.state { font-weight: 600; white-space: nowrap; }
.state.done { color: var(--good); }
.state.failed { color: var(--bad); }
.state.queued::before, .state.running::before {
  content: ""; display: inline-block; width: 0.8em; height: 0.8em; margin-right: 6px; vertical-align: -0.1em;
  border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 8: Build**

Run: `cd web && npm run build`
Expected: `vue-tsc` passes and Vite prints `✓ built`.

Suggested commit: `feat(web): publish buttons and polling job list`

---

### Task 6: Live check and spec sync

- [ ] **Step 1: Check the running stack.** `doc-20` is used, so `doc-1` stays untouched for the browser check.

```bash
docker compose up -d --wait --build api web
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
H="Authorization: Bearer $T"
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/publish -H 'Content-Type: application/json' -d '{"documentId":"doc-20","requestId":"live-1"}'
J=$(curl -s -X POST localhost:8000/publish -H "$H" -H 'Content-Type: application/json' -d '{"documentId":"doc-20","requestId":"live-1"}' | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
curl -s -X POST localhost:8000/publish -H "$H" -H 'Content-Type: application/json' -d '{"documentId":"doc-20","requestId":"live-2"}' | grep -o "\"id\":\"$J\""
for i in $(seq 20); do curl -s localhost:8000/jobs/$J -H "$H" | grep -q '"state":"done"' && break; sleep 0.5; done
curl -s localhost:8000/jobs/$J -H "$H"; echo
curl -s localhost:8001/documents/doc-20; echo
MSYS_NO_PATHCONV=1 docker compose exec -T api find /data/archive/doc-20 -type f
MSYS_NO_PATHCONV=1 docker compose exec -T api ls /data/jobs
```

The job runs in the background, so the loop waits up to 10 s for it to finish. `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting `/data/...` into a Windows path.

Expected:
- `401`: no token, no job;
- the same job id again for the second publish (`live-2`);
- the job is `done`, with `"archivePath":"doc-20/<sha256>/doc-20.txt"`;
- Source shows `doc-20` as `"deleted":true` with an `archiveUri`;
- the archive file exists, and `doc-20.json` is in `/data/jobs`.

- [ ] **Step 2: Browser check (owner).** Open `http://localhost:8080` and log in. Then check that:
  1. publishing `doc-1` from the field adds a job that goes Waiting/Running → Done, and the documents table then shows `doc-1` as deleted, with its Publish button disabled;
  2. a row's Publish button does the same for its document;
  3. publishing `doc-99` ends Failed with "Reading the document from Source failed: HTTP 404";
  4. publishing `../x` shows the invalid-document-id message;
  5. after logging out, the browser's Network tab shows no more `/jobs` calls.

- [ ] **Step 3: Spec sync.** In `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
  - **4.3, second bullet:** after "…with the exception text as the message.", add: "The message names the failed step, e.g. "Reading the document from Source failed: HTTP 404"."
  - **3, units table:**
    - `dependencies`: "Uses" becomes `config`, `clients`, `store`, `runner`, and "(settings, Source client, later store and runner)" becomes "(settings, Source client, job store, runner)";
    - `main`: "Uses" becomes `routers`, `clients`, `config`, `archive`, `store`, `runner`;
    - `models/`: "later the job record, job states and request/response shapes" becomes "`jobs.py` (`Job`, `JobOut`, `PublishRequest`)";
    - `routers/`: "later `jobs.py`" becomes "`jobs.py`".
  - **12, feature 6 row, "Spec sections" column:** "7.1 (without the retry fields)" becomes "7.1 (without `vendorAttempts`, `vendorReceipt`, `statusReported` and the retry fields)".
  - **9, test files table:** add, after the `test_pipeline.py` row:
    `| api/tests/test_jobs.py | <ul><li>`POST /publish` answers 202 with the Job object.</li><li>A finished job shows its archive path.</li><li>`GET /jobs` is newest first.</li><li>An unknown job id gets 404.</li></ul> |`
  - **12, feature 6 row, last column:** append: "Until features 7 and 8, a run that passes step 4 ends `done`. Starting a run from its checkpoints (4.2) comes with retry in feature 10."

Suggested commit: `docs: sync spec with feature 6`

---

### Task 7: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with this feature's changed files and CLAUDE.md rule 4. Fix what it finds, re-run lint, tests and the web build, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the owner with the outputs of lint, tests, the web build and Task 6 Step 1, plus the browser checklist, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address feature 6 review findings`
