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
    assert finished["error"] == "Verifying the archive copy failed: archive copy is damaged or missing"
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
