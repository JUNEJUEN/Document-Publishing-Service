# Feature 8: Status Write-Back, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Order:** feature 6b (session cookie) is still on hold. Its plan is regenerated on top of the current code before it runs.

**Goal:** every run ends by writing its outcome back to Source (step 6), and the job's final state follows the spec's rules:
- **Outcome:** steps 1–5 end in an outcome, `Published` or `Error` with a message, which is saved before step 6.
- **Step 6:** `PUT /documents/{id}/status` with `{status, message}`, retried like any Source call.
- **Final state, set in one change** (`state`, `error` and `status_reported` together):

| Step 6 result | State | `error` |
|---|---|---|
| Written, `Published` | `done` | none |
| Written, `Error` | `failed` | the outcome message |
| Source answers 404 (no document to write to) | `failed` | "Document not found in Source; the status cannot be written." + the outcome message |
| Any other failure | `running` | "Status not written yet: {reason}" |

- **Outer guard:** an unexpected error anywhere in a run is logged, and the job is put into a state the next run can continue from. Cancellation still passes through, so shutdown works.

**Architecture:**
- **`models/jobs.py`:**
  - `Outcome = Literal["Published", "Error"]`;
  - `JobOut.status_reported`, shown by the API as `statusReported`;
  - `Job.outcome` and `Job.outcome_message`, kept internal.
- **`clients.py`:**
  - `UpstreamError` also carries the HTTP `status`, so step 6 can tell a 404 apart;
  - new `SourceClient.set_status()`.
- **`runner.py`:**
  - `_run` = outer guard around `_decide_outcome` (steps 1–5) then `_report` (step 6);
  - `_finish` is the one place that ends a run;
  - `_rescue` is the outer guard's handling.

**Interim behaviour, until later features:**
- **A job left `running` stays that way until the retry cron writes its status (feature 11).** That happens when step 6 was refused, or after an unexpected error. Its `error` says why ("Status not written yet: …").
- **No run starts at step 6 yet.** Spec 4.2 says a run with a saved outcome does only step 6. Only startup (feature 9) and the cron (feature 11) start such runs, so that branch comes with them.

**Tech stack:** FastAPI 0.115.6, httpx 0.28.1, Pydantic 2.14.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
- 1 (R4);
- 4.1 (`outcome`, `outcome_message`, `status_reported`), 4.2 (step 6), 4.3, 4.4, 4.5;
- 5.2;
- 7.1 (`statusReported`);
- 9 (`test_pipeline`: status write cases and the outer guard; `test_vendor_failure`: "Error in Source");
- 12, feature 8.

**Deferred (spec 12):**
- the cron writing a pending status again comes with feature 11;
- "404, then Retry works" comes with feature 10.

---

## File structure

| File | Change |
|---|---|
| `api/app/models/jobs.py` | `Outcome`; `status_reported`; `outcome`, `outcome_message` |
| `api/app/clients.py` | `UpstreamError.status`; `SourceClient.set_status()` |
| `api/app/runner.py` | Outcome, step 6, final states, outer guard |
| `api/tests/test_pipeline.py` | Step 6 in the call order; status write and guard tests; `flaky()` takes a path and a status |
| `api/tests/test_jobs.py`, `test_vendor_failure.py` | `statusReported`; `Error` written to Source |
| `docs/superpowers/specs/2026-11-08-document-publishing-service.md` | Sections 3, 12 |

Commands are written for Git Bash, run from the repository root. Test counts: 141 before this plan, 146 after.

---

### Task 1: Outcome, status write-back and final states

**Files:** Modify `api/tests/test_pipeline.py`, `api/tests/test_jobs.py`, `api/tests/test_vendor_failure.py`, `api/app/models/jobs.py`, `api/app/clients.py`, `api/app/runner.py`

- [ ] **Step 1: Write the failing tests.**

Replace `api/tests/test_pipeline.py`:

```python
import asyncio
import json

import httpx
import pytest

from app.archive import Archive
from app.clients import SourceClient
from app.runner import Runner
from app.store import JobStore


def intercept(source_app, before) -> httpx.MockTransport:
    """The Source stub, with `await before(request)` run before each call reaches it."""
    stub = httpx.ASGITransport(app=source_app)

    async def handler(request: httpx.Request) -> httpx.Response:
        await before(request)
        return await stub.handle_async_request(request)

    return httpx.MockTransport(handler)


async def test_steps_run_in_order(published, source_app, vendor_app, events):
    finished = await published("doc-1")
    assert finished["state"] == "done"
    assert events == [
        ("source", "get", "doc-1"),
        ("source", "get_content", "doc-1"),
        ("source", "archive_link", "doc-1"),
        ("source", "delete", "doc-1"),
        ("vendor", "post", finished["id"]),
        ("source", "status", "doc-1"),
    ]
    stored = source_app.state.documents["doc-1"]
    assert (stored.content, stored.status, stored.status_message) == (None, "Published", None)
    assert stored.archive_uri == f"archive://{finished['archivePath']}"
    # The Vendor got exactly the archive copy: its path holds that copy's sha256.
    assert vendor_app.state.deliveries[finished["id"]].sha256 == finished["archivePath"].split("/")[1]


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
    make_api, source_app, events, settings, published_on
):
    async def damage_archive(request: httpx.Request) -> None:
        if request.url.path.endswith("/archive-link"):
            for path in (settings.data_dir / "archive").rglob("doc-1.txt"):
                path.write_bytes(b"damaged")

    api = await make_api(source=intercept(source_app, damage_archive))
    finished = await published_on(api)
    assert finished["state"] == "failed"
    assert finished["error"] == "Verifying the archive copy failed: archive copy is damaged or missing"
    assert ("source", "delete", "doc-1") not in events
    assert source_app.state.documents["doc-1"].content is not None


async def test_unknown_document_fails_and_no_status_can_be_written(published, events):
    finished = await published("doc-99")
    assert (finished["state"], finished["statusReported"]) == ("failed", False)
    assert finished["error"] == (
        "Document not found in Source; the status cannot be written. "
        "Reading the document from Source failed: HTTP 404"
    )
    assert events == [("source", "get", "doc-99"), ("source", "status", "doc-99")]


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


def flaky(source_app, failures: int, path: str = "/documents/doc-1", status: int = 503) -> httpx.MockTransport:
    """The Source stub, but the first `failures` calls to `path` answer `status`."""
    stub = httpx.ASGITransport(app=source_app)
    failed = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == path and len(failed) < failures:
            failed.append(request)
            return httpx.Response(status)
        return await stub.handle_async_request(request)

    return httpx.MockTransport(handler)


async def test_temporary_source_failures_are_retried(make_api, source_app, published_on):
    api = await make_api(source=flaky(source_app, failures=2))
    assert (await published_on(api))["state"] == "done"


async def test_source_failing_every_attempt_fails_the_job(make_api, source_app, published_on, events):
    # Two attempts, set apart from the vendor's: proves the setting reaches the Source policy.
    api = await make_api(source=flaky(source_app, failures=2), source_max_attempts=2)
    finished = await published_on(api)
    assert finished["error"] == "Reading the document from Source failed: HTTP 503"
    assert events == [("source", "status", "doc-1")]  # the 503s never reached the stub


async def test_damaged_archive_copy_after_the_delete_is_never_delivered(
    make_api, source_app, settings, published_on, events
):
    async def damage_archive(request: httpx.Request) -> None:
        if request.method == "DELETE":
            for path in (settings.data_dir / "archive").rglob("doc-1.txt"):
                path.write_bytes(b"damaged")

    api = await make_api(source=intercept(source_app, damage_archive))
    finished = await published_on(api)
    assert finished["state"] == "failed"
    assert finished["error"] == (
        "Archive copy damaged or missing after the original was deleted; manual action needed"
    )
    assert not [event for event in events if event[0] == "vendor"]


def saved(settings, document_id: str = "doc-1") -> dict:
    """The job record as saved on disk."""
    return json.loads((settings.data_dir / "jobs" / f"{document_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("status", [503, 409])
async def test_refused_status_write_leaves_the_job_running(make_api, source_app, published_on, status):
    refusing = flaky(source_app, failures=99, path="/documents/doc-1/status", status=status)
    finished = await published_on(await make_api(source=refusing))
    assert (finished["state"], finished["statusReported"]) == ("running", False)
    assert finished["error"] == f"Status not written yet: HTTP {status}"


async def test_unexpected_error_in_the_status_write_leaves_the_job_running(published, monkeypatch, caplog):
    async def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(SourceClient, "set_status", broken)
    finished = await published("doc-1")
    assert (finished["state"], finished["error"]) == ("running", "Status not written yet: boom")
    assert f"Job {finished['id']}: unexpected error" in caplog.text  # logged, not silent


async def test_unexpected_error_before_the_outcome_gives_error(published, monkeypatch, settings):
    async def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(Runner, "_decide_outcome", broken)
    finished = await published("doc-1")
    assert (finished["state"], finished["error"]) == ("running", "Status not written yet: boom")
    record = saved(settings)
    assert (record["outcome"], record["outcome_message"]) == ("Error", "boom")


async def test_failure_after_the_status_was_written_still_ends_the_job(published, monkeypatch, settings):
    save, failed = JobStore.save, []

    def save_failing_once_after_the_status(store, job):
        if job.status_reported and not failed:
            failed.append(job)
            raise OSError("disk full")
        save(store, job)

    monkeypatch.setattr(JobStore, "save", save_failing_once_after_the_status)
    await published("doc-1")
    assert failed  # the first final save did fail
    record = saved(settings)
    assert (record["state"], record["status_reported"]) == ("done", True)
```

