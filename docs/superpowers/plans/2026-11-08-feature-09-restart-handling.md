# Feature 9: Restart Handling, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Order:** feature 6b (session cookie) is still on hold. Its plan is regenerated on top of the current code before it runs.

**Later change (code review, after this plan ran):** the code below records what this plan built; the current code is in `api/app/store.py`. The review led to these changes:
- **Strict names:** only names matching `{id}.json.corrupt-{UTC timestamp}` exactly count as blocked, so a valid id such as `a.json.corrupt-x` never blocks itself.
- **Startup keeps going:** a file that can't be read or renamed (an `OSError`) no longer stops startup. It is logged, and its document is blocked anyway.
- **Name check:** a file whose name doesn't match the `document_id` inside it counts as unreadable.
- **Log line:** it names the new file: "Unreadable job file doc-1.json set aside as doc-1.json.corrupt-…; its document is blocked".
- **Six new test cases:**
  - a `done` job is left alone;
  - a `.tmp` next to an intact record;
  - a blocked document that also has a readable record;
  - an id that looks like a set-aside file;
  - a rename that fails;
  - a file holding another document's job.

  Tests: 166 after this change.

**Goal (spec 4.6):** after a stop or crash, every unfinished job still gets its status written to Source, and broken files never stop startup.

**Interrupted jobs.** Every job left `queued` or `running`:
- **with no outcome** gets one:
  - `Published` when it had already delivered (`vendor_receipt` set);
  - otherwise `Error` with "Interrupted by a service restart".
- **with an outcome but no status written** keeps that outcome.

Both are started again for **step 6 only**. `done` and `failed` jobs are not touched.

**Damaged and half-written files:**
- **Leftover `*.json.tmp` files** (a save cut short) are deleted before loading.
- **Unreadable job files** (not JSON, or not a job record):
  - are renamed to `{documentId}.json.corrupt-{UTC timestamp}`, and the problem is logged;
  - startup continues;
  - `POST /publish` for that document answers **409** "The job record for this document is unreadable; manual action needed". The block holds across restarts until someone removes the `.corrupt-*` file.

**Shutdown** cancels running tasks and leaves job states as they are. Its test was deferred from feature 8.

**Architecture:**
- **`store.py`:**
  - deletes `*.json.tmp`;
  - sets unreadable files aside;
  - collects the blocked documents from the `*.json.corrupt-*` names;
  - `is_blocked()`.
- **`runner.py`:**
  - `recover()` runs at startup, from the app's lifespan;
  - `_run` skips steps 1–5 when the job already has an outcome (spec 4.2, "where a run starts"). Such a run keeps the job's state until step 6 ends it, so a job left `queued` stays `queued` while it waits (spec 3, rule 3);
  - `publish()` raises `JobRecordUnreadable` for a blocked document.
- **`routers/jobs.py`:** turns `JobRecordUnreadable` into the 409.
- **Tests:**
  - `ApiFactory.shutdown()` stops the job tasks the way the app's shutdown does;
  - a restart is simulated by building a second API on the same data folder.

**Interim, until later features:**
- **The retry cron** (spec 4.6 step 3) comes with feature 11.
- **The "delivered on retry" tests for interrupted jobs** come with feature 10, the Retry button.

**Tech stack:** FastAPI 0.115.6, Pydantic 2.14, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
- 1 (R4, second exception);
- 3 (rule 3, step-6-only runs);
- 4.1 (`.tmp` and `.corrupt-*` files), 4.2 ("where a run starts", the saved-outcome case), 4.6;
- 7.2 (`/publish` 409);
- 9 (`test_startup`; `test_idempotency` "duplicate after a restart");
- 12, feature 9.

---

## File structure

| File | Change |
|---|---|
| `api/app/store.py` | Delete `.tmp`; set unreadable files aside; blocked documents |
| `api/app/runner.py` | `recover()`; step-6-only runs; `JobRecordUnreadable` |
| `api/app/routers/jobs.py` | 409 for a blocked document |
| `api/app/main.py` | `runner.recover()` at startup |
| `api/tests/conftest.py` | `ApiFactory.shutdown()` |
| `api/tests/test_startup.py` | New |
| `api/tests/test_pipeline.py` | Shutdown test |
| `api/tests/test_idempotency.py` | Duplicate after a restart |
| `docs/superpowers/specs/2026-11-08-document-publishing-service.md` | Section 12 |

