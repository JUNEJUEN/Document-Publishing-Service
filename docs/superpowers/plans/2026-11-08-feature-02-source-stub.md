# Feature 2: Source API Stub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Goal:** a working fake CRM (`stubs/source_api.py`) that serves the endpoints in spec section 7.3. It does a soft delete, records every call in an event log, and, under Compose, saves its data so it survives a restart.

**Architecture:**
- **App:** one FastAPI module built by `create_app(seed, data_file, events)`.
- **Documents:** kept in an insertion-ordered dict of `StoredDocument` records.
- **Handlers:** all `async def`, so they run one at a time on the event loop. Two changes can never write the save file at the same moment.
- **Saving:** when `data_file` is set, every change rewrites a JSON file. It writes a temp file, fsyncs it, then renames it, with the content base64-encoded. On start, the file is loaded if it exists; otherwise the stub seeds `doc-1` … `doc-N`.
- **Event log:** a plain list of `(service, action, id)` tuples. Tests can pass in their own list, so later API tests can check the order of calls, or count `GET /documents` calls.

**Tech stack:** Python 3.12, FastAPI 0.115.6 (with Pydantic v2).

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`, section 7.3 (the endpoint table and "Behaviour"), D11 (stubs saved on volumes), and section 12, feature 2.

**Testing:** owner-approved exception to the CLAUDE.md test-first flow (spec sections 9 and 12): the stub gets **no tests of its own**. It is checked by hand with `curl` under Compose (Task 3), and later exercised by the API tests from feature 5 on. The existing API tests must still pass.

**Out of scope:** the Vendor stub (feature 3); anything in `api/`.

---

## File structure

| File | Change |
|---|---|
| `stubs/source_api.py` | Replace the skeleton with the full stub |
| `docker-compose.yml` | Add `SEED_DOCUMENTS` and `DATA_FILE` to `source-stub` |

Commands are written for Git Bash, run from the repository root. Nothing runs Python on the host: JSON checks run inside the container.

---

### Task 1: The Source stub

**Files:**
- Modify: `stubs/source_api.py`, replacing it completely

- [ ] **Step 1: Write `stubs/source_api.py`**

```python
"""Source API stub: the CRM that owns a document until it is published."""
import base64
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

SERVICE = "source"


@dataclass
class StoredDocument:
    id: str
    filename: str
    content_type: str
    content: bytes | None  # None after delete; the record stays for the status
    status: str = "Released"
    status_message: str | None = None
    archive_uri: str | None = None
    archive_sha256: str | None = None


class ArchiveLink(BaseModel):
    uri: str
    sha256: str


class StatusUpdate(BaseModel):
    status: Literal["Published", "Error"]
    message: str | None = None


def seed_documents(count: int) -> dict[str, StoredDocument]:
    docs = {}
    for n in range(1, count + 1):
        doc_id = f"doc-{n}"
        docs[doc_id] = StoredDocument(
            id=doc_id,
            filename=f"{doc_id}.txt",
            content_type="text/plain",
            content=f"Contract {doc_id}\nReleased for partner delivery.\n".encode(),
        )
    return docs


def metadata(doc: StoredDocument) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "contentType": doc.content_type,
        "size": None if doc.content is None else len(doc.content),
        "deleted": doc.content is None,
        "status": doc.status,
        "statusMessage": doc.status_message,
        "archiveUri": doc.archive_uri,
        "archiveSha256": doc.archive_sha256,
    }


def load(path: Path) -> dict[str, StoredDocument]:
    docs = {}
    for row in json.loads(path.read_text(encoding="utf-8")):
        content = row.pop("content")
        doc = StoredDocument(**row, content=None if content is None else base64.b64decode(content))
        docs[doc.id] = doc
    return docs


