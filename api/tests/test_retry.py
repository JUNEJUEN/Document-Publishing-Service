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
    assert finished["vendorAttempts"] == 4  # 3 failed, then 1 that worked: never reset
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

