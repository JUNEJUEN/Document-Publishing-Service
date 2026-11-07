import httpx
import pytest

import source_api

# Source answers that must each become a 502.
FAILING_ANSWERS = {
    "404": lambda: httpx.Response(404, json={"detail": "nope"}),
    "503": lambda: httpx.Response(503, text="down"),
    "not-json": lambda: httpx.Response(200, text="<html>"),
    "object": lambda: httpx.Response(200, json={"id": "doc-1"}),
    "item-not-object": lambda: httpx.Response(200, json=["doc-1"]),
    "item-without-id": lambda: httpx.Response(200, json=[{"filename": "a.txt"}]),
    "id-not-string": lambda: httpx.Response(200, json=[{"id": 1}]),
}
KINDS = ["unreachable", "timeout", *FAILING_ANSWERS]


def failing(kind: str):
    """A Source that fails in one way, counting the calls it receives."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if kind == "unreachable":
            raise httpx.ConnectError("connection refused", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("too slow", request=request)
        return FAILING_ANSWERS[kind]()

    return httpx.MockTransport(handler), calls


async def test_returns_source_documents_unchanged(api, auth_headers, source_app, source_events):
    source_app.state.documents["doc-2"].content = None  # deleted in Source
    response = await api.get("/documents", headers=auth_headers)
    assert response.status_code == 200
    expected = [source_api.metadata(doc) for doc in source_app.state.documents.values()]
    assert response.json() == expected
    assert [doc["id"] for doc in response.json()] == ["doc-1", "doc-2", "doc-3", "doc-4", "doc-5"]
    assert response.json()[1]["deleted"] is True
    assert source_events.count(("source", "list", None)) == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_source_failure_gives_502_after_one_attempt(make_api, auth_headers, kind):
    transport, calls = failing(kind)
    api = await make_api(transport)
    response = await api.get("/documents", headers=auth_headers)
    assert response.status_code == 502
    assert response.json()["detail"].startswith("Source API unavailable: ")
    assert calls == ["/documents"]


@pytest.mark.parametrize(
    "kind, reason",
    [
        ("unreachable", "ConnectError"),
        ("timeout", "ReadTimeout"),
        ("503", "HTTP 503"),
        ("not-json", "answer is not JSON"),
        ("object", "answer is not a document list"),
    ],
)
async def test_502_names_the_reason_without_internals(make_api, auth_headers, kind, reason):
    transport, _ = failing(kind)
    api = await make_api(transport)
    response = await api.get("/documents", headers=auth_headers)
    # Only the reason: no upstream URL or exception text.
    assert response.json() == {"detail": f"Source API unavailable: {reason}"}
