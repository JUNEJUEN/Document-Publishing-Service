import asyncio


async def test_same_request_twice_gives_the_same_job(publish, settle, events):
    first = await publish("doc-1", "req-1")
    second = await publish("doc-1", "req-1")
    await settle()
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert events.count(("source", "delete", "doc-1")) == 1
    assert events.count(("vendor", "post", first.json()["id"])) == 1  # delivered once


async def test_simultaneous_duplicates_create_one_job(api, auth_headers, publish, settle):
    responses = await asyncio.gather(*(publish("doc-1", "req-1") for _ in range(10)))
    await settle()
    assert {r.json()["id"] for r in responses} == {responses[0].json()["id"]}
    assert len((await api.get("/jobs", headers=auth_headers)).json()) == 1


async def test_duplicate_after_the_job_finished_gives_the_same_job(published, publish, events):
    finished = await published("doc-1")
    again = await publish("doc-1")
    assert again.json() == finished
    assert events.count(("source", "get", "doc-1")) == 1
    assert events.count(("vendor", "post", finished["id"])) == 1  # delivered once


async def test_new_request_id_for_the_same_document_gives_the_same_job(publish, settle):
    first = await publish("doc-1", "req-1")
    second = await publish("doc-1", "req-2")
    await settle()
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["requestId"] == "req-1"