Commands are written for Git Bash, run from the repository root. Test counts: 147 before this plan, 161 after.

---

### Task 1: Restart handling

**Files:**
- Modify: `api/tests/conftest.py`, `api/tests/test_pipeline.py`, `api/tests/test_idempotency.py`
- Create: `api/tests/test_startup.py`
- Modify: `api/app/store.py`, `api/app/runner.py`, `api/app/routers/jobs.py`, `api/app/main.py`

- [ ] **Step 1: Test helper.** In `api/tests/conftest.py`, add to `ApiFactory`, before `settle()`:

```python
    async def shutdown(self) -> None:
        """Stops every job task, as the app's shutdown does."""
        for app in self._apps:
            await app.state.runner.shutdown()
```

- [ ] **Step 2: Write the tests.** `api/tests/test_startup.py`:

```python
"""Startup after a stop or crash (spec 4.6): every unfinished job gets a status written."""
import asyncio

import httpx
import pytest

from app.models.jobs import Job
from app.store import JobStore

INTERRUPTED = "Interrupted by a service restart"
BLOCKED = "The job record for this document is unreadable; manual action needed"


@pytest.fixture
def jobs_dir(settings):
    return settings.data_dir / "jobs"


@pytest.fixture
def leftover(jobs_dir):
    """Saves a job record as an earlier run of the service left it."""

    def save(**fields) -> Job:
        job = Job(document_id="doc-1", request_id="r", **fields)
        JobStore(jobs_dir).save(job)
        return job

    return save


@pytest.fixture
def restart(make_api):
    """Starts the API on the saved data and waits until startup's work is done."""

    async def start(**changes) -> httpx.AsyncClient:
        api = await make_api(**changes)
        await make_api.settle()
        return api

    return start


@pytest.fixture
def read_job(auth_headers):
    async def read(api: httpx.AsyncClient, job_id: str) -> dict:
        response = await api.get(f"/jobs/{job_id}", headers=auth_headers)
        assert response.status_code == 200
        return response.json()

    return read


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_interrupted_job_fails_with_error_written_to_source(leftover, restart, read_job, source_app, state):
    job = leftover(state=state)
    finished = await read_job(await restart(), job.id)
    assert (finished["state"], finished["error"], finished["statusReported"]) == ("failed", INTERRUPTED, True)
    stored = source_app.state.documents["doc-1"]
    assert (stored.status, stored.status_message) == ("Error", INTERRUPTED)


async def test_job_killed_after_delivering_ends_published(leftover, restart, read_job, source_app, events):
    job = leftover(state="running", source_deleted=True, vendor_receipt="receipt-1", vendor_attempts=1)
    finished = await read_job(await restart(), job.id)
    assert (finished["state"], finished["statusReported"]) == ("done", True)
    assert source_app.state.documents["doc-1"].status == "Published"
    assert events == [("source", "status", "doc-1")]  # step 6 only: nothing is delivered again


async def test_saved_outcome_gets_its_status_written(leftover, restart, read_job, source_app, events):
    job = leftover(state="running", outcome="Published")
    finished = await read_job(await restart(), job.id)
    assert (finished["state"], finished["statusReported"]) == ("done", True)
    assert source_app.state.documents["doc-1"].status == "Published"
    assert events == [("source", "status", "doc-1")]


async def test_status_write_failing_after_startup_sets_running(leftover, restart, read_job):
    job = leftover(state="queued", outcome="Error", outcome_message="Archiving failed: OSError")
    api = await restart(source=httpx.MockTransport(lambda request: httpx.Response(503)))
    finished = await read_job(api, job.id)
    assert (finished["state"], finished["error"]) == ("running", "Status not written yet: HTTP 503")


async def test_failed_job_is_left_alone(leftover, restart, jobs_dir, events):
    leftover(state="failed", error="Archiving failed: OSError", outcome="Error")
    before = (jobs_dir / "doc-1.json").read_text()
    await restart()
    assert events == []
    assert (jobs_dir / "doc-1.json").read_text() == before


def corrupt_files(jobs_dir) -> list:
    return sorted(jobs_dir.glob("doc-1.json.corrupt-*"))


@pytest.mark.parametrize("content", ["not json", '{"id": 5}'], ids=["not-json", "not-a-job"])
async def test_unreadable_job_file_is_set_aside_and_blocks_its_document(
    restart, jobs_dir, auth_headers, caplog, content
):
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "doc-1.json").write_text(content)
    api = await restart()  # startup continues
    assert not (jobs_dir / "doc-1.json").exists()
    assert [path.read_text() for path in corrupt_files(jobs_dir)] == [content]
    assert "doc-1.json set aside" in caplog.text
    blocked = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    assert (blocked.status_code, blocked.json()) == (409, {"detail": BLOCKED})
    other = await api.post("/publish", json={"documentId": "doc-2", "requestId": "r"}, headers=auth_headers)
    assert other.status_code == 202


async def test_block_survives_another_restart(restart, jobs_dir, auth_headers):
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "doc-1.json").write_text("not json")
    await restart()
    api = await restart()
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    assert response.status_code == 409


async def test_second_corruption_keeps_the_first_evidence(restart, jobs_dir):
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "doc-1.json").write_text("first")
    await restart()
    (jobs_dir / "doc-1.json").write_text("second")
    await restart()
    assert [path.read_text() for path in corrupt_files(jobs_dir)] == ["first", "second"]


async def test_leftover_temp_file_is_deleted_and_not_loaded(restart, jobs_dir, auth_headers):
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "doc-2.json.tmp").write_text(Job(document_id="doc-2", request_id="r").model_dump_json())
    api = await restart()
    assert not (jobs_dir / "doc-2.json.tmp").exists()
    assert (await api.get("/jobs", headers=auth_headers)).json() == []


async def test_step_6_only_run_keeps_its_state_while_it_runs(leftover, make_api, read_job):
    job = leftover(state="queued", outcome="Error", outcome_message="Archiving failed: OSError")
    reached = asyncio.Event()

    async def hold_status_write(request: httpx.Request) -> None:
        reached.set()  # a step-6-only run calls nothing else
        await asyncio.Event().wait()  # never answers

    api = await make_api(source=httpx.MockTransport(hold_status_write))
    async with asyncio.timeout(2):
        await reached.wait()
    assert (await read_job(api, job.id))["state"] == "queued"  # spec 3, rule 3: not switched to running
```