def save(path: Path, docs: dict[str, StoredDocument]) -> None:
    rows = []
    for doc in docs.values():
        row = asdict(doc)
        row["content"] = None if doc.content is None else base64.b64encode(doc.content).decode()
        rows.append(row)
    # Temp file, fsync, rename: the saved file is never half-written.
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def create_app(seed: int = 0, data_file: Path | None = None, events: list | None = None) -> FastAPI:
    """`events` collects (service, action, id) for every call; tests pass a shared list."""
    app = FastAPI(title="Source API stub")
    if data_file is not None and data_file.exists():
        docs = load(data_file)
    else:
        docs = seed_documents(seed)
        if data_file is not None:
            save(data_file, docs)
    events = events if events is not None else []
    app.state.documents = docs
    app.state.events = events

    def record(action: str, doc_id: str | None) -> None:
        events.append((SERVICE, action, doc_id))

    def find(doc_id: str) -> StoredDocument:
        doc = docs.get(doc_id)
        if doc is None:
            raise HTTPException(404, f"document {doc_id} not found")
        return doc

    def changed() -> None:
        if data_file is not None:
            save(data_file, docs)

    # Handlers are async so they run one at a time: saves never overlap.
    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/documents")
    async def list_documents() -> list[dict]:
        record("list", None)
        return [metadata(doc) for doc in docs.values()]

    @app.get("/documents/{doc_id}")
    async def get_document(doc_id: str) -> dict:
        record("get", doc_id)
        return metadata(find(doc_id))

    @app.get("/documents/{doc_id}/content")
    async def get_content(doc_id: str) -> Response:
        record("get_content", doc_id)
        doc = find(doc_id)
        if doc.content is None:
            raise HTTPException(410, f"document {doc_id} was deleted")
        # Raw header: Starlette would otherwise append a charset.
        return Response(content=doc.content, headers={"content-type": doc.content_type})

    @app.put("/documents/{doc_id}/archive-link")
    async def set_archive_link(doc_id: str, link: ArchiveLink) -> dict:
        record("archive_link", doc_id)
        doc = find(doc_id)
        doc.archive_uri, doc.archive_sha256 = link.uri, link.sha256
        changed()
        return metadata(doc)

    @app.delete("/documents/{doc_id}", status_code=204)
    async def delete_document(doc_id: str) -> Response:
        record("delete", doc_id)
        doc = find(doc_id)
        doc.content = None
        changed()
        return Response(status_code=204)

    @app.put("/documents/{doc_id}/status")
    async def set_status(doc_id: str, update: StatusUpdate) -> dict:
        record("status", doc_id)
        doc = find(doc_id)
        doc.status, doc.status_message = update.status, update.message
        changed()
        return metadata(doc)

    return app


app = create_app(
    seed=int(os.environ.get("SEED_DOCUMENTS", "20")),
    data_file=Path(os.environ["DATA_FILE"]) if os.environ.get("DATA_FILE") else None,
)
```

- [ ] **Step 2: Check that the existing tests still pass**

Run: `docker compose run --rm -T tests`
Expected: `3 passed`. The API tests don't import the stub yet; this checks nothing else broke.

Suggested commit: `feat(stubs): implement source api stub`

---

### Task 2: Compose settings for the Source stub

**Files:**
- Modify: `docker-compose.yml` (the `source-stub` service)

- [ ] **Step 1: Add an `environment` block to `source-stub`,** directly after its `command` line

```yaml
    environment:
      SEED_DOCUMENTS: "20"
      DATA_FILE: /data/source.json
