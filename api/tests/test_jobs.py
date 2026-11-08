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