What's new:
- **The call order** ends with the status write, and Source shows `Published`.
- **An unknown document** now also tries the status write, which gets a 404, and ends with the "not found" message.
- **`flaky()`** takes the path to fail and the status to answer, so the same helper can refuse the status write.
- **`saved()`** reads the job file from disk. That's where the guard tests look, because a guard that didn't run would leave the file behind the in-memory job.
- **Four new tests:**
  - a refused status write (503 or 409) leaves the job `running`;
  - an unexpected error in step 6 leaves it `running`;
  - an unexpected error before the outcome gives `Error`;
  - a failed save after the status was written still ends the job `done` on disk.

Replace `api/tests/test_jobs.py`:

```python
import hashlib


async def test_publish_answers_202_with_the_job_object(publish):
    response = await publish("doc-1", "req-1")
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "id", "requestId", "documentId", "state", "error", "createdAt", "updatedAt", "archivePath",
        "vendorAttempts", "vendorReceipt", "statusReported",
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
    assert finished["statusReported"] is True
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

Replace `api/tests/test_vendor_failure.py` (the "Error in Source" check deferred from feature 7):

```python
"""The brief's vendor-failure rule: the Vendor fails often, and each document is delivered once."""
import pytest


@pytest.mark.parametrize("vendor_outcomes", [["fail", "fail_after_store", "ok"]])
async def test_delivers_once_after_failures(published, vendor_app):
    finished = await published("doc-1")
    assert (finished["state"], finished["vendorAttempts"]) == ("done", 3)
    assert list(vendor_app.state.deliveries) == [finished["id"]]  # one delivery, keyed by the job id
    assert finished["vendorReceipt"] == vendor_app.state.deliveries[finished["id"]].receipt_id


@pytest.mark.parametrize("vendor_outcomes", [["fail_after_store", "fail_after_store"]])
async def test_lost_answers_do_not_deliver_twice(published, vendor_app):
    finished = await published("doc-1")
    assert (finished["state"], finished["vendorAttempts"]) == ("done", 3)
    assert len(vendor_app.state.deliveries) == 1


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_gives_up_after_three_failures_and_keeps_the_archive_copy(published, settings, source_app, vendor_app):
    finished = await published("doc-1")
    assert finished["state"] == "failed"
    assert finished["error"] == "Delivering to the Vendor failed: HTTP 503"
    assert (finished["vendorAttempts"], finished["vendorReceipt"]) == (3, None)
    assert vendor_app.state.deliveries == {}
    stored = source_app.state.documents["doc-1"]
    assert stored.content is None  # deleted before delivery
    assert (stored.status, stored.status_message) == ("Error", "Delivering to the Vendor failed: HTTP 503")
    assert (settings.data_dir / "archive" / finished["archivePath"]).exists()


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_vendor_attempts_follow_their_setting(make_api, published_on):
    api = await make_api(vendor_max_attempts=2)
    finished = await published_on(api)
    assert (finished["state"], finished["vendorAttempts"]) == ("failed", 2)
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `11 failed, 135 passed`. Nothing writes a status yet: the new events, messages and the `statusReported` field are all missing.