```

Suggested commit: `build: configure source stub seeding and storage`

---

### Task 3: Manual check under Compose

This replaces the stub tests (owner-approved exception). Every command must give the expected result. `LIST` is a helper that prints a summary of `GET /documents` from inside the container:

```bash
LIST() { docker compose exec -T source-stub python -c "import json,urllib.request as u; d=json.load(u.urlopen('http://localhost:8001/documents')); print(len(d), d[0]['id'], d[-1]['id'], d[0]['deleted'])"; }
```

- [ ] **Step 1: Start from a clean state**

```bash
docker compose down -v
docker compose up -d --wait --build source-stub
curl -s localhost:8001/health; echo
```
Expected: the command returns once `source-stub` is healthy, then `{"ok":true}`.

- [ ] **Step 2: List and read**

```bash
LIST
curl -s localhost:8001/documents/doc-1; echo
curl -s -D - localhost:8001/documents/doc-1/content | grep -i -e '^content-type' -e 'Contract'
for path in documents/nope documents/nope/content; do curl -s -o /dev/null -w "$path %{http_code}\n" localhost:8001/$path; done
```
Expected:
- `20 doc-1 doc-20 False`
- doc-1 metadata: `"size":46`, `"deleted":false`, `"status":"Released"`, `null` archive fields
- `content-type: text/plain` (no charset) and `Contract doc-1`
- `documents/nope 404` and `documents/nope/content 404`

- [ ] **Step 3: Archive link, delete and status**

```bash
curl -s -X PUT localhost:8001/documents/doc-1/archive-link -H 'Content-Type: application/json' -d '{"uri":"archive://doc-1/abc/doc-1.txt","sha256":"abc"}'; echo
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8001/documents/doc-1
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8001/documents/doc-1
curl -s -o /dev/null -w '%{http_code}\n' localhost:8001/documents/doc-1/content
LIST
curl -s -X PUT localhost:8001/documents/doc-1/archive-link -H 'Content-Type: application/json' -d '{"uri":"archive://doc-1/def/doc-1.txt","sha256":"def"}' | grep -o '"archiveSha256":"def"'
curl -s -X PUT localhost:8001/documents/doc-1/status -H 'Content-Type: application/json' -d '{"status":"Published"}'; echo
curl -s -X PUT localhost:8001/documents/doc-2/status -H 'Content-Type: application/json' -d '{"status":"Error","message":"Vendor API answered 503"}' | grep -o '"statusMessage":"[^"]*"'
curl -s -o /dev/null -w '%{http_code}\n' -X PUT localhost:8001/documents/doc-1/status -H 'Content-Type: application/json' -d '{"status":"Maybe"}'
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8001/documents/nope
curl -s -o /dev/null -w '%{http_code}\n' -X PUT localhost:8001/documents/nope/archive-link -H 'Content-Type: application/json' -d '{"uri":"x","sha256":"x"}'
curl -s -o /dev/null -w '%{http_code}\n' -X PUT localhost:8001/documents/nope/status -H 'Content-Type: application/json' -d '{"status":"Error"}'
```
Expected, in order:
- metadata with `"archiveUri":"archive://doc-1/abc/doc-1.txt"` and `"archiveSha256":"abc"`
- `204`, `204` (a repeated delete still succeeds)
- `410`
- `20 doc-1 doc-20 True` (deleted documents stay in the list)
- `"archiveSha256":"def"` (the link can be written after the delete)
- metadata with `"deleted":true`, `"size":null` and `"status":"Published"` (the status can be written after the delete)
- `"statusMessage":"Vendor API answered 503"`
- `422`
- `404`, `404`, `404`

- [ ] **Step 4: The data survives a restart**

```bash
docker compose restart source-stub && docker compose up -d --wait source-stub
curl -s localhost:8001/documents/doc-1; echo
docker compose exec -T source-stub sh -c 'ls -l /data && whoami'
```
Expected:
- doc-1 still has `"deleted":true`, `"status":"Published"` and `"archiveSha256":"def"`;
- `/data` holds only `source.json`, owned by `stubuser`, with no `.tmp` file;
- `whoami` prints `stubuser`.

- [ ] **Step 5: A reset starts fresh**

```bash
docker compose down -v
docker compose up -d --wait source-stub
curl -s localhost:8001/documents/doc-1; echo
docker compose down
```
Expected: doc-1 is back to `"deleted":false`, `"status":"Released"`, with no archive link.

---

### Task 4: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with only `stubs/source_api.py`, the `source-stub` part of `docker-compose.yml`, and spec section 7.3. Fix what it finds, re-run Task 3's affected checks and `docker compose run --rm -T tests`, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the user with the Task 3 output, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix(stubs): address source stub review findings`