- **`leftover()`** saves a job record the way an earlier run of the service left it.
- **`restart()`** starts the API on that data and waits for startup's step-6 runs.
- **`test_step_6_only_run_keeps_its_state_while_it_runs`** holds the status write, and checks that the job is still `queued` meanwhile (spec 3, rule 3).
- **A Source that answers 503 to everything** is enough for the "status write fails" case. A step-6-only run makes just that one call.

At the end of `api/tests/test_pipeline.py`:

```python
async def test_shutdown_leaves_a_running_job_as_it_is(make_api, source_app, auth_headers, settings):
    reached = asyncio.Event()

    async def hold_status_write(request: httpx.Request) -> None:
        if request.url.path.endswith("/status"):
            reached.set()
            await asyncio.Event().wait()  # never answers

    api = await make_api(source=intercept(source_app, hold_status_write))
    await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    async with asyncio.timeout(2):
        await reached.wait()
    await make_api.shutdown()
    record = saved(settings)  # cancelled, not rescued: startup will finish it
    assert (record["state"], record["error"], record["outcome"]) == ("running", None, "Published")
```

At the end of `api/tests/test_idempotency.py`:

```python
async def test_duplicate_after_a_restart_gives_the_same_job(published, make_api, auth_headers, events):
    finished = await published("doc-1")
    restarted = await make_api()  # a new API process on the same saved jobs
    again = await restarted.post("/publish", json={"documentId": "doc-1", "requestId": "r2"}, headers=auth_headers)
    await make_api.settle()
    assert again.json()["id"] == finished["id"]
    assert events.count(("vendor", "post", finished["id"])) == 1  # not delivered again
```

- [ ] **Step 3: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `11 failed, 150 passed`. All new `test_startup.py` cases fail except `test_failed_job_is_left_alone`.

Three new tests already pass, because they pin down behaviour that exists today:
- **`test_failed_job_is_left_alone`:** nothing touches failed jobs yet.
- **`test_shutdown_leaves_a_running_job_as_it_is`:** the outer guard doesn't catch cancellation. If it caught `BaseException`, this test would fail.
- **`test_duplicate_after_a_restart_gives_the_same_job`:** the store already reloads saved jobs.

- [ ] **Step 4: The store.** Replace `api/app/store.py`:

```python
"""Jobs in memory, mirrored to one JSON file per document."""

import logging
from pathlib import Path

from .files import write_durably
from .models.jobs import Job, utc_now

log = logging.getLogger(__name__)


def _set_aside(path: Path) -> None:
    """Renames an unreadable job file; the timestamp keeps earlier ones."""
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    path.rename(path.with_name(f"{path.name}.corrupt-{stamp}"))
    log.error("Unreadable job file %s set aside; its document is blocked", path.name)


class JobStore:
    def __init__(self, folder: Path):
        # Get all jobs from storage
        folder.mkdir(parents=True, exist_ok=True)
        self._folder = folder
        for temp in folder.glob("*.json.tmp"):  # a save cut short; the .json beside it is intact
            temp.unlink()
        self._jobs: dict[str, Job] = {}
        for path in folder.glob("*.json"):
            try:
                job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError:  # not JSON, or not a job record
                _set_aside(path)
            else:
                self._jobs[job.document_id] = job
        # The document id is the file name before ".json.corrupt-…".
        self._blocked = {path.name.rsplit(".json", 1)[0] for path in folder.glob("*.json.corrupt-*")}

    def by_document(self, document_id: str) -> Job | None:
        return self._jobs.get(document_id)

    def is_blocked(self, document_id: str) -> bool:
        """True when the document's job record was unreadable; unblocked by removing the .corrupt file."""
        return document_id in self._blocked

    def get(self, job_id: str) -> Job | None:
        return next((job for job in self._jobs.values() if job.id == job_id), None)

    def newest_first(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def save(self, job: Job) -> None:
        job.updated_at = utc_now()
        write_durably(self._folder / f"{job.document_id}.json", job.model_dump_json(indent=2).encode(), ".tmp")
        self._jobs[job.document_id] = job
```