- [ ] **Step 3: Models.** Replace `api/app/models/jobs.py`:

```python
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import ConfigDict, Field

from .base import ApiModel

JobState = Literal["queued", "running", "done", "failed"]
Outcome = Literal["Published", "Error"]  # the status written back to Source


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobOut(ApiModel):
    """A job as the API shows it."""

    # Lets Job load its snake_case files from disk.
    model_config = ConfigDict(validate_by_name=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    request_id: str
    document_id: str
    state: JobState = "queued"
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    archive_path: str | None = None
    vendor_receipt: str | None = None  # the Vendor's receipt id, proof of delivery; None until the Vendor confirms
    vendor_attempts: int = 0  # a counter, never reset
    status_reported: bool = False  # the outcome is written to Source


class Job(JobOut):
    """The job record, saved as /data/jobs/{document_id}.json: the API fields plus internal checkpoints."""

    filename: str | None = None
    content_type: str | None = None
    sha256: str | None = None
    archive_linked: bool = False
    source_deleted: bool = False
    outcome: Outcome | None = None  # set once steps 1-5 end
    outcome_message: str | None = None  # why an Error; at most 500 characters


class PublishRequest(ApiModel):
    # It becomes a folder name, so it can't be ".." or contain slashes.
    document_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")]
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
```

- `status_reported` is on `JobOut`, because the API shows it (spec 7.1).
- `outcome` and `outcome_message` are on `Job` only, because they are internal.

- [ ] **Step 4: Clients.** Replace `api/app/clients.py`:

```python
"""HTTP clients for the external systems."""
import httpx
from pydantic import TypeAdapter, ValidationError

from .models.documents import Document
from .models.jobs import Outcome
from .models.vendor import Receipt

_DOCUMENT = TypeAdapter(Document)
_DOCUMENT_LIST = TypeAdapter(list[Document])
_RECEIPT = TypeAdapter(Receipt)
# Transport failures that may pass on a repeat; others (e.g. a bad URL) never will.
_TEMPORARY_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


class UpstreamError(Exception):
    """A call to Source or Vendor failed; the message says how.

    `temporary` failures (network, timeout, 429, 5xx) may succeed when repeated.
    `status` is the HTTP status of a refused call, otherwise None.
    """

    def __init__(self, message: str, *, temporary: bool = False, status: int | None = None):
        super().__init__(message)
        self.temporary = temporary
        self.status = status


async def _send(http: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError(type(exc).__name__, temporary=isinstance(exc, _TEMPORARY_TRANSPORT_ERRORS)) from exc
    if not response.is_success:
        temporary = response.status_code == 429 or response.is_server_error
        raise UpstreamError(f"HTTP {response.status_code}", temporary=temporary, status=response.status_code)
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

    async def set_status(self, document_id: str, status: Outcome, message: str | None) -> None:
        body = {"status": status, "message": message}
        await _send(self._http, "PUT", f"/documents/{document_id}/status", json=body)


class VendorClient:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def deliver(self, key: str, filename: str, content_type: str, content: bytes) -> str:
        """Uploads the file and returns the receipt id; the same key never stores it twice."""
        files = {"file": (filename, content, content_type)}
        response = await _send(self._http, "POST", "/files", files=files, headers={"Idempotency-Key": key})
        return _parse(response, _RECEIPT, "a receipt").receipt_id
```

- **`status`** is set only when Source or the Vendor answered with an error code. Transport errors leave it `None`.
- **`set_status()`** sends `{status, message}`, the body the Source stub expects.

- [ ] **Step 5: Runner.** Replace `api/app/runner.py`:

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

