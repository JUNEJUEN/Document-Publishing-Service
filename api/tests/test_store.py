import json
from datetime import timedelta

from app.models.jobs import Job, utc_now
from app.store import JobStore


def test_finds_jobs_by_document_and_by_id(tmp_path):
    store = JobStore(tmp_path)
    job = Job(document_id="doc-1", request_id="r1")
    store.save(job)
    assert store.by_document("doc-1") is job
    assert store.get(job.id) is job
    assert store.by_document("doc-2") is None
    assert store.get("unknown") is None


def test_reloads_jobs_from_disk(tmp_path):
    job = Job(document_id="doc-1", request_id="r1", state="done", archive_path="doc-1/abc/doc-1.txt")
    JobStore(tmp_path).save(job)
    assert JobStore(tmp_path).by_document("doc-1") == job


def test_lists_newest_first(tmp_path):
    store = JobStore(tmp_path)
    now = utc_now()
    for n, age in enumerate([2, 0, 1]):
        store.save(Job(document_id=f"doc-{n}", request_id="r", created_at=now - timedelta(minutes=age)))
    assert [job.document_id for job in store.newest_first()] == ["doc-1", "doc-2", "doc-0"]


def test_writes_one_complete_file_per_document(tmp_path):
    store = JobStore(tmp_path)
    job = Job(document_id="doc-1", request_id="r1")
    store.save(job)
    job.state = "running"
    store.save(job)
    assert [p.name for p in tmp_path.iterdir()] == ["doc-1.json"]  # no .tmp left
    assert json.loads((tmp_path / "doc-1.json").read_text())["state"] == "running"