- **`.tmp` first:** a leftover `.tmp` is a save that was cut short, and the `.json` beside it is still intact. It's deleted before loading.
- **Unreadable files:** `ValueError` covers bad JSON, bad UTF-8 and a failed model check (Pydantic's `ValidationError` is a `ValueError`).
- **Evidence is never overwritten:** the microsecond timestamp gives every set-aside file its own name.

- [ ] **Step 5: The runner.** Replace `api/app/runner.py`:

```python
"""Runs each job as an asyncio task, a limited number at a time."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from .archive import Archive, ArchiveError
from .backoff import RetryPolicy
from .clients import SourceClient, UpstreamError, VendorClient
from .config import Settings
from .models.jobs import Job, JobState
from .store import JobStore

MAX_MESSAGE = 500
DAMAGED_AFTER_DELETE = "Archive copy damaged or missing after the original was deleted; manual action needed"
NOT_IN_SOURCE = "Document not found in Source; the status cannot be written."
STATUS_PENDING = "Status not written yet: "
INTERRUPTED = "Interrupted by a service restart"

log = logging.getLogger(__name__)


class StepFailed(Exception):
    pass


class JobRecordUnreadable(Exception):
    """The document's job file could not be read at startup; publishing it waits for manual action."""


def _reason(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__)[:MAX_MESSAGE]


def _final_state(job: Job) -> tuple[JobState, str | None]:
    """The state and error that match a written outcome."""
    return ("done", None) if job.outcome == "Published" else ("failed", job.outcome_message)


@contextmanager
def _step(name: str) -> Iterator[None]:
    """Names the failed step in the error message."""
    try:
        yield
    except Exception as exc:
        raise StepFailed(f"{name} failed: {_reason(exc)}") from exc


class Runner:
    """Job runner"""

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

        if self._store.is_blocked(document_id):
            raise JobRecordUnreadable(document_id)
        # Find any job now that has the same document id
        job = self._store.by_document(document_id)
        if job is None:
            job = Job(document_id=document_id, request_id=request_id)
            self._store.save(job)
            self._submit(job)
        return job

    def recover(self) -> None:
        """Startup (spec 4.6): interrupted jobs get an outcome; every unwritten status is written."""
        for job in self._store.newest_first():
            if job.state not in ("queued", "running"):
                continue  # done and failed jobs are left to retry
            if job.outcome is None:
                # A receipt means it was delivered before the kill.
                job.outcome, job.outcome_message = ("Published", None) if job.vendor_receipt else ("Error", INTERRUPTED)
                self._store.save(job)
            self._submit(job)

    def _submit(self, job: Job) -> None:
        self._tasks[job.id] = asyncio.create_task(self._run(job))

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
            # Outer guard: no unexpected error ends a run silently. Cancellation passes through.
            try:
                # A job with an outcome already only needs step 6, and keeps its state until then.
                if job.outcome is None:
                    job.state = "running"
                    self._store.save(job)
                    await self._decide_outcome(job)
                await self._report(job)
            except Exception as exc:
                log.exception("Job %s: unexpected error", job.id)
                self._rescue(job, exc)

    async def _decide_outcome(self, job: Job) -> None:
        """Runs steps 1-5; any failure becomes an Error outcome."""
        try:
            await self._steps(job)
        except Exception as exc:
            job.outcome, job.outcome_message = "Error", _reason(exc)
        else:
            job.outcome, job.outcome_message = "Published", None
        self._store.save(job)

    async def _report(self, job: Job) -> None:
        """Step 6: writes the outcome back to Source, then ends the run."""
        try:
            await self._source_retry.run(
                lambda: self._source.set_status(job.document_id, job.outcome, job.outcome_message)
            )
        except UpstreamError as exc:
            if exc.status == 404:  # nothing to write to: the job ends without a status (R4 exception)
                message = f"{NOT_IN_SOURCE} {job.outcome_message}" if job.outcome_message else NOT_IN_SOURCE
                self._finish(job, "failed", message)
            else:
                self._finish(job, "running", f"{STATUS_PENDING}{_reason(exc)}")
        else:
            self._finish(job, *_final_state(job), reported=True)

    def _rescue(self, job: Job, exc: Exception) -> None:
        """Leaves a job hit by an unexpected error in a safe, saved state."""
        reason = _reason(exc)
        try:
            if job.status_reported:
                self._finish(job, *_final_state(job), reported=True)
                return
            if job.outcome is None:
                job.outcome, job.outcome_message = "Error", reason
            self._finish(job, "running", f"{STATUS_PENDING}{reason}")
        except Exception:  # e.g. the disk still refuses; the job stays as it is in memory
            log.exception("Job %s: could not save after an unexpected error", job.id)

    def _finish(self, job: Job, state: JobState, error: str | None, reported: bool = False) -> None:
        """The run's last change: state, error and status_reported are always saved together."""
        job.state, job.error, job.status_reported = state, error, reported
        self._store.save(job)

    async def _steps(self, job: Job) -> None:
        """Steps to run a job"""
        document_id = job.document_id

        # Step 1: get document
        with _step("Reading the document from Source"):
            document = await self._source_retry.run(lambda: self._source.get_document(document_id))
            content = await self._source_retry.run(lambda: self._source.get_content(document_id))

        # Step 2: archive the document and update the job status
        with _step("Archiving"):
            job.archive_path, job.sha256 = await asyncio.to_thread(
                self._archive.store, document_id, document.filename, content
            )
        job.filename, job.content_type = document.filename, document.content_type
        self._store.save(job)

        # Step 3: Update the archive link in source api stub
        uri = f"archive://{job.archive_path}"
        with _step("Linking the archive copy in Source"):
            await self._source_retry.run(lambda: self._source.set_archive_link(document_id, uri, job.sha256))
        job.archive_linked = True
        self._store.save(job)

        # Step 4: Before deletion, verify the archive copy using hash, then delete the document in source api stub
        with _step("Verifying the archive copy"):
            if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                raise ArchiveError("archive copy is damaged or missing")
        with _step("Deleting the original in Source"):
            await self._source_retry.run(lambda: self._source.delete_document(document_id))
        job.source_deleted = True
        self._store.save(job)

        # Step 5: Deliver file to vendor stub
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

- **`recover()`:** gives an interrupted job its outcome, then submits every unfinished job. `done` and `failed` jobs are skipped.
- **`_run`:** steps 1–5 run only when there is no outcome yet. Otherwise only step 6 runs. Step 6 then decides the state: a refused write leaves the job `running`, even when it started from `queued` (spec 4.4).
- **`_submit()`:** the one place a job task is started, shared by `publish()` and `recover()`.

- [ ] **Step 6: The 409.** Replace `api/app/routers/jobs.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..auth import require_token
from ..dependencies import get_runner, get_store
from ..models.jobs import JobOut, PublishRequest
from ..runner import JobRecordUnreadable, Runner
from ..store import JobStore

router = APIRouter(dependencies=[Depends(require_token)])

# The body is read by hand, so OpenAPI needs to be told about it.
PUBLISH_BODY = {
    "required": True,
    "content": {"application/json": {"schema": PublishRequest.model_json_schema(by_alias=True)}},
}


@router.post("/publish", status_code=202, openapi_extra={"requestBody": PUBLISH_BODY})
async def publish(request: Request, runner: Annotated[Runner, Depends(get_runner)]) -> JobOut:
    # Read after the token check, so an anonymous broken body gets 401, not 422.
    try:
        order = PublishRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        raise RequestValidationError(errors) from None
    try:
        return runner.publish(order.document_id, order.request_id)
    except JobRecordUnreadable:
        raise HTTPException(409, "The job record for this document is unreadable; manual action needed") from None


@router.get("/jobs")
async def list_jobs(store: Annotated[JobStore, Depends(get_store)]) -> list[JobOut]:
    return store.newest_first()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, store: Annotated[JobStore, Depends(get_store)]) -> JobOut:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job
