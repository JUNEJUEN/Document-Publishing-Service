# Feature 10: Retry Button, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Order:** feature 6b (session cookie) is still on hold. Its plan is regenerated on top of the current code before it runs.

**Goal:** a failed job can be run again, by a button in the job list or `POST /jobs/{id}/retry`. The run continues from its saved checkpoints.

**`retry(job)` (spec 5.3):**
- **Acts only on a `failed` job whose last run has finished.** Otherwise the API answers 409 "only failed jobs can be retried".
- **Clears the old result:** `outcome`, `outcome_message`, `status_reported` and `error`. The checkpoints are kept.
- **Re-queues it:** sets `queued`, saves, submits.
- **No `await` inside,** so a retry and another request can never interleave on one job (spec 3, rule 1).

**Where a run starts (spec 4.2):**
- **Steps 1–2** run only when `archive_path` is not set.
- **Steps 3, 4 and 5** each run only when their own checkpoint (`archive_linked`, `source_deleted`, `vendor_receipt`) is not set.
- **Archive copy saved, original not deleted yet:** the run first checks the copy's sha256.
  - **Intact:** go on.
  - **Damaged or missing:** read Source's metadata:
    - **original still there:** clear the archive checkpoints and start again at step 1;
    - **original already deleted** (a delete whose answer was lost, spec 5.4): keep the checkpoints as evidence, and fail with "Archive copy damaged or missing after the original was deleted; manual action needed";
    - **the read fails:** keep the checkpoints, and fail with that error.

**Web:** a Retry button on every failed job in the job list. The list refreshes right after a retry, and an error (e.g. the 409) is shown above the list.

**Deferred tests that are due now:**
- feature 4: `/jobs/{id}/retry` without a token gets 401;
- feature 8: an unknown document fails, then Retry works once Source has it;
- feature 9: a job interrupted after step 4 delivers the archive copy on retry; a job interrupted during step 5 is delivered once on retry.

**Interim, until feature 11:**
- **Ignoring a submit for a job that already has a running task** (spec 3, rule 2). Only the cron can make such a submit, so the `_submit` guard comes with it. `retry()` already refuses a job with an unfinished task.
- **Automatic retries:** the hourly retry cron, `retry(job, automatic=True)`, `auto_retries` and `nextRetryAt` come with feature 11. A manual retry never counts toward `AUTO_RETRY_MAX`.
- **"Manual retry and a cron run together: 1 run"** is written then.

**Tech stack:** FastAPI 0.115.6, Pydantic 2.14, httpx 0.28.1, Vue 3 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
- 3 (rules 1–2);
- 4.2 ("where a run starts", "checking an existing archive copy");
- 5.3 (manual retry), 5.4;
- 7.2 (`POST /jobs/{id}/retry`);
- 8 (the Retry button);
- 9 (`test_retry` manual cases; deferred `test_auth`, `test_pipeline`, `test_startup` cases);
- 12, feature 10.

---

## File structure

| File | Change |
|---|---|
| `api/app/runner.py` | `retry()`, `NotRetryable`, `_has_task()`; `_steps` skips by checkpoint; `_check_archive_copy()` |
| `api/app/routers/jobs.py` | `POST /jobs/{id}/retry` |
| `api/tests/conftest.py` | `ApiFactory.runner` |
| `api/tests/test_retry.py` | New |
| `api/tests/test_startup.py` | The two "delivered on retry" tests |
| `api/tests/test_auth.py` | The retry endpoint needs a token |
| `api/tests/test_pipeline.py` | The unknown-document test goes on to retry once Source has it |
| `web/src/api.ts`, `web/src/jobs.ts`, `web/src/components/JobList.vue` | `retryJob`, `retryFailedJob`, the Retry button |
| `docs/superpowers/specs/2026-11-08-document-publishing-service.md` | Section 12 |

Commands are written for Git Bash, run from the repository root. Test counts: 166 before this plan, 177 after.

---

### Task 1: Retry in the API

**Files:**
- Modify: `api/tests/conftest.py`, `api/tests/test_auth.py`, `api/tests/test_startup.py`
- Create: `api/tests/test_retry.py`
- Modify: `api/app/runner.py`, `api/app/routers/jobs.py`

