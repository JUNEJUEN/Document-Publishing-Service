# Retry for Jobs Owing Their Status (`canRetry`), Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Work on the current branch; create no branches or worktrees. Each task ends with a suggested commit message for the user instead.**

**Problem (final review):** when Source refuses the status write (step 6), for example because it's down, the job stays `running` with "Status not written yet: …". The hourly retry cron would have written it again, but that feature is skipped. So only an API restart finishes the job: it has no Retry button, and `retry()` refuses any job that isn't `failed`. While the service runs, that goes against R4 ("no job ends without a status").

**Fix (owner's design):**
- **One field:** every job endpoint returns `canRetry`, and the UI shows the Retry button **only** from it, never from a state or a message.
- **One rule, one place:** `Runner.can_retry(job)` decides, and `retry()` uses the same rule. The button and the endpoint can't disagree.
- **The rule:** no run is going for the job, and either:
  - the job is `failed`, as before; or
  - it **owes Source its status**: its outcome is saved, but `status_reported` is false.
- **`retry()` is unchanged otherwise:** it clears the result and runs again from the checkpoints. For a job that owes its status, that means:
  - **outcome `Published`** (delivered, Source not told): every checkpoint up to step 5 is saved, so the run only writes the status. Nothing is read or delivered again;
  - **outcome `Error`** (a step failed, then telling Source failed too): the run tries again from wherever its checkpoints stop, like Retry on a failed job, and then writes the new outcome. Repeats are safe, exactly as for any retry (spec 5.1).

**Not in scope:** the other final-review findings (the `async` dependencies, README and spec cleanup) are separate changes.

**Spec:** `docs/specs/2026-11-08-document-publishing-service.md`, sections 4.4, 4.6, 5.3, 7.1, 7.2 and 8, updated in Task 3.

---

## File structure

| File | Change |
|---|---|
| `api/app/runner.py` | `can_retry()`; `retry()` uses it; `NotRetryable` docstring |
| `api/app/models/jobs.py` | `JobView(JobOut)` with `can_retry` |
| `api/app/routers/jobs.py` | Every job endpoint answers a `JobView`; the 409 text |
| `api/tests/test_jobs.py`, `api/tests/test_retry.py` | `canRetry` checks; the status-retry test; the status-still-writing test |
| `web/src/api.ts`, `web/src/components/JobList.vue` | `canRetry` in `Job`; the button uses it |
| `docs/specs/2026-11-08-document-publishing-service.md` | Sections 4.4, 4.6, 5.3, 7.1, 7.2, 8 |

Commands are written for Git Bash, run from the repository root. Test counts: 177 before, 179 after.

---

### Task 1: `canRetry` in the API

**Files:** Modify `api/tests/test_jobs.py`, `api/tests/test_retry.py`, `api/app/runner.py`, `api/app/models/jobs.py`, `api/app/routers/jobs.py`

- [ ] **Step 1: Write the failing tests.** Replace `api/tests/test_jobs.py`:

```python
import hashlib


async def test_publish_answers_202_with_the_job_object(publish):
    response = await publish("doc-1", "req-1")
    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "id", "requestId", "documentId", "state", "error", "createdAt", "updatedAt", "archivePath",
        "vendorAttempts", "vendorReceipt", "statusReported", "canRetry",
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
    assert finished["canRetry"] is False  # done: nothing to retry
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

Replace `api/tests/test_retry.py`:

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
STATUS = ("PUT", "/documents/doc-1/status")
REFUSED = "this job can't be retried now"


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
    assert (failed["state"], failed["canRetry"]) == ("failed", True)
    events.clear()
    finished = await retried(failed["id"])
    assert (finished["state"], finished["statusReported"]) == ("done", True)
    assert finished["vendorAttempts"] == 4  # 3 failed, then 1 that worked: never reset
    assert events == [("vendor", "post", failed["id"]), ("source", "status", "doc-1")]
    assert len(vendor_app.state.deliveries) == 1
    assert source_app.state.documents["doc-1"].status == "Published"


async def test_done_job_cannot_be_retried(published, api, auth_headers):
    done = await published("doc-1")
    assert done["canRetry"] is False
    response = await api.post(f"/jobs/{done['id']}/retry", headers=auth_headers)
    assert (response.status_code, response.json()) == (409, {"detail": REFUSED})


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
        shown = (await api.get(f"/jobs/{failed['id']}", headers=auth_headers)).json()
        response = await api.post(f"/jobs/{failed['id']}/retry", headers=auth_headers)
    finally:
        unfinished.cancel()
    assert shown["canRetry"] is False
    assert (response.status_code, response.json()) == (409, {"detail": REFUSED})
    assert saved(settings)["state"] == "failed"


async def test_lost_delete_answer_then_retry_delivers(
    published, retried, source, source_app, vendor_app, settings, events
):
    source.answers_lost.add(DELETE)
    failed = await published("doc-1")
    assert failed["error"] == "Deleting the original in Source failed: HTTP 503"
    assert source_app.state.documents["doc-1"].content is None  # the delete did happen
    source.answers_lost.clear()
    events.clear()
    finished = await retried(failed["id"])
    assert finished["state"] == "done"
    # Intact copy, link set: only the delete is repeated (spec 5.4).
    assert events == [("source", "delete", "doc-1"), ("vendor", "post", failed["id"]), ("source", "status", "doc-1")]
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
    source.refused.add(DELETE)  # fails after the link: the retry must link again
    failed = await published("doc-1")
    assert failed["error"] == "Deleting the original in Source failed: HTTP 503"
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


async def test_retry_writes_a_status_source_refused(published, api, auth_headers, settle, source, events, vendor_app):
    source.refused.add(STATUS)
    waiting = await published("doc-1")
    assert (waiting["state"], waiting["error"]) == ("running", "Status not written yet: HTTP 503")
    assert waiting["canRetry"] is True  # delivered; only Source's status is missing
    source.refused.clear()
    events.clear()
    response = await api.post(f"/jobs/{waiting['id']}/retry", headers=auth_headers)
    assert response.status_code == 202
    await settle()
    finished = (await api.get(f"/jobs/{waiting['id']}", headers=auth_headers)).json()
    assert (finished["state"], finished["statusReported"], finished["canRetry"]) == ("done", True, False)
    assert events == [("source", "status", "doc-1")]  # step 6 only: nothing delivered again
    assert len(vendor_app.state.deliveries) == 1


async def test_job_still_writing_its_status_cannot_be_retried(make_api, source_app, auth_headers):
    """Outcome saved, status not written yet, but the run is still going: no retry."""
    stub = httpx.ASGITransport(app=source_app)
    reached = asyncio.Event()

    async def hold_status_write(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status"):
            reached.set()
            await asyncio.Event().wait()  # never answers
        return await stub.handle_async_request(request)

    api = await make_api(source=httpx.MockTransport(hold_status_write))
    job = (await api.post("/publish", json={"documentId": "doc-1", "requestId": "r"}, headers=auth_headers)).json()
    async with asyncio.timeout(2):
        await reached.wait()
    assert (await api.get(f"/jobs/{job['id']}", headers=auth_headers)).json()["canRetry"] is False
    assert (await api.post(f"/jobs/{job['id']}/retry", headers=auth_headers)).status_code == 409
```

What's new:
- **`canRetry` is checked** on a failed job (`true`), a done job (`false`), a job whose last run hasn't finished (`false`) and a job that is still running (`false`).
- **`test_retry_writes_a_status_source_refused`:**
  - Source refuses the status write, so the job stays `running` with `canRetry: true`;
  - once Source accepts again, a retry ends it `done`;
  - the only call made is the status write, and the Vendor still holds one delivery.
- **`test_job_still_writing_its_status_cannot_be_retried`:** holds only the status write, so the job has its outcome and still owes its status, but its run is still going. It checks `canRetry: false` and a 409. Without the "no run going" part of the rule, this test fails.
- **The 409 text** is now "this job can't be retried now". "Only failed jobs" is no longer true.

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose run --rm -T tests`
Expected: `7 failed, 172 passed`. `canRetry` is missing, and the 409 text has changed.

- [ ] **Step 3: The rule.** In `api/app/runner.py`:
  - change `NotRetryable`'s docstring to `"""The job is neither failed nor owing its status, or its last run hasn't finished."""`;
  - replace `retry()` with:

```python
    def can_retry(self, job: Job) -> bool:
        """Failed, or still owing Source its status, with no run going (spec 5.3)."""
        owes_status = job.outcome is not None and not job.status_reported
        return not self._has_task(job) and (job.state == "failed" or owes_status)

    def retry(self, job: Job) -> None:
        """Runs a job again from its checkpoints (spec 5.3); no await, so it can't interleave."""
        if not self.can_retry(job):
            raise NotRetryable(job.id)
        job.outcome = job.outcome_message = job.error = None
        job.status_reported = False
        job.state = "queued"
        self._store.save(job)
        self._submit(job)
```

- [ ] **Step 4: The answer model.** In `api/app/models/jobs.py`, add between `JobOut` and `Job`:

```python
class JobView(JobOut):
    """A job as every job endpoint answers: its fields, plus whether Retry is allowed now."""

    can_retry: bool
```

`JobView` is answer-only. `Job`, which is saved to disk, still inherits from `JobOut`, so `can_retry` never lands in a job file.

- [ ] **Step 5: The endpoints.** Replace `api/app/routers/jobs.py`:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..auth import require_token
from ..dependencies import get_runner, get_store
from ..models.jobs import Job, JobOut, JobView, PublishRequest
from ..runner import JobRecordUnreadable, NotRetryable, Runner
from ..store import JobStore

router = APIRouter(dependencies=[Depends(require_token)])
VISIBLE_FIELDS = set(JobOut.model_fields)


def view(job: Job, runner: Runner) -> JobView:
    """The job as the API shows it; the runner knows whether Retry is allowed now."""
    return JobView(**job.model_dump(include=VISIBLE_FIELDS), can_retry=runner.can_retry(job))


# The body is read by hand, so OpenAPI needs to be told about it.
PUBLISH_BODY = {
    "required": True,
    "content": {"application/json": {"schema": PublishRequest.model_json_schema(by_alias=True)}},
}


@router.post("/publish", status_code=202, openapi_extra={"requestBody": PUBLISH_BODY})
async def publish(request: Request, runner: Annotated[Runner, Depends(get_runner)]) -> JobView:
    # Read after the token check, so an anonymous broken body gets 401, not 422.
    try:
        order = PublishRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        raise RequestValidationError(errors) from None
    try:
        return view(runner.publish(order.document_id, order.request_id), runner)
    except JobRecordUnreadable:
        raise HTTPException(409, "The job record for this document is unreadable; manual action needed") from None


@router.get("/jobs")
async def list_jobs(
    store: Annotated[JobStore, Depends(get_store)], runner: Annotated[Runner, Depends(get_runner)]
) -> list[JobView]:
    return [view(job, runner) for job in store.newest_first()]


def job_or_404(job_id: str, store: Annotated[JobStore, Depends(get_store)]) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.get("/jobs/{job_id}")
async def get_job(
    job: Annotated[Job, Depends(job_or_404)], runner: Annotated[Runner, Depends(get_runner)]
) -> JobView:
    return view(job, runner)


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry_job(
    job: Annotated[Job, Depends(job_or_404)], runner: Annotated[Runner, Depends(get_runner)]
) -> JobView:
    try:
        runner.retry(job)
    except NotRetryable:
        raise HTTPException(409, "this job can't be retried now") from None
    return view(job, runner)
```

- **`view()`** builds the answer: the `JobOut` fields of the job, plus `can_retry` from the runner. All four job endpoints use it, so they all carry `canRetry`.
- **`list_jobs`, `get_job`** take the runner as well, because only it knows whether a run is going.

- [ ] **Step 6: Run and confirm pass**

Run: `docker compose run --rm -T tests && docker compose run --rm -T lint`
Expected: `179 passed`, then `All checks passed!`.

Run it once more with warnings as errors: `docker compose run --rm -T tests -q -W error`
Expected: `179 passed`.

Suggested commit: `feat(api): canRetry, and retry for jobs owing their status`

---

### Task 2: The button follows `canRetry`

**Files:** Modify `web/src/api.ts`, `web/src/components/JobList.vue`

- [ ] **Step 1:** in `web/src/api.ts`, in `interface Job`, after `vendorReceipt`, add `canRetry: boolean`.
- [ ] **Step 2:** in `web/src/components/JobList.vue`, change the button's `v-if="job.state === 'failed'"` to `v-if="job.canRetry"`.
- [ ] **Step 3: Build.** Run: `(cd web && npm run build)`. Expected: `vue-tsc` passes and Vite prints `✓ built`.

Suggested commit: `feat(web): show retry from canRetry`

---

### Task 3: Live check and spec sync

- [ ] **Step 1: Live check.** A job that can't tell Source its outcome gets a Retry button. It's staged by stopping the Source stub before publishing. Pick a document no job has used yet, here `<id>`.

```bash
docker compose up -d --wait --build api web
T=$(curl -s -X POST localhost:8000/oauth/token -d client_id=demo-client -d client_secret=demo-secret | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
H="Authorization: Bearer $T"
curl -s localhost:8000/jobs -H "$H" | grep -o '"canRetry":\(true\|false\)' | sort | uniq -c
```

Expected: every job has a `canRetry` field: `true` on failed jobs and on Running jobs that owe their status, `false` on done jobs and on jobs whose run is going.

Then, in the browser:
1. Run `docker compose stop source-stub`.
2. Type the document id into the field and publish it. The dropdown can't load while Source is stopped. The job fails at step 1 and can't write its status either, so it stays **Running** with "Status not written yet: ConnectError" and shows **Retry**.
3. Run `docker compose start source-stub`.
4. Click **Retry**. The retry runs the job again from its checkpoints. None were saved, since step 1 never succeeded, so it runs fully. It normally ends **Done**, with Source showing "Published". It can end **Failed** if the Vendor fails all 3 attempts, and then Retry shows again.

- [ ] **Step 2: Spec sync.** In `docs/specs/2026-11-08-document-publishing-service.md`:
  - **5.3 "Retrying failed jobs", item 1:** "Acts only on `failed` jobs that have no running task." becomes "Acts on a job with no running task that is either `failed`, or owes Source its status (outcome saved, `status_reported` false). The same rule gives the API's `canRetry`. A job owing a `Published` status then only writes it; one owing an `Error` runs again from its checkpoints, like a failed job."
  - **4.4, "Step 6 fails for any other reason":** after "The next cron run tries the status write again." add "Until then (the cron is skipped), the Retry button does: the job shows it through `canRetry`." In the outer guard's "Otherwise" item, after "the next cron run picks it up" add "(or the Retry button)".
  - **4.6, item 1:** after "If step 6 fails, the job waits for the next cron run (4.4)" add "or the Retry button".
  - **7.1 Job object:** add the row `| `canRetry` | bool | `true` |` with "Whether `POST /jobs/{id}/retry` is allowed now; the UI shows Retry only from this."
  - **7.2, `POST /jobs/{id}/retry`:**
    - the 202 "When" becomes "the job can be retried (`canRetry`)";
    - the 409 row becomes "job can't be retried now (not failed and owing no status, or its last run is still finishing) | `{"detail": "this job can't be retried now"}`".
  - **8, "Failed" row:** "A **Retry** button" becomes "A **Retry** button (shown whenever `canRetry` is true, which also covers a Running job that owes its status)".

Suggested commit: `docs: sync spec with canRetry`

---

### Task 4: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer with the changed files and CLAUDE.md rule 4. Fix what it finds, re-run lint, tests and the web build, and review again until it comes back clean.
- [ ] **Step 2: User review.** Hand the result to the owner and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address canRetry review findings`
