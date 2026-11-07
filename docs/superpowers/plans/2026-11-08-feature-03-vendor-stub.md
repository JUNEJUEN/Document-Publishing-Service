# Feature 3: Vendor API Stub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

> **As built:** the plan review led to these changes, applied during implementation. The shared helper is named `stub_storage.py`. `outcomes` accepts a list, and unknown values raise `ValueError`. The manual check counts records with `grep -o … | wc -l` before and after the restart, expects 202 minus the 503 count, shows the 400 status code, re-posts `k1` at rate 1, and includes an import smoke check. `stubs/vendor_api.py` is the source of truth for the code.

**Goal:** a fake partner (`stubs/vendor_api.py`) that implements spec section 7.4:
- it receives files with an `Idempotency-Key`;
- it fails on purpose about 30% of the time, half of those failures *after* storing the file;
- it keeps only one record per key;
- under Compose, it saves what it received so the data survives a restart.

**Architecture:**
- **App:** one FastAPI module built by `create_app(failure_rate, rng, outcomes, data_file, events)`.
- **Outcome per call:** taken from a scripted list (`outcomes`, for tests), or else rolled with `rng`.
- **Deliveries:** kept in a dict keyed by idempotency key.
- **No await between check and store:** the file is read *before* the key lookup, so the "check key, then store" step has no `await` in it. Two calls with the same key can never both store.
- **Saving:** done by a small shared helper, `stubs/stub_storage.py` (temp file, fsync, rename). It is also used by the Source stub, which is refactored to use it.
- **Event log:** works the same way as in the Source stub. Each call adds `("vendor", "post", key)`.

**Tech stack:** Python 3.12, FastAPI 0.115.6, python-multipart 0.0.20 (needed by FastAPI for file uploads).

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`, section 7.4, section 7.3 "Call log", D11 and section 12, feature 3.

**Testing:** owner-approved exception to the CLAUDE.md test-first flow (spec sections 9 and 12): the stub gets **no tests of its own**. It is checked by hand with `curl` under Compose (Task 4), and later exercised by the API tests from feature 7 on. The existing API tests must still pass.

**Out of scope:** anything in `api/`.

---

## File structure

| File | Change |
|---|---|
| `stubs/stub_storage.py` | New: `save_json(path, data)`, an atomic JSON write |
| `stubs/source_api.py` | Use `stub_storage.save_json` instead of its own write code |
| `stubs/vendor_api.py` | Replace the skeleton with the full stub |
| `stubs/requirements.txt` | Add `python-multipart==0.0.20` |
| `docker/stubs.Dockerfile` | Also copy `stub_storage.py` |
| `docker-compose.yml` | Add `FAILURE_RATE` and `DATA_FILE` to `vendor-stub` |

Commands are written for Git Bash, run from the repository root.

---

### Task 1: Shared storage helper

**Files:**
- Create: `stubs/stub_storage.py`
- Modify: `stubs/source_api.py`, `docker/stubs.Dockerfile`

- [ ] **Step 1: Write `stubs/stub_storage.py`**

```python
"""Atomic JSON writes shared by the stubs."""
import json
import os
from pathlib import Path


def save_json(path: Path, data) -> None:
    # Temp file, fsync, rename: the saved file is never half-written.
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
```

- [ ] **Step 2: Use it in `stubs/source_api.py`**

Add `from stub_storage import save_json` after the `pydantic` import. Replace the body of `save()` from the comment line to the end with a single call:

```python
def save(path: Path, docs: dict[str, StoredDocument]) -> None:
    rows = []
    for doc in docs.values():
        row = asdict(doc)
        row["content"] = None if doc.content is None else base64.b64encode(doc.content).decode()
        rows.append(row)
    save_json(path, rows)