- [ ] **Step 1: Test helper.** In `api/tests/conftest.py`, add `from app.runner import Runner` after the `create_app` import, and add to `ApiFactory`, before `shutdown()`:

```python
    @property
    def runner(self) -> Runner:
        """The runner of the API built last."""
        return self._apps[-1].state.runner
```

Only one test uses it: it injects a fake unfinished task, which spec 9 asks for.

- [ ] **Step 2: Write the tests.**

`api/tests/test_retry.py`:

```python
"""Manual retries (spec 5.3, 5.4): a failed job runs again from its saved checkpoints."""
import asyncio
import json

import httpx
import pytest

DAMAGED_AFTER_DELETE = "Archive copy damaged or missing after the original was deleted; manual action needed"
METADATA = ("GET", "/documents/doc-1")
LINK = ("PUT", "/documents/doc-1/archive-link")
DELETE = ("DELETE", "/documents/doc-1")


class FlakySource(httpx.AsyncBaseTransport):
    """The Source stub, with switches that refuse calls or lose their answers."""

    def __init__(self, source_app):
        self._stub = httpx.ASGITransport(app=source_app)
        self.refused: set[tuple[str, str]] = set()  # answered 503 without reaching the stub
        self.answers_lost: set[tuple[str, str]] = set()  # done by the stub, then answered 503

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        call = (request.method, request.url.path)
        if call in self.refused:
            return httpx.Response(503)
        response = await self._stub.handle_async_request(request)
        return httpx.Response(503) if call in self.answers_lost else response


@pytest.fixture
def source(source_app) -> FlakySource:
    return FlakySource(source_app)


@pytest.fixture
async def api(make_api, source):
    """Overrides the shared `api`, so `published` runs against the switchable Source."""
    return await make_api(source=source)


@pytest.fixture
def retried(api, auth_headers, settle):
    """Retries a job, waits for the run, and returns the job object."""

    async def retry(job_id: str) -> dict:
        response = await api.post(f"/jobs/{job_id}/retry", headers=auth_headers)
        assert (response.status_code, response.json()["state"]) == (202, "queued")
        await settle()
        return (await api.get(f"/jobs/{job_id}", headers=auth_headers)).json()

    return retry


def damage_archive(settings, job: dict) -> None:
    (settings.data_dir / "archive" / job["archivePath"]).write_bytes(b"damaged")


def saved(settings) -> dict:
    return json.loads((settings.data_dir / "jobs" / "doc-1.json").read_text())


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_retry_after_a_vendor_failure_skips_steps_1_to_4(published, retried, events, source_app, vendor_app):
    failed = await published("doc-1")
    assert failed["state"] == "failed"
    events.clear()
    finished = await retried(failed["id"])
    assert (finished["state"], finished["statusReported"]) == ("done", True)
    assert events == [("vendor", "post", failed["id"]), ("source", "status", "doc-1")]
    assert len(vendor_app.state.deliveries) == 1
    assert source_app.state.documents["doc-1"].status == "Published"


async def test_only_failed_jobs_can_be_retried(published, api, auth_headers):
    done = await published("doc-1")
    response = await api.post(f"/jobs/{done['id']}/retry", headers=auth_headers)
    assert (response.status_code, response.json()) == (409, {"detail": "only failed jobs can be retried"})


async def test_unknown_job_cannot_be_retried(api, auth_headers):
    response = await api.post("/jobs/unknown/retry", headers=auth_headers)
    assert (response.status_code, response.json()) == (404, {"detail": "job not found"})


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_retry_while_the_last_run_is_still_finishing_is_refused(
    published, api, auth_headers, make_api, settings
):
    failed = await published("doc-1")
    unfinished = asyncio.get_running_loop().create_future()  # stands in for a run that hasn't ended
    make_api.runner._tasks[failed["id"]] = unfinished
    try:
        response = await api.post(f"/jobs/{failed['id']}/retry", headers=auth_headers)
    finally:
        unfinished.cancel()
    assert (response.status_code, response.json()) == (409, {"detail": "only failed jobs can be retried"})
    assert saved(settings)["state"] == "failed"


async def test_lost_delete_answer_then_retry_delivers(published, retried, source, source_app, vendor_app, settings):
    source.answers_lost.add(DELETE)
    failed = await published("doc-1")
    assert failed["error"] == "Deleting the original in Source failed: HTTP 503"
    assert source_app.state.documents["doc-1"].content is None  # the delete did happen
    source.answers_lost.clear()
    finished = await retried(failed["id"])
    assert finished["state"] == "done"
    assert len(vendor_app.state.deliveries) == 1
    assert (settings.data_dir / "archive" / finished["archivePath"]).exists()


async def test_damaged_archive_after_a_lost_delete_answer_needs_manual_action(
    published, retried, source, vendor_app, settings
):
    source.answers_lost.add(DELETE)
    failed = await published("doc-1")
    source.answers_lost.clear()
    damage_archive(settings, failed)
    finished = await retried(failed["id"])
    assert (finished["state"], finished["error"]) == ("failed", DAMAGED_AFTER_DELETE)
    assert saved(settings)["archive_path"] == failed["archivePath"]  # checkpoints kept as evidence
    assert vendor_app.state.deliveries == {}


async def test_damaged_archive_and_a_failing_metadata_read_keep_the_checkpoints(
    published, retried, source, settings
):
    source.answers_lost.add(DELETE)
    failed = await published("doc-1")
    source.answers_lost.clear()
    damage_archive(settings, failed)
    source.refused.add(METADATA)
    finished = await retried(failed["id"])
    assert (finished["state"], finished["error"]) == ("failed", "Reading the document from Source failed: HTTP 503")
    assert saved(settings)["archive_path"] == failed["archivePath"]


async def test_damaged_archive_while_the_original_exists_is_made_again(
    published, retried, source, events, settings, source_app
):
    source.refused.add(LINK)
    failed = await published("doc-1")
    assert failed["error"] == "Linking the archive copy in Source failed: HTTP 503"
    damage_archive(settings, failed)
    source.refused.clear()
    events.clear()
    finished = await retried(failed["id"])
    assert finished["state"] == "done"
    assert events[:5] == [
        ("source", "get", "doc-1"),  # the check: is the original still there?
        ("source", "get", "doc-1"),  # then steps 1 to 4 again
        ("source", "get_content", "doc-1"),
        ("source", "archive_link", "doc-1"),
        ("source", "delete", "doc-1"),
    ]
    assert (settings.data_dir / "archive" / finished["archivePath"]).read_bytes() != b"damaged"
```

