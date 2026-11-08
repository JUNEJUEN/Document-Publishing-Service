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