```

- [ ] **Step 7: Recover at startup.** In `api/app/main.py`, make `runner.recover()` the first line of `lifespan`, before `yield`:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        runner.recover()
        yield
        await runner.shutdown()
        for client in owned:
            await client.aclose()
```

- [ ] **Step 8: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `161 passed`, then `All checks passed!`.

Run it twice more with warnings as errors: `docker compose run --rm -T tests -q -W error`
Expected: `161 passed` both times.

Suggested commit: `feat(api): finish interrupted jobs at startup`

---

### Task 2: Live check and spec sync

- [ ] **Step 1: A real restart.** This plants an interrupted job and a broken job file, then restarts the API.
  - **Made-up ids:** the files use the ids `restart-check` and `corrupt-check`. Source doesn't have those documents, so no real job or document is touched.
  - **Not a seeded document:** don't use a seeded `doc-N` here. Every seeded document may already have a finished job, and planting a file would overwrite its record and its status in Source.

```bash
docker compose up -d --wait --build api
MSYS_NO_PATHCONV=1 docker compose exec -T api sh -c 'test ! -e /data/jobs/restart-check.json && test ! -e /data/jobs/corrupt-check.json && echo free'
```

Expected: `free`. If nothing prints, the ids are in use; pick others and change them in every command below.

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T api python -c "
from pathlib import Path
from app.models.jobs import Job
from app.store import JobStore
JobStore(Path('/data/jobs')).save(Job(document_id='restart-check', request_id='live-9', state='running'))
"
MSYS_NO_PATHCONV=1 docker compose exec -T api sh -c 'echo broken > /data/jobs/corrupt-check.json'
docker compose restart api && until curl -sf localhost:8000/health >/dev/null; do sleep 1; done
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
H="Authorization: Bearer $T"
sleep 1
curl -s localhost:8000/jobs -H "$H" | grep -oE '"documentId":"restart-check","state":"[a-z]+","error":"[^"]*"'
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/publish -H "$H" -H 'Content-Type: application/json' -d '{"documentId":"corrupt-check","requestId":"live-9"}'
MSYS_NO_PATHCONV=1 docker compose exec -T api sh -c 'ls /data/jobs | grep check'
docker compose logs api | grep -m1 'corrupt-check.json set aside'
```

Expected:
- `"documentId":"restart-check","state":"failed","error":"Document not found in Source; the status cannot be written. Interrupted by a service restart"`. Startup gave the interrupted job its outcome and tried step 6, and Source has no such document;
- `409` for `corrupt-check`;
- `corrupt-check.json.corrupt-…` and `restart-check.json` listed;
- the log line "Unreadable job file corrupt-check.json set aside; its document is blocked".

`docker compose restart` keeps the same container, so its log still holds the startup line. Running `docker compose up` right after would replace the container, and that log with it.

Clean up, so the check leaves nothing behind:

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T api sh -c 'rm /data/jobs/restart-check.json /data/jobs/corrupt-check.json.corrupt-*'
docker compose restart api && until curl -sf localhost:8000/health >/dev/null; do sleep 1; done
```

- [ ] **Step 2: Spec sync.** In `docs/superpowers/specs/2026-11-08-document-publishing-service.md`, in the section 12 row for feature 9, append to the last column:

"`ApiFactory.shutdown()` stops job tasks in tests. A restart is simulated by building a second API on the same data folder."

Suggested commit: `docs: sync spec with feature 9`

---

### Task 3: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with this feature's changed files and CLAUDE.md rule 4. Fix what it finds, re-run lint and tests, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the owner with the outputs of lint, tests and Task 2 Step 1, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address feature 9 review findings`