```

`import os` stays, because the module-level `app` reads environment variables. `import json` stays, because `load()` uses it.

- [ ] **Step 3: Copy `stub_storage.py` into the stub image.** In `docker/stubs.Dockerfile`, change the copy line to:

```dockerfile
COPY stubs/source_api.py stubs/vendor_api.py stubs/stub_storage.py ./
```

Suggested commit: `refactor(stubs): share atomic json save helper`

---

### Task 2: The Vendor stub

**Files:**
- Modify: `stubs/vendor_api.py` (replace completely), `stubs/requirements.txt`

- [ ] **Step 1: Add the upload package to `stubs/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
python-multipart==0.0.20
```

- [ ] **Step 2: Write `stubs/vendor_api.py`**

```python
"""Vendor API stub: the external partner. Fails on purpose about 30% of the time."""
import hashlib
import json
import os
import random
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from stub_storage import save_json

SERVICE = "vendor"
OK = "ok"
FAIL = "fail"  # nothing stored
FAIL_AFTER_STORE = "fail_after_store"  # stored, but the caller sees an error


@dataclass
class Delivery:
    receipt_id: str
    idempotency_key: str
    filename: str
    sha256: str
    size: int


def view(delivery: Delivery) -> dict:
    return {
        "receiptId": delivery.receipt_id,
        "idempotencyKey": delivery.idempotency_key,
        "filename": delivery.filename,
        "sha256": delivery.sha256,
        "size": delivery.size,
    }


def load(path: Path) -> dict[str, Delivery]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["idempotency_key"]: Delivery(**row) for row in rows}


def create_app(
    failure_rate: float = 0.3,
    rng: random.Random | None = None,
    outcomes: Iterator[str] | None = None,
    data_file: Path | None = None,
    events: list | None = None,
) -> FastAPI:
    """`outcomes` scripts each call's result for tests; once used up, every call is OK."""
    app = FastAPI(title="Vendor API stub")
    rng = rng or random.Random()
    deliveries = load(data_file) if data_file is not None and data_file.exists() else {}
    events = events if events is not None else []
    app.state.deliveries = deliveries
    app.state.events = events
    app.state.attempts = 0

    def next_outcome() -> str:
        if outcomes is not None:
            return next(outcomes, OK)
        roll = rng.random()
        if roll < failure_rate / 2:
            return FAIL
        if roll < failure_rate:
            return FAIL_AFTER_STORE
        return OK

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/files")
    async def list_files() -> list[dict]:
        return [view(d) for d in deliveries.values()]

    @app.post("/files")
    async def receive(
        file: UploadFile = File(...),
        idempotency_key: str | None = Header(default=None),
    ):
        events.append((SERVICE, "post", idempotency_key))
        app.state.attempts += 1
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header is required")
        # Read before the key check: no await between check and store.
        content = await file.read()
        outcome = next_outcome()
        if outcome == FAIL:
            return JSONResponse({"detail": "vendor temporarily unavailable"}, status_code=503)
        delivery = deliveries.get(idempotency_key)
        if delivery is None:
            delivery = Delivery(
                receipt_id=uuid.uuid4().hex,
                idempotency_key=idempotency_key,
                filename=file.filename or "file",
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            )
            deliveries[idempotency_key] = delivery
            if data_file is not None:
                save_json(data_file, [asdict(d) for d in deliveries.values()])
        if outcome == FAIL_AFTER_STORE:
            return JSONResponse({"detail": "gateway timeout"}, status_code=504)
        return {"receiptId": delivery.receipt_id, "sha256": delivery.sha256}

    return app


app = create_app(
    failure_rate=float(os.environ.get("FAILURE_RATE", "0.3")),
    data_file=Path(os.environ["DATA_FILE"]) if os.environ.get("DATA_FILE") else None,
)
```

- [ ] **Step 3: Rebuild the tests image (new package) and run the tests**

Run: `docker compose build tests && docker compose run --rm -T tests`
Expected: `3 passed`.

Suggested commit: `feat(stubs): implement vendor api stub`

---

### Task 3: Compose settings for the Vendor stub

**Files:**
- Modify: `docker-compose.yml` (the `vendor-stub` service)

- [ ] **Step 1: Add an `environment` block to `vendor-stub`,** directly after its `command` line. `VENDOR_FAILURE_RATE` lets you override the rate from the shell for demos and checks.

```yaml
    environment:
      FAILURE_RATE: ${VENDOR_FAILURE_RATE:-0.3}
      DATA_FILE: /data/vendor.json