- **`FlakySource`:** wraps the Source stub. A test can switch on refused calls (a 503 without reaching the stub) or lost answers (the stub does the work, then the caller gets a 503). The second is how a delete whose answer was lost (spec 5.4) is staged.
- **The module's `api` fixture overrides the shared one,** so `published()` runs against the switchable Source.

In `api/tests/test_auth.py`, replace the `test_protected_endpoints_need_a_token` decorator with:

```python
@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/documents"), ("POST", "/publish"), ("GET", "/jobs"), ("GET", "/jobs/some-id"),
        ("POST", "/jobs/some-id/retry"),
    ],
)
```

In `api/tests/test_pipeline.py`, replace `test_unknown_document_fails_and_no_status_can_be_written` with the test below, and add `import source_api` directly above `from app.archive import Archive`, in the same group (`source_api` counts as first-party). This is spec 9's "unknown document ends failed with the 'cannot be written' message, and Retry works", deferred from feature 8:

```python
async def test_unknown_document_fails_and_retry_works_once_it_exists(
    published, events, api, auth_headers, settle, source_app
):
    finished = await published("doc-99")
    assert (finished["state"], finished["statusReported"]) == ("failed", False)
    assert finished["error"] == (
        "Document not found in Source; the status cannot be written. "
        "Reading the document from Source failed: HTTP 404"
    )
    assert events == [("source", "get", "doc-99"), ("source", "status", "doc-99")]
    source_app.state.documents["doc-99"] = source_api.StoredDocument(
        id="doc-99", filename="doc-99.txt", content_type="text/plain", content=b"arrived late"
    )
    assert (await api.post(f"/jobs/{finished['id']}/retry", headers=auth_headers)).status_code == 202
    await settle()
    assert (await api.get(f"/jobs/{finished['id']}", headers=auth_headers)).json()["state"] == "done"
```

