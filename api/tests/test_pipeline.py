import asyncio
import json

import httpx
import pytest

import source_api
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


async def test_document_gone_before_the_status_write_fails_without_a_reason(make_api, source_app, published_on):
    gone = flaky(source_app, failures=99, path="/documents/doc-1/status", status=404)
    finished = await published_on(await make_api(source=gone))
    assert (finished["state"], finished["statusReported"]) == ("failed", False)
    assert finished["error"] == "Document not found in Source; the status cannot be written."  # Published: no reason


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


async def test_failure_after_the_status_was_written_still_ends_the_job(published, monkeypatch, settings, caplog):
    save, failed = JobStore.save, []

    def save_failing_once_after_the_status(store, job):
        if job.status_reported and not failed:
            failed.append(job)
            raise OSError("disk full")
        save(store, job)

    monkeypatch.setattr(JobStore, "save", save_failing_once_after_the_status)
    finished = await published("doc-1")
    assert failed  # the first final save did fail
    record = saved(settings)
    assert (record["state"], record["error"], record["status_reported"]) == ("done", None, True)
    assert f"Job {finished['id']}: unexpected error" in caplog.text


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