log = logging.getLogger(__name__)


class StepFailed(Exception):
    pass


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

        # Find any job now that has the same document id
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
            # Outer guard: no unexpected error ends a run silently. Cancellation passes through.
            try:
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
        """Puts a job hit by an unexpected error into a state the next run can continue from."""
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

How it follows the spec:
- **`_decide_outcome` (4.3):** any exception from steps 1–5 becomes `Error` with its message (at most 500 characters). Success becomes `Published`. The outcome is saved before step 6.
- **`_report` (4.4):** writes the status through the Source retry policy, then ends the run according to the table above.
- **`_finish`:** the one place that ends a run. `state`, `error` and `status_reported` change together and are saved once.
- **`_rescue`, the outer guard (4.4):**
  - **`status_reported` is already true:** the state follows the outcome, so the job can't stay `running`.
  - **No outcome yet:** it becomes `Error`.
  - **In every other case:** the job waits like a refused status write.
- **Logging:** `log.exception` records the unexpected error with its traceback, and a test checks it. If the guard's own save fails too (the disk still refuses), that error is logged as well. The job then stays as it is in memory.
- **Cancellation:** `except Exception` doesn't catch it, so shutdown still works. Its test comes with feature 9's shutdown tests.
- **Step comments:** the owner's step comments are renumbered to match the spec, keeping their wording: step 4 is "verify, then delete", step 5 is delivery, and step 6 is this status write. The typo "arivhive" is fixed.
- **Not changed:** passing `job.outcome` (`Outcome | None`) where `Outcome` is expected. It is always set by then, and the project has no Python type checker (the same choice as in feature 7).

- [ ] **Step 6: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `146 passed`, then `All checks passed!`.

Run it twice more with warnings as errors: `docker compose run --rm -T tests -q -W error`
Expected: `146 passed` both times.

Suggested commit: `feat(api): write the outcome back to Source`

---

### Task 2: Live check and spec sync

- [ ] **Step 1: Check the running stack.** Pick a document no job has used yet (check `GET /jobs`), here `doc-20`.

```bash
docker compose up -d --wait --build api
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
H="Authorization: Bearer $T"
for D in doc-20 doc-99; do
  J=$(curl -s -X POST localhost:8000/publish -H "$H" -H 'Content-Type: application/json' -d "{\"documentId\":\"$D\",\"requestId\":\"live-8\"}" | grep -o '"id":"[^"]*"' | cut -d'"' -f4)
  for i in $(seq 40); do curl -s localhost:8000/jobs/$J -H "$H" | grep -qE '"state":"(done|failed)"' && break; sleep 0.5; done
  curl -s localhost:8000/jobs/$J -H "$H" | grep -oE '"state":"[a-z]+"|"error":(null|"[^"]*")|"statusReported":(true|false)' | paste -sd' '
done
curl -s localhost:8001/documents/doc-20 | grep -oE '"status":"[A-Za-z]+"'
```

Expected:
- `doc-20`:
  - `"state":"done" "error":null "statusReported":true`, then Source's `"status":"Published"`;
  - with the Vendor's 30% failure rate, it can instead end `failed`, with Source showing `"status":"Error"`.
- `doc-99`: `"state":"failed"`, the error "Document not found in Source; the status cannot be written. Reading the document from Source failed: HTTP 404", and `"statusReported":false`.

- [ ] **Step 2: Browser check (owner).** At `http://localhost:8080`, publish a document. When its job is Done, the documents table shows its Source status as "Published", and as deleted.

- [ ] **Step 3: Spec sync.** In `docs/superpowers/specs/2026-11-08-document-publishing-service.md`:
  - **3, units table:** `clients` "Uses": `—` becomes `models`.
  - **12, feature 8 row, last column:** append "A run that starts from a saved outcome (step 6 only, 4.2) comes with features 9 and 11, which start such runs."

Suggested commit: `docs: sync spec with feature 8`

---

### Task 3: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with this feature's changed files and CLAUDE.md rule 4. Fix what it finds, re-run lint and tests, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the owner with the outputs of lint, tests and Task 2 Step 1, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address feature 8 review findings`