Replace `api/tests/test_startup.py`. It adds the `delivered_elsewhere` and `retry_on` fixtures and the two "delivered on retry" tests at the end:

```python
"""Startup after a stop or crash (spec 4.6): every unfinished job gets a status written."""
import asyncio
from pathlib import Path

import httpx
import pytest

import vendor_api
from app.archive import Archive
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


@pytest.mark.parametrize("state, outcome", [("failed", "Error"), ("done", "Published")])
async def test_finished_job_is_left_alone(leftover, restart, jobs_dir, events, state, outcome):
    leftover(state=state, outcome=outcome, status_reported=True)
    before = (jobs_dir / "doc-1.json").read_text()
    await restart()
    assert events == []
    assert (jobs_dir / "doc-1.json").read_text() == before


def corrupt_files(jobs_dir) -> list:
    return sorted(jobs_dir.glob("doc-1.json.corrupt-*"))


OTHER_DOCUMENT = Job(document_id="doc-2", request_id="r").model_dump_json()


@pytest.mark.parametrize(
    "content", ["not json", '{"id": 5}', OTHER_DOCUMENT], ids=["not-json", "not-a-job", "other-document"]
)
async def test_unreadable_job_file_is_set_aside_and_blocks_its_document(
    restart, jobs_dir, auth_headers, caplog, content
):
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "doc-1.json").write_text(content)
    api = await restart()  # startup continues
    assert not (jobs_dir / "doc-1.json").exists()
    assert [path.read_text() for path in corrupt_files(jobs_dir)] == [content]
    assert f"doc-1.json set aside as {corrupt_files(jobs_dir)[0].name}" in caplog.text
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


async def test_leftover_temp_file_is_deleted_and_the_record_beside_it_loads(
    leftover, restart, read_job, jobs_dir
):
    job = leftover(state="done", outcome="Published", status_reported=True)
    (jobs_dir / "doc-1.json.tmp").write_text("a save cut short")
    api = await restart()
    assert not (jobs_dir / "doc-1.json.tmp").exists()
    assert (await read_job(api, job.id))["state"] == "done"
    assert corrupt_files(jobs_dir) == []


async def test_blocked_document_with_a_readable_record_stays_blocked(
    leftover, restart, read_job, jobs_dir, auth_headers
):
    job = leftover(state="done", outcome="Published", status_reported=True)
    (jobs_dir / "doc-1.json.corrupt-20261108T000000000000Z").write_text("older evidence")
    api = await restart()
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    assert response.status_code == 409
    assert (await read_job(api, job.id))["state"] == "done"


async def test_id_that_looks_like_a_set_aside_file_is_not_blocked(restart, jobs_dir, auth_headers):
    document_id = "a.json.corrupt-x"  # a valid id whose record name ends like a set-aside file
    JobStore(jobs_dir).save(Job(document_id=document_id, request_id="r", state="done", outcome="Published"))
    api = await restart()
    response = await api.post("/publish", json={"documentId": document_id, "requestId": "r"}, headers=auth_headers)
    assert response.status_code == 202


async def test_file_that_cannot_be_set_aside_still_blocks_and_startup_continues(
    restart, jobs_dir, auth_headers, caplog, monkeypatch
):
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "doc-1.json").write_text("not json")

    def refuse(self, target):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(Path, "rename", refuse)
    api = await restart()
    response = await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)
    assert response.status_code == 409
    assert "could not set aside doc-1.json" in caplog.text


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


@pytest.fixture
def delivered_elsewhere(settings, source_app):
    """An archive copy whose original is already deleted in Source, as left after step 4."""
    content = source_app.state.documents["doc-1"].content
    archive_path, sha256 = Archive(settings.data_dir / "archive").store("doc-1", "doc-1.txt", content)
    source_app.state.documents["doc-1"].content = None
    return {
        "filename": "doc-1.txt", "content_type": "text/plain", "archive_path": archive_path, "sha256": sha256,
        "archive_linked": True, "source_deleted": True,
    }


@pytest.fixture
def retry_on(make_api, auth_headers, read_job):
    async def retry(api: httpx.AsyncClient, job_id: str) -> dict:
        assert (await api.post(f"/jobs/{job_id}/retry", headers=auth_headers)).status_code == 202
        await make_api.settle()
        return await read_job(api, job_id)

    return retry


async def test_job_interrupted_after_step_4_delivers_the_archive_copy_on_retry(
    leftover, restart, read_job, retry_on, delivered_elsewhere, vendor_app
):
    job = leftover(state="running", **delivered_elsewhere)
    api = await restart()
    assert (await read_job(api, job.id))["state"] == "failed"  # Error: interrupted
    finished = await retry_on(api, job.id)
    assert finished["state"] == "done"
    assert vendor_app.state.deliveries[job.id].sha256 == delivered_elsewhere["sha256"]


async def test_job_interrupted_during_step_5_is_delivered_once_on_retry(
    leftover, restart, retry_on, delivered_elsewhere, vendor_app
):
    job = leftover(state="running", vendor_attempts=1, **delivered_elsewhere)
    # The vendor stored the file, but the answer was lost when the service stopped.
    vendor_app.state.deliveries[job.id] = vendor_api.Delivery(
        receipt_id="first", idempotency_key=job.id, filename="doc-1.txt", sha256=delivered_elsewhere["sha256"],
        size=1,  # never compared
    )
    finished = await retry_on(await restart(), job.id)
    assert (finished["state"], finished["vendorReceipt"]) == ("done", "first")
    assert list(vendor_app.state.deliveries) == [job.id]
```

