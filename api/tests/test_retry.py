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


async def test_retry_writes_a_status_source_refused(
    published, api, auth_headers, settle, source, events, vendor_app, source_app
):
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
    assert source_app.state.documents["doc-1"].status == "Published"


@pytest.mark.parametrize("vendor_outcomes", [["fail"] * 3])
async def test_retry_runs_a_job_owing_an_error_status_again(published, retried, source, events, source_app):
    source.refused.add(STATUS)
    waiting = await published("doc-1")
    assert (waiting["state"], waiting["canRetry"]) == ("running", True)  # Error outcome, status not written
    source.refused.clear()
    events.clear()
    finished = await retried(waiting["id"])
    assert finished["state"] == "done"
    assert events == [("vendor", "post", waiting["id"]), ("source", "status", "doc-1")]  # delivery tried again
    assert source_app.state.documents["doc-1"].status == "Published"


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
    response = await api.post(f"/jobs/{job['id']}/retry", headers=auth_headers)
    assert (response.status_code, response.json()) == (409, {"detail": REFUSED})