```

Suggested commit: `build: configure vendor stub failure rate and storage`

---

### Task 4: Manual check under Compose

This replaces the stub tests (owner-approved exception). The upload file is `docker/tests-requirements.txt`, so its sha256 can be compared.

```bash
F=docker/tests-requirements.txt
SHA=$(sha256sum "$F" | cut -d' ' -f1)
POST() { curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8002/files -H "Idempotency-Key: $1" -F "file=@$F"; }
```

- [ ] **Step 1: Source stub still works after the refactor**

```bash
docker compose down -v
docker compose up -d --wait --build source-stub vendor-stub
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8001/documents/doc-1
docker compose exec -T source-stub sh -c 'ls /data'
```
Expected: both stubs come up healthy; `204`; `source.json` (the shared helper saved it).

- [ ] **Step 2: No failures (rate 0): store once per key**

```bash
VENDOR_FAILURE_RATE=0 docker compose up -d --wait vendor-stub
curl -s -X POST localhost:8002/files -F "file=@$F"; echo
curl -s -X POST localhost:8002/files -H 'Idempotency-Key: k1' -F "file=@$F"; echo
curl -s -X POST localhost:8002/files -H 'Idempotency-Key: k1' -F "file=@$F"; echo
curl -s localhost:8002/files; echo
echo "$SHA"
```
Expected:
- `{"detail":"Idempotency-Key header is required"}`
- the same `{"receiptId":"…","sha256":"…"}` twice: one receipt for `k1`
- `GET /files` lists exactly one record: key `k1`, filename `tests-requirements.txt`, the size of the file, and a `sha256` equal to the last line

- [ ] **Step 3: Always failing (rate 1): 503 or 504, and a 504 stores the file**

```bash
VENDOR_FAILURE_RATE=1 docker compose up -d --wait vendor-stub
for i in $(seq 10); do POST k2; done | sort | uniq -c
curl -s localhost:8002/files | grep -o '"idempotencyKey":"k[0-9]"'
```
Expected:
- only `503` and `504` codes, totalling 10;
- the list shows `k1` and `k2`, because a "fail after store" kept `k2`. There's a 1-in-1024 chance that no 504 happened; if so, re-run the loop.

- [ ] **Step 4: Default rate: about 30% failures**

```bash
docker compose up -d --wait vendor-stub
for i in $(seq 200); do POST "r$i"; done | sort | uniq -c
```
Expected: about 140 × `200`, 30 × `503` and 30 × `504`. Anything between 20% and 40% failures in total is fine.

- [ ] **Step 5: The data survives a restart, and a reset clears it**

```bash
docker compose restart vendor-stub && docker compose up -d --wait vendor-stub
curl -s localhost:8002/files | grep -c '"receiptId"'
docker compose exec -T vendor-stub sh -c 'ls -l /data && whoami'
docker compose down -v
docker compose up -d --wait vendor-stub
curl -s localhost:8002/files; echo
docker compose down
```
Expected:
- the same number of records as before the restart (2 plus the successful `r…` uploads);
- `/data` holds only `vendor.json`, owned by `stubuser`, with no `.tmp` file;
- `whoami` prints `stubuser`;
- after the reset, `[]`.

---

### Task 5: Reviews

- [ ] **Step 1: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with only `stubs/stub_storage.py`, `stubs/vendor_api.py`, the changed part of `stubs/source_api.py`, `stubs/requirements.txt`, `docker/stubs.Dockerfile`, the `vendor-stub` part of `docker-compose.yml`, and spec section 7.4. Fix what it finds, re-run Task 4's affected checks and `docker compose run --rm -T tests`, and review again until it comes back clean.

- [ ] **Step 2: User review.** Hand the result to the user with the Task 4 output, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix(stubs): address vendor stub review findings`