- [ ] **Step 3: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `12 failed, 165 passed`. The retry endpoint doesn't exist yet, so every new case gets 404 or 405.

- [ ] **Step 4: The runner.** Replace `api/app/runner.py`:

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


class NotRetryable(Exception):
    """Only a failed job whose last run has finished can be retried."""


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
                continue  # finished jobs: done never runs again; failed waits for a retry
            if job.outcome is None:
                # A receipt means it was delivered before the kill.
                job.outcome, job.outcome_message = ("Published", None) if job.vendor_receipt else ("Error", INTERRUPTED)
                self._store.save(job)
            self._submit(job)

    def retry(self, job: Job) -> None:
        """Runs a failed job again from its checkpoints (spec 5.3); no await, so it can't interleave."""
        if job.state != "failed" or self._has_task(job):
            raise NotRetryable(job.id)
        job.outcome = job.outcome_message = job.error = None
        job.status_reported = False
        job.state = "queued"
        self._store.save(job)
        self._submit(job)

    def _has_task(self, job: Job) -> bool:
        task = self._tasks.get(job.id)
        return task is not None and not task.done()

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
        # A retry skips each step whose checkpoint is saved (spec 4.2).
        document_id = job.document_id
        if job.archive_path is not None and not job.source_deleted:
            await self._check_archive_copy(job)

        # Step 1: get document
        if job.archive_path is None:  # steps 1 and 2 share this checkpoint
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
        if not job.archive_linked:
            uri = f"archive://{job.archive_path}"
            with _step("Linking the archive copy in Source"):
                await self._source_retry.run(lambda: self._source.set_archive_link(document_id, uri, job.sha256))
            job.archive_linked = True
            self._store.save(job)

        # Step 4: Before deletion, verify the archive copy using hash, then delete the document in source api stub
        if not job.source_deleted:
            with _step("Verifying the archive copy"):
                if not await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
                    raise ArchiveError("archive copy is damaged or missing")
            with _step("Deleting the original in Source"):
                await self._source_retry.run(lambda: self._source.delete_document(document_id))
            job.source_deleted = True
            self._store.save(job)

        # Step 5: Deliver file to vendor stub
        if job.vendor_receipt is None:
            await self._deliver(job)

    async def _check_archive_copy(self, job: Job) -> None:
        """Before the delete: a damaged archive copy is made again, if Source still has the original."""
        if await asyncio.to_thread(self._archive.matches, job.archive_path, job.sha256):
            return
        with _step("Reading the document from Source"):
            document = await self._source_retry.run(lambda: self._source.get_document(job.document_id))
        if document.deleted:  # a delete whose answer was lost (spec 5.4): keep the evidence
            raise ArchiveError(DAMAGED_AFTER_DELETE)
        job.archive_path = job.sha256 = job.filename = job.content_type = None
        job.archive_linked = False
        self._store.save(job)

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
- **`retry()`:** acts only on a `failed` job with no unfinished task (spec 3, rule 2). It clears the result fields, keeps the checkpoints, re-queues and submits. There's no `await` anywhere in it.
- **`_steps`:** each block runs only when its checkpoint is missing. The owner's docstring and step comments are kept; one comment line explains the skipping. Steps 1 and 2 stay one block because they share `archive_path`.
- **`_check_archive_copy()`:** runs only when the archive copy exists and the original is not deleted yet. If the copy is intact, nothing changes. If it isn't, Source's metadata decides: start again, or keep the evidence and fail.
- **After the delete:** step 5's own hash check (`_deliver`) guards the only copy, as before.

- [ ] **Step 5: The endpoint.** Replace `api/app/routers/jobs.py`. Both job endpoints now get the job through one dependency, `job_or_404`, instead of repeating the lookup:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..auth import require_token
from ..dependencies import get_runner, get_store
from ..models.jobs import Job, JobOut, PublishRequest
from ..runner import JobRecordUnreadable, NotRetryable, Runner
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


def job_or_404(job_id: str, store: Annotated[JobStore, Depends(get_store)]) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}")
async def get_job(job: Annotated[Job, Depends(job_or_404)]) -> JobOut:
    return job


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry_job(job: Annotated[Job, Depends(job_or_404)], runner: Annotated[Runner, Depends(get_runner)]) -> JobOut:
    try:
        runner.retry(job)
    except NotRetryable:
        raise HTTPException(409, "only failed jobs can be retried") from None
    return job
```

- [ ] **Step 6: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `177 passed`, then `All checks passed!`.

Run it twice more with warnings as errors: `docker compose run --rm -T tests -q -W error`
Expected: `177 passed` both times.

Suggested commit: `feat(api): retry failed jobs from their checkpoints`

---

### Task 2: Retry button

**Files:** Modify `web/src/api.ts`, `web/src/jobs.ts`, `web/src/components/JobList.vue`

- [ ] **Step 1: API call.** At the end of `web/src/api.ts`:

```ts
export async function retryJob(jobId: string): Promise<void> {
  await callApi(`/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' })
}
```

- [ ] **Step 2: Shared helper.** In `web/src/jobs.ts`, import `retryJob` from `./api`, and add at the end:

```ts
/** Runs a failed job again; refreshes the list at once, also when the retry is refused. */
export async function retryFailedJob(jobId: string): Promise<void> {
  try {
    await retryJob(jobId)
  } finally {
    await refreshJobs()
  }
}
```

A refused retry (e.g. a 409 because another tab already retried) refreshes the list too, so it doesn't stay stale.

- [ ] **Step 3: The button.** Replace `web/src/components/JobList.vue`:

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import type { Job, JobState } from '../api'
import { errorMessage } from '../http'
import { jobList, retryFailedJob, startPolling, stopPolling } from '../jobs'

const LABELS: Record<JobState, string> = { queued: 'Waiting', running: 'Running', done: 'Done', failed: 'Failed' }
const retrying = reactive(new Set<string>())
const retryError = ref('')

function details(job: Job): string {
  // Jobs finished before vendor delivery existed have no receipt.
  if (job.state === 'done') return job.vendorReceipt ? `Vendor receipt ${job.vendorReceipt}` : ''
  if (job.state === 'failed') return job.error ?? ''
  return job.error ?? `Vendor attempts so far: ${job.vendorAttempts}`
}

async function retry(jobId: string) {
  retrying.add(jobId)
  retryError.value = ''
  try {
    await retryFailedJob(jobId)
  } catch (e) {
    retryError.value = errorMessage(e)
  } finally {
    retrying.delete(jobId)
  }
}

onMounted(startPolling)
onUnmounted(stopPolling)
</script>

<template>
  <section class="card">
    <h2>Jobs</h2>
    <p v-if="jobList.error" class="error">{{ jobList.error }}</p>
    <p v-if="retryError" class="error">{{ retryError }}</p>
    <p v-if="!jobList.loaded" class="muted">Loading jobs…</p>
    <p v-else-if="jobList.jobs.length === 0 && !jobList.error" class="muted">No jobs yet</p>
    <div v-else-if="jobList.jobs.length" class="table-scroll">
      <table>
        <thead>
          <tr><th>Started</th><th>Document</th><th>State</th><th>Details</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="job in jobList.jobs" :key="job.id">
            <td>{{ new Date(job.createdAt).toLocaleString() }}</td>
            <td>{{ job.documentId }}</td>
            <td><span :class="['state', job.state]">{{ LABELS[job.state] }}</span></td>
            <td>{{ details(job) }}</td>
            <td>
              <button v-if="job.state === 'failed'" :disabled="retrying.has(job.id)" @click="retry(job.id)">
                {{ retrying.has(job.id) ? 'Retrying…' : 'Retry' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
```

- **Shown only on failed jobs,** and disabled while that job's retry request is on its way.
- **Errors:** an error, such as a 409 when the job has changed meanwhile, shows above the list. It follows the documents table's pattern.

- [ ] **Step 4: Build**

Run: `(cd web && npm run build)`
Expected: `vue-tsc` passes and Vite prints `✓ built`.

Suggested commit: `feat(web): retry button for failed jobs`

---

### Task 3: Live check and spec sync

- [ ] **Step 1: Retry a real failed job.** This retries a job that already failed, e.g. a random Vendor failure from "Publish all".

**If no job has failed,** make one first, in this order. Don't run `docker compose up` while the Vendor is stopped, because `up` would start it again as a dependency.
1. `docker compose stop vendor-stub`
2. Publish a document that has no job yet:
   `curl -s -X POST localhost:8000/publish -H "$H" -H 'Content-Type: application/json' -d '{"documentId":"<id>","requestId":"live-10"}'`
3. Wait until its job is `failed` (about 2 s).
4. `docker compose start vendor-stub`.
5. Run the script below again, from the `J=` line.

```bash
docker compose up -d --wait --build api web
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
H="Authorization: Bearer $T"
J=$(curl -s localhost:8000/jobs -H "$H" | grep -oE '"id":"[0-9a-f]+","requestId":"[^"]*","documentId":"[^"]*","state":"failed"' | head -1 | cut -d'"' -f4)
if [ -z "$J" ]; then echo "no failed job: make one first (see above)"; else
  echo "retrying $J"
  curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/jobs/$J/retry -H "$H"
  for i in $(seq 40); do curl -s localhost:8000/jobs/$J -H "$H" | grep -qE '"state":"(done|failed)"' && break; sleep 0.5; done
  curl -s localhost:8000/jobs/$J -H "$H" | grep -oE '"state":"[a-z]+"|"vendorAttempts":[0-9]+|"vendorReceipt":(null|"[^"]*")' | paste -sd' '
  curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/jobs/$J/retry -H "$H"
fi
```

Expected:
- `202`;
- the job ends `"state":"done"`, with a receipt. `vendorAttempts` keeps counting from before, since it's never reset. With the Vendor's random failures, it can fail again; the Retry button then works once more;
- `409` for a second retry of the now-`done` job.

- [ ] **Step 2: Browser check (owner).**
  1. A failed job shows a Retry button.
  2. Clicking it turns the job Waiting/Running, then Done, and the documents table reloads.

- [ ] **Step 3: Spec sync.** In `docs/superpowers/specs/2026-11-08-document-publishing-service.md`, section 12, feature 10 row, append to the last column: "`retry(job)` here is the manual path. The `automatic` flag and `auto_retries` come with the cron in feature 11."

Suggested commit: `docs: sync spec with feature 10`

---

### Task 4: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with this feature's changed files and CLAUDE.md rule 4. Fix what it finds, re-run lint, tests and the web build, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the owner with the outputs of lint, tests, the web build and Task 3 Step 1, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address feature 10 review findings`
