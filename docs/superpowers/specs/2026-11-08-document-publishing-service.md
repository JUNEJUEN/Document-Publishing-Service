# Document Publishing Service: Design

**Date:** 2026-11-08
**Source brief:** `take-home-task-publishing-service.docx`
**Status:** revised after five AI reviews; waiting for the owner's review

## 1. Goal

When a document is released in a CRM (the Source API), the service:
1. delivers it to an external partner (the Vendor API);
2. keeps our own archive copy, so we can prove later what was sent;
3. deletes the original in the CRM;
4. writes the result back to the CRM.

The deliverable also includes:
- stub servers for both external systems;
- a separate frontend;
- Docker Compose;
- CI;
- tests.

**Rules from the brief:**
- R1. The Vendor API fails about 30% of the time. Handle it.
- R2. The same `requestId` may arrive twice. The document is delivered only once, and the second call returns the same job.
- R3. The original in the Source API is never deleted while the archive copy is not safe.
- R4. No job ends without a status written back. There are two documented exceptions:
  - the document doesn't exist in Source, so there is nothing to write to (4.4);
  - the job's own record file is unreadable (4.6).
- R5. No anonymous calls. A call without a valid token answers 401, never 500, and never does any work.

**Not needed (from the brief):** a database, a real deployment, user management.

**Assumptions:**
- **The vendor honours an `Idempotency-Key`.** "Delivered only once" relies on this. Our stub does. With a real vendor that has no such feature, a lost answer after the vendor stored the file would make delivery "at least once".
- **The caller only publishes released documents.** The service does not check the document's status in Source.
- **Source keeps the document's record after the delete** (a soft delete), so the status can still be written. Our stub does this.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| D1 | Backend | Python 3.12 + FastAPI |
| D2 | Frontend | Vue 3 + Vite + TypeScript, served by nginx |
| D3 | Job execution | asyncio tasks inside the API process. Archive file writes run in a thread (`asyncio.to_thread`). |
| D4 | Persistence | Jobs are kept in memory and mirrored to one JSON file per job. |
| D5 | Duplicates | Checked by `documentId` only. A publish for a document that already has a job returns that job. `requestId` is stored for display only. |
| D6 | Job list | Extra endpoint `GET /jobs` (needs a token) |
| D7 | Checksums | Only our own sha256 of the archive copy is used. |
| D8 | UI updates | Polls `GET /jobs` every 2 s |
| D9 | Retrying failed jobs | Manual `POST /jobs/{id}/retry` plus a Retry button, and an in-process **retry cron** that runs every hour. It retries all failed jobs, whatever caused the failure, up to `AUTO_RETRY_MAX` times each. |
| D10 | Restart | Unfinished jobs are **not** resumed automatically. They get an outcome and a status write (4.6). Most end `failed` with "Interrupted by a service restart" and are retried by the button or the retry cron. |
| D11 | Stubs | Both stubs save their data to their own Docker volume, so all containers stay consistent across restarts. |
| D12 | Instances | Exactly one API process. |
| D13 | Parallel jobs | At most `MAX_CONCURRENT_JOBS` (default 10) jobs run at the same time. The others wait as `queued`. |
| D14 | Document list | Extra endpoint `GET /documents` (needs a token). It passes the call through to Source's `GET /documents`, so the UI can show a list of documents to publish **in addition to** the document-id field the brief asks for. |

## 3. Architecture

### Containers

One `docker compose up --build` starts all four.

| Container | Port | Volume | Role |
|---|---|---|---|
| `api` | 8000 | `publishing-data:/data` | The publishing service |
| `source-stub` | 8001 | `source-data:/data` | Fake CRM |
| `vendor-stub` | 8002 | `vendor-data:/data` | Fake partner; fails about 30% of the time |
| `web` | 8080 | — | Vue app served by nginx; the browser calls `api` over HTTP |

`api` keeps:
- archived documents in `/data/archive/`;
- one JSON file per job in `/data/jobs/`.

`docker compose down -v` deletes all three volumes, which resets everything together.

### Units inside `api`

| Unit | Responsibility | Uses |
|---|---|---|
| `config` | Reads settings from environment variables (section 10) | — |
| `models/` | Pydantic models, one module per resource: `documents.py` (`Document`, Source's metadata), later the job record, job states and request/response shapes | — |
| `auth` | Issues tokens; checks the bearer token | `config`, `dependencies` |
| `store` | Jobs in memory plus JSON files; finds a job by document | `models` |
| `archive` | Writes a file durably to a stable path; verifies it by sha256 | — |
| `clients` | Source and Vendor HTTP calls; sorts failures into temporary or permanent | — |
| `backoff` | Retry helper with exponential backoff and full jitter | `clients` (error types) |
| `runner` | Runs jobs as asyncio tasks, limited by a semaphore; startup handling; retries; retry cron | `store`, `archive`, `clients`, `backoff` |
| `routers/` | HTTP endpoints, one FastAPI `APIRouter` module per area: `health.py`, `auth.py`, `documents.py`, later `jobs.py` | `auth`, `dependencies`, `models`, `clients`, `store`, `runner` |
| `dependencies` | FastAPI dependencies that hand shared objects (settings, Source client, later store and runner) from `app.state` to routes | `config`, `clients` |
| `main` | `create_app()`: builds settings and clients, adds CORS, includes the routers, runs startup and shutdown | `routers`, `clients`, `config` |

### Concurrency rule

Everything runs in one process with one event loop. Three rules follow from that:

1. **No `await` during a state change.** Every state change is made in memory and then saved with a plain synchronous file write. No `await` sits between checking a job's state and changing it. This covers:
   - the duplicate check;
   - `failed` → `queued` on a retry;
   - a run finishing.

   As a result, two requests, or a request and the cron, can never interleave on the same job, and no lock is needed.
2. **One task per job.** The runner keeps at most one task per job.
   - A job counts as running when it has a task that is not finished (`task is not None and not task.done()`).
   - A submit for a running job is ignored.
   - `retry()` refuses a job that still has a running task (5.3), so a retry submit is never silently dropped. This guard is defensive: because of the next rule, a `failed` job normally has no unfinished task. Its test injects a fake pending task.
   - A task's final state change is its very last action, with no `await` after it.
   - As a safety net, the retry cron resubmits any `queued` job that has no running task (5.3), so a job can never be stuck in `queued`.
3. **At most `MAX_CONCURRENT_JOBS` jobs run at once.**
   - Each job's task first waits on an `asyncio.Semaphore(MAX_CONCURRENT_JOBS)`.
   - **Full runs:** while waiting, the job stays `queued`, shown as "Waiting". Once the task gets a slot, the job switches to `running`.
   - **Step-6-only runs** (startup, cron): the job keeps its current state while waiting.
   - Every submit goes through this limit: publish, retry, the cron and startup.
   - A task that is waiting for a slot still counts as a task, so the job is not submitted twice.

## 4. The job

### 4.1 Job record

Saved as `/data/jobs/{documentId}.json` after every change. There is one job per document (D5), and `documentId` is already restricted to safe file names (7.2). Each save writes `{documentId}.json.tmp`, fsyncs it, then renames it over the old file. Startup first deletes leftover `*.json.tmp` files, then loads only files whose name ends in `.json`. `.tmp` and `.corrupt-*` files are never parsed as jobs.

| Field | Meaning |
|---|---|
| `id` | Random id; also the vendor idempotency key |
| `request_id` | The `requestId` of the call that created the job (display only) |
| `document_id` | From the request |
| `state` | `queued`, `running`, `done`, `failed` |
| `error` | Reason shown to users: why it failed, or what it is waiting for |
| `created_at`, `updated_at` | UTC timestamps |
| `filename`, `content_type` | From the Source metadata |
| `archive_path` | Checkpoint of step 2. Relative to the archive root, e.g. `doc-1/<sha256>/doc-1.txt` |
| `sha256` | Checkpoint of step 2 |
| `archive_linked` | Checkpoint of step 3 |
| `source_deleted` | Checkpoint of step 4 |
| `vendor_attempts` | Total delivery attempts; never reset |
| `vendor_receipt` | Checkpoint of step 5 |
| `outcome` | `Published` or `Error`, set after steps 1–5 |
| `outcome_message` | The reason for an `Error`, max 500 chars |
| `status_reported` | Checkpoint of step 6 |
| `auto_retries` | Automatic retries of steps 1–5 used so far |

### 4.2 Steps

| # | Step | Sets |
|---|---|---|
| 1 | Read the metadata (`GET /documents/{id}`) and the bytes (`GET /documents/{id}/content`) from Source. | — |
| 2 | Archive the file to `{archive root}/{documentId}/{sha256}/{safe filename}`. If the cleaned filename is empty, use `document`. Write a `.partial` file, fsync it, rename it, then fsync the folder (skipped on Windows). Read the file back and compare its sha256. If the same file already exists with the right hash, keep it. | `archive_path`, `sha256`, `filename`, `content_type` |
| 3 | `PUT /documents/{id}/archive-link` with `{uri: "archive://{archive_path}", sha256}`. | `archive_linked` |
| 4 | Verify the archive copy's sha256 again, then `DELETE /documents/{id}`. | `source_deleted` |
| 5 | Read the archive copy from disk and check its sha256 against `sha256`. If it doesn't match or the file is missing, fail with "Archive copy damaged or missing after the original was deleted; manual action needed". Otherwise `POST /files` to Vendor with `Idempotency-Key: {job id}`. | `vendor_attempts`, `vendor_receipt` |
| 6 | `PUT /documents/{id}/status` with `{status: outcome, message: outcome_message}`. | `status_reported` |

**Where a run starts:**
- If `outcome` is already set, the run does only step 6.
- Steps 1 and 2 share one checkpoint, `archive_path`. When it is set, both are skipped.
- Steps 3, 4 and 5 are each skipped when their own checkpoint is set.

**Checking an existing archive copy before the delete.** If the run finds `archive_path` set but `source_deleted` not set, it first verifies the archive copy:
- **Hash matches:** the checkpoints stay, and steps 1–2 are skipped.
- **Hash doesn't match, or the file is missing:** the run first reads the Source metadata.
  - **The document is not deleted:** the archive checkpoints (`archive_path`, `sha256`, `filename`, `content_type`, `archive_linked`) are cleared, and the run starts at step 1.
  - **The document is already deleted** (a lost delete answer, 5.4): the checkpoints are kept as evidence. The run fails with "Archive copy damaged or missing after the original was deleted; manual action needed".
  - **The metadata read fails** (any error, 404 included): the checkpoints are kept, and the run fails with that error.

This keeps an intact archive copy even when a delete succeeded but its answer was lost (5.4).

After the delete there is no original to fall back on. Step 5 then does its own hash check before sending.

### 4.3 Outcome

- Steps 1–5 run inside one `try`. Success gives the outcome `Published`.
- Any exception (`except Exception`, so cancellation is not caught) gives the outcome `Error`, with the exception text as the message. When the text is empty, the exception's type name is used instead (e.g. `ReadTimeout`). The message is cut to 500 characters.
- The outcome is saved, and step 6 runs.

### 4.4 Finishing a run

**The final change is one change.** The fields `status_reported`, `state` and `error` are always set together, in one synchronous change and one save.

**Step 6 succeeds:**

| Outcome | Result |
|---|---|
| `Published` | State `done`. |
| `Error` | State `failed`. `error` = `outcome_message`. The next cron run retries it while `auto_retries < AUTO_RETRY_MAX`. |

**Step 6 gets 404 (the document doesn't exist in Source):** the status can never be written, so the job ends.
- State `failed`.
- `error` reads "Document not found in Source; the status cannot be written." followed by " {outcome_message}" only when there is one.
- The cron retries it like any failed job, as in the `Error` row above.

This is one of the **two documented exceptions to R4**; the other is an unreadable job file (4.6). The job behaves like any failed job: the Retry button works and automatic retries continue. If the document appears in Source later, a retry succeeds.

**Step 6 fails for any other reason:** the job is *not* ended.
- The state is set to `running`, even for a step-6-only run that started from `queued`.
- `error` reads "Status not written yet: {reason}".
- The next cron run tries the status write again.

These retries have **no limit**, so R4 always holds.

**Outer guard:**
- The whole run is wrapped in a second `try` that catches `Exception`. It does not catch `CancelledError`, so shutdown still works.
- When something unexpected escapes, the error is logged and the job is handled by where the run had got to:
  - **`status_reported` is already true:** `state` is set from `outcome` (`done` for `Published`, `failed` for `Error`), so the job can't stay `running`.
  - **`outcome` is empty:** it is set to `Error` with the exception's message.
  - **Otherwise:** the job is treated like a failed step 6 above, and the next cron run picks it up.
- So a run can never die silently.

### 4.5 States

| State | Meaning | UI label |
|---|---|---|
| `queued` | Created or re-queued; not started yet | Waiting |
| `running` | Steps in progress, or the status is waiting to be written | Running |
| `done` | `Published` written to Source | Done |
| `failed` | Ended with `Error` written to Source, or ended with any outcome whose status could not be written because the document is missing (404, 4.4) | Failed |

### 4.6 Startup

The API does **not** resume unfinished jobs. On startup:

1. **Interrupted jobs:** every job in `queued` or `running` whose `outcome` is empty gets an outcome, and is then submitted for step 6 only:
   - **`vendor_receipt` is set** (the job was killed after delivering): the outcome is `Published`.
   - **Otherwise:** the outcome is `Error`, with the message "Interrupted by a service restart".
   - If step 6 succeeds, the job becomes `done`, or `failed` with a Retry button. An automatic retry is planned while `auto_retries < AUTO_RETRY_MAX`.
   - If step 6 fails, the job waits for the next cron run (4.4).
2. **Jobs whose status is still waiting** (state `queued` or `running`, `outcome` set, `status_reported` false) are also submitted for step 6 straight away. `failed` jobs are not touched; the cron and the button handle them. This covers a job killed after its outcome was saved but before step 6.
3. **The retry cron starts.** Its first run is one `AUTO_RETRY_INTERVAL` after startup.

**Unreadable job files:** a job file counts as unreadable when it fails to parse as JSON or fails the model check. Such a file is renamed to `{documentId}.json.corrupt-{UTC timestamp}`, so earlier evidence is never overwritten. The problem is logged, and startup continues.
- **Its document stays blocked,** and the block survives later restarts: every startup adds the document of each `*.json.corrupt-*` file to a blocked set. The document id is the part of the file name before the last `.json`. `POST /publish` for a blocked document answers 409, "The job record for this document is unreadable; manual action needed".
- **Unblocking is manual:** remove the `.corrupt-*` file.
- This prevents a second job from overwriting a real `Published` status, or abandoning an intact archive copy.
- No status is written for that job. This is the second documented exception to R4.

**Shutdown:** running tasks are cancelled and job states are not touched. The next startup handles whatever they left behind.

A retry continues from the saved checkpoints. Examples:
- A job interrupted after step 4 delivers the archive copy on retry.
- A job interrupted during step 5 delivers again with the same idempotency key, so the vendor keeps only one copy.

### 4.7 Why the order is safe

- **The original is deleted only after two hash checks have passed:** one right after writing (step 2) and one right before deleting (step 4).
- **From step 5 on, the archive copy is the only copy.** Delivery always reads the archive copy, never Source, so the vendor receives exactly what we can prove we sent.

## 5. Error handling and retries

### 5.1 Quick retries inside a run

Failures fall into two kinds:

| Kind | Examples | Inside a run |
|---|---|---|
| Temporary | network error, timeout (10 s), HTTP 429, any 5xx | retried |
| Permanent | any other 4xx, archive hash mismatch, our own bugs | not retried; the step fails |

**How temporary failures are retried:**
- Each Source call and each Vendor delivery gets up to 5 attempts.
- The wait before retry *n* (that is, attempt *n*+1) is a random value between 0 and `min(RETRY_MAX_DELAY, RETRY_BASE_DELAY × 2^(n-1))`. With the defaults that is up to 0.5 s, 1 s, 2 s, then 4 s.

**Repeating a step is always harmless:**
- **Read:** the same request, sent again.
- **Link and status:** `PUT`s that set the same value again.
- **Delete:** a soft delete, which also answers 204 when repeated.
- **Delivery:** deduplicated by the vendor using the idempotency key.

### 5.2 What happens at each failure point

| Fails at | Original in Source | Outcome |
|---|---|---|
| Step 1 read (e.g. 404, 410) | untouched | Error |
| Step 2 archive (disk full, hash mismatch) | untouched | Error |
| Step 3 link | untouched | Error |
| Step 4 check or delete | untouched, or deleted with the answer lost (5.4) | Error |
| Step 5, vendor fails 5 times | deleted; archive kept | Error, with the vendor's last answer |
| Step 6 status, 404 | (any) | Job `failed`, status not written; a documented exception to R4 (4.4) |
| Step 6 status, anything else | (any) | Job is set to `running` (also from `queued`); retried by the cron, no limit (4.4) |

### 5.3 Retrying failed jobs

The manual endpoint and the retry cron share one function, `retry(job, automatic)`. It:

1. **Acts only on `failed` jobs that have no running task.** Otherwise it does nothing: the API answers 409, and the cron skips the job.
2. **Clears the old result:** `outcome`, `outcome_message`, `status_reported` and `error`. Checkpoints are kept; 4.2 decides where the run starts.
3. **Counts automatic retries:** if `automatic` is true, it adds 1 to `auto_retries`.
4. **Re-queues the job:** it sets the state to `queued`, saves the job and submits it.

**Every failure can be retried, whatever its cause, including a 404.** A permanent problem simply fails again; after `AUTO_RETRY_MAX` automatic retries, only the button is left.

**Manual retry:**
- Allowed on any `failed` job, even after the automatic limit is reached.
- It does not change `auto_retries`.

**The retry cron:**
- Starts at startup and runs every `AUTO_RETRY_INTERVAL` (3600 s, counted from startup). No retry time is stored per job; each run simply acts on every job that matches a row below.
- **Trade-off:** a job that fails shortly before a run is retried at that run, so the wait can be anywhere from a few seconds up to one hour.
- On each run it acts on:

| Job | Action |
|---|---|
| `failed` and `auto_retries < AUTO_RETRY_MAX` | `retry(job, automatic=True)` |
| `running`, `outcome` set, `status_reported` false, no task running | Submit for step 6 only. Not counted, no limit. |
| `queued` with no task running | Submit again (safety net, see section 3) |

- Each run is wrapped in `try`/`except Exception`, which logs the error and continues. One bad run never stops the cron.
- The runner remembers when the next run is due (`next_cron_at`, kept in memory only) so the API can show it (7.1).

**What Source sees:** each failed run writes `Error`, and a later successful retry changes it to `Published`.

### 5.4 Worked example: a delete whose answer was lost

1. The `DELETE` in step 4 succeeds on Source, but the answer times out, and all 5 attempts fail while Source is down.
2. The outcome is `Error`; `source_deleted` is still false and `archive_path` is set.
3. On retry, the archive copy passes its hash check, so the archive checkpoints are kept.
4. Step 3 is skipped, because `archive_linked` is already set.
5. Step 4 verifies the archive copy and deletes again. The delete answers 204 even though the document is already deleted.
6. Step 5 delivers the archive copy, and step 6 writes `Published`.

The document is not lost.

### 5.5 A failed job blocks a new publish of its document

Because duplicates are checked by document (D5), publishing a document whose job failed returns that failed job and starts nothing. The way out is its Retry button.

## 6. Login

There is one API client (no user management). Its id and secret come from `CLIENT_ID` and `CLIENT_SECRET`; Compose defaults them to `demo-client` / `demo-secret`.

### Flow

1. The user opens `http://localhost:8080` and sees the login form: client id, client secret and a Log in button.
2. The UI sends `POST /oauth/token` with the two values as form fields.
3. **Wrong values:** the form shows "Wrong client id or secret".
4. **Right values:** the UI keeps the token in memory, and switches to the main screen (publish field, documents table and job list).
5. From then on, every API call carries `Authorization: Bearer <token>`.
6. **The token lasts 300 s** (`TOKEN_TTL_SECONDS`). For silent renewal, the UI also keeps the id and secret in memory.
   - When any call gets a 401, the UI fetches a new token once and repeats the call.
   - If the renewal is rejected (wrong id or secret), or the repeated call still gets a 401, the UI goes back to the login form with "Your session has expired. Please log in again."
   - If the renewal fails because the API cannot be reached or answers 5xx, the UI stays logged in. The call returns its 401, and the next call tries to renew again. Callers must therefore treat a 401 while still logged in as a temporary error, not as a logout.
7. **Log out:** the "Log out" button clears the token and the credentials from memory and shows the login form.
8. **Page reload:** nothing is kept in browser storage, so a reload also clears everything and shows the login form.

### Token

- An HS256 JWT, signed with `TOKEN_SECRET`.
- Claims: `sub` = client id, `aud` = `publishing-api`, `iat`, `exp`.

### Checking credentials

- The form is read inside a `try`. A missing field, a non-form body or an unreadable form counts as a wrong credential and gets 401, never 422 or 500.
- The id and secret are compared with `hmac.compare_digest` after encoding both sides to UTF-8 bytes. This keeps the comparison constant-time and accepts any characters.

### Checking the token on protected endpoints

- Our own dependency checks the token before the handler runs. It reads the `Authorization` header itself and does not use FastAPI's `HTTPBearer`, which can answer 403.
- It decodes the token with `algorithms=["HS256"]`, `audience="publishing-api"` and `options={"require": ["sub", "aud", "iat", "exp"]}`.
- All of these get 401 with `WWW-Authenticate: Bearer`:
  - a missing header;
  - a scheme other than `Bearer`;
  - an empty token;
  - a value that isn't a JWT;
  - a bad signature;
  - `alg=none`;
  - an expired token;
  - the wrong audience;
  - a missing claim.
- Any exception while decoding the token becomes 401.
- `/publish` reads its body only after the token check (`await request.body()` inside the handler). An anonymous call with a broken body therefore gets 401, not 422, and creates nothing.

## 7. API endpoints

Errors use FastAPI's shape `{"detail": "<text>"}`. A 422 has a list in `detail`.

### 7.1 Job object

Returned by the job endpoints.

| Field | Type | Example |
|---|---|---|
| `id` | string | `"3f9c…"` |
| `requestId` | string | `"7b0c…"` |
| `documentId` | string | `"doc-1"` |
| `state` | `queued` \| `running` \| `done` \| `failed` | `"failed"` |
| `error` | string \| null | `"Vendor API answered 503"` |
| `createdAt`, `updatedAt` | ISO 8601 UTC | `"2026-11-08T10:00:00Z"` |
| `archivePath` | string \| null | `"doc-1/9a4e…/doc-1.txt"` |
| `vendorAttempts` | int | `5` |
| `vendorReceipt` | string \| null | `null` |
| `statusReported` | bool | `true` |
| `autoRetries` | int | `2` |
| `autoRetryMax` | int (from `AUTO_RETRY_MAX`) | `24` |
| `nextRetryAt` | ISO 8601 UTC \| null | `"2026-11-08T11:00:00Z"`. Not stored; worked out for each answer. It is the next cron run when the cron will act on this job: `failed` with `autoRetries < autoRetryMax`, or a status write still waiting. It is `null` otherwise, and also while the job has a task, because the work is already happening. |

### 7.2 Publishing API (port 8000)

| Endpoint | Token | Request | Status | When | Response body |
|---|---|---|---|---|---|
| `POST /oauth/token` | no | form: `client_id=demo-client&client_secret=demo-secret` | 200 | credentials match | `{"access_token": "eyJ…", "token_type": "Bearer", "expires_in": 300}` |
| | | | 401 | wrong, missing or unreadable | `{"detail": "invalid client credentials"}` |
| `POST /publish` | yes | JSON: `{"documentId": "doc-1", "requestId": "7b0c…"}` | 202 | new job, or existing job for this document | Job object |
| | | | 401 | token problem (6) | `{"detail": "invalid or expired token"}` |
| | | | 409 | this document's job record is unreadable (4.6) | `{"detail": "The job record for this document is unreadable; manual action needed"}` |
| | | | 422 | body invalid; `documentId` must match `^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$`; `requestId` 1–200 chars | `{"detail": [ … ]}`. The handler validates the body with the Pydantic model and raises `RequestValidationError`, so FastAPI returns its usual list. |
| `GET /documents` | yes | — | 200 | Source answered | Source's metadata objects (7.3), unchanged and in Source's order: `[{"id": "doc-1", "filename": "doc-1.txt", "contentType": "text/plain", "size": 46, "deleted": false, "status": "Released", "statusMessage": null, "archiveUri": null, "archiveSha256": null}, …]` |
| | | | 401 | token problem | `{"detail": "…"}` |
| | | | 502 | one attempt, no retries; any transport error, timeout, non-2xx answer, or a body that isn't a JSON list of objects each with a string `id` | `{"detail": "Source API unavailable: <reason>"}` |
| `GET /jobs` | yes | — | 200 | always | `[Job, …]`, newest first |
| | | | 401 | token problem | `{"detail": "…"}` |
| `GET /jobs/{id}` | yes | — | 200 | found | Job object |
| | | | 401 | token problem | `{"detail": "…"}` |
| | | | 404 | unknown id | `{"detail": "job not found"}` |
| `POST /jobs/{id}/retry` | yes | no body | 202 | job was `failed`; now `queued` | Job object |
| | | | 401 | token problem | `{"detail": "…"}` |
| | | | 404 | unknown id | `{"detail": "job not found"}` |
| | | | 409 | job is not `failed`, or its last run is still finishing | `{"detail": "only failed jobs can be retried"}` |
| `GET /health` | no | — | 200 | always | `{"ok": true}` |

**Why `documentId` is restricted:** it becomes a folder name, so the pattern blocks `..` and `../x`. As a second guard, the archive refuses any path that would land outside the archive root.

**CORS:**
- Allowed origins: from `CORS_ORIGINS`, default `http://localhost:8080`.
- Allowed methods: `GET`, `POST`.
- Allowed headers: `Authorization`, `Content-Type`.

### 7.3 Source API stub (port 8001)

**Metadata object:**

```json
{
  "id": "doc-1", "filename": "doc-1.txt", "contentType": "text/plain", "size": 46,
  "deleted": false, "status": "Released", "statusMessage": null,
  "archiveUri": null, "archiveSha256": null
}
```

| Endpoint | Request | Status | When | Response body |
|---|---|---|---|---|
| `GET /documents` | — | 200 | always | `[metadata, …]`: all documents, deleted ones included, in seed order (`doc-1`, `doc-2`, …) |
| `GET /documents/{id}` | — | 200 | found (also when deleted) | metadata |
| | | 404 | unknown id | `{"detail": "document doc-9 not found"}` |
| `GET /documents/{id}/content` | — | 200 | not deleted | raw bytes, `Content-Type` from metadata |
| | | 404 | unknown id | `{"detail": "…"}` |
| | | 410 | deleted | `{"detail": "document doc-1 was deleted"}` |
| `PUT /documents/{id}/archive-link` | JSON: `{"uri": "archive://doc-1/9a4e…/doc-1.txt", "sha256": "9a4e…"}` | 200 | found (also when deleted) | metadata |
| | | 404 | unknown id | `{"detail": "…"}` |
| `DELETE /documents/{id}` | — | 204 | found; also when repeated (soft delete: bytes removed, record kept) | empty |
| | | 404 | unknown id | `{"detail": "…"}` |
| `PUT /documents/{id}/status` | JSON: `{"status": "Published" \| "Error", "message": "…" \| null}` | 200 | found (also when deleted) | metadata |
| | | 404 | unknown id | `{"detail": "…"}` |
| | | 422 | other status value | `{"detail": [ … ]}` |
| `GET /health` | — | 200 | always | `{"ok": true}` |

**Behaviour:**
- **Seeding:** on first start it creates `doc-1` … `doc-20` (`SEED_DOCUMENTS`), each a small text file.
- **Saving:** when `DATA_FILE` is set (Compose: `/data/source.json`), every change is saved there (content base64-encoded) and loaded again on start. Seeding happens only when the file doesn't exist.
- **Tests:** they leave `DATA_FILE` unset, so everything stays in memory.
- **Call log:** every call is recorded as `(service, action, id)` in a shared event log that tests can pass in, so the order of calls across both stubs can be checked.

### 7.4 Vendor API stub (port 8002)

| Endpoint | Request | Status | When | Response body |
|---|---|---|---|---|
| `POST /files` | multipart `file`; header `Idempotency-Key` | 400 | no key | `{"detail": "Idempotency-Key header is required"}` |
| | | 503 | outcome "fail": nothing stored | `{"detail": "vendor temporarily unavailable"}` |
| | | 504 | outcome "fail after store": file stored under the key | `{"detail": "gateway timeout"}` |
| | | 200 | outcome "ok": stored if the key is new, otherwise the first receipt | `{"receiptId": "c71d…", "sha256": "9a4e…"}` |
| `GET /files` | — | 200 | always | `[{"receiptId", "idempotencyKey", "filename", "sha256", "size"}, …]` |
| `GET /health` | — | 200 | always | `{"ok": true}` |

**Order inside `POST /files`:**
1. Check that the key is present.
2. Pick the outcome. Each of "fail" and "fail after store" has a chance of `FAILURE_RATE / 2` (15% each by default).
3. Only after that, look up the key.

So a known key can still get a 503. For a known key, "fail after store" and "ok" keep the first record unchanged; nothing is added to `GET /files`.

**Saving:** when `DATA_FILE` is set (Compose: `/data/vendor.json`), the received files' metadata is saved there and loaded on start. The file bytes are not kept.

**Test hooks:** `create_app(outcomes=...)` takes a fixed list of outcomes; after the list runs out, every call is "ok". `attempts` counts calls.

## 8. Frontend (port 8080)

Vue 3 + Vite + TypeScript, built to static files and served by nginx. The API address is fixed at build time through `VITE_API_URL`, default `http://localhost:8000`.

| Screen part | Behaviour |
|---|---|
| Login | As in section 6. |
| Header | Title and a "Log out" button. |
| Start a publication | As the brief asks: a document-id field and a Publish button. It works even when the documents table can't load, and it lets any id be tried, including unknown ones. The field and the row buttons share the same publish behaviour (below). |
| Documents | A table from `GET /documents`, with columns id, filename, size and Source status. It loads after login and when the user clicks "Refresh". It also reloads whenever a job in the job list changes to `done` or `failed`. Otherwise it is not polled, so it is not live. Each row has a **Publish** button. The button is disabled when the document is `deleted`, or while that row's request is in flight. Each click sends a fresh `requestId` (`crypto.randomUUID()`, with a fallback). API errors are shown above the table, including "Source API unavailable" from a 502. |
| Job list | Polls `GET /jobs` every 2 s, and right after a publish or a retry. Polling stops on logout. Newest first. Columns: started, document, state, details. |

**How each state is shown:**

| State | Shown as |
|---|---|
| Waiting, Running | Spinner. Details: the waiting reason from `error` when there is one, otherwise "Vendor attempts so far: {vendorAttempts}". When `nextRetryAt` is set (a status write still waiting), add "Next try at {time}". |
| Done | Green, with the vendor receipt. |
| Failed | Red, with the reason. A **Retry** button, plus "Next automatic retry at {time} (retry {autoRetries + 1} of {autoRetryMax})" when `nextRetryAt` is set. |

**Waiting states:**
- "Loading documents…" and "Loading jobs…" before the first answer.
- "No jobs yet" when the job list is empty, and "No documents in Source" when the documents table is empty.
- Button labels change while a request is in flight.
- A failed refresh shows an error and keeps the last list.

**Layout:** works at phone width.

## 9. Testing

**Tools:** pytest + pytest-asyncio, run inside Docker with Python 3.12. No local Python is needed.

**How to run:**
- `docker compose run --rm -T tests` runs `pytest` over `api/tests`.
- The image's entrypoint is `pytest`, so extra arguments go straight to it, for example `docker compose run --rm -T tests tests/test_auth.py -v`. The container starts in `/workspace/api`, so paths are relative to `api/`.
- `-T` turns off the TTY, so the command also works in Git Bash on Windows and in CI.
- `docker compose run --rm -T lint` checks all Python code (`api/`, `stubs/`) with ruff, using the rules in `ruff.toml`.

Section 11 describes the `tests` and `lint` services.

**Setup:**
- The API and both stubs run inside the test process, connected with `httpx.ASGITransport`. No other containers and no network are used; everything runs in the test process.
- Each test gets its own temp `/data`.
- Retry waits are 0.
- The vendor gets a fixed list of outcomes.
- Both stubs share one event log.

**Layout and imports:**
- One `api/pytest.ini`, next to the code it tests, with these settings:
  - `pythonpath = . ../stubs` (the API tests load the stub apps in-process)
  - `testpaths = tests`
  - `asyncio_mode = auto`
  - `asyncio_default_fixture_loop_scope = function`
  - `addopts = -p no:cacheprovider --import-mode=importlib`
- The tests import `app.*` (the API package) and the stub modules `source_api` and `vendor_api` directly.
- Tests are imported in `importlib` mode. In this mode the test folders are not on `sys.path`, so shared test helpers live in `api/tests/conftest.py` as fixtures, not in a separate module.

**Test helpers:**
- `publish()`
- `job()`
- `settle()`: waits for all running tasks
- `run_cron_once()`: one run of the retry cron, called directly with no waiting

| File | Covers |
|---|---|
| `api/tests/test_auth.py` **(brief: rejected unauthenticated call)** | <ul><li>Anonymous `/publish` gets 401, with no job created and no Source or Vendor calls.</li><li>Every bad-token case from section 6 gets 401.</li><li>An expired or forged token gets 401.</li><li>A broken body without a token gets 401.</li><li>Token endpoint gets 401 for: wrong values; a missing field; a JSON body; a non-ASCII value.</li><li>A valid login works.</li><li>`/documents`, `/jobs`, `/jobs/{id}` and `/jobs/{id}/retry` without a token get 401.</li><li>A broken body with a token gets 422.</li><li>A `documentId` that tries to leave the archive folder gets 422.</li></ul> |
| `api/tests/test_idempotency.py` **(brief: retry rule, i.e. the caller retries)** | <ul><li>Same `requestId` twice: same job, 1 delivery, 1 delete.</li><li>10 simultaneous duplicates: 1 job.</li><li>Duplicate after the job finished: same job.</li><li>Duplicate after a restart: same job, no new delivery.</li><li>New `requestId` for the same document: same job.</li></ul> |
| `api/tests/test_vendor_failure.py` **(brief: vendor failure)** | <ul><li>Outcomes fail, fail, fail-after-store, ok: `done`, 4 attempts, 1 delivery.</li><li>Fail-after-store twice: 1 delivery.</li><li>5 failures: `failed`, `Error` in Source, archive copy kept, `nextRetryAt` shows the next cron run.</li></ul> |
| `api/tests/test_retry.py` | <ul><li>Manual retry after a vendor failure: `done`, 1 delivery, Source shows `Published`.</li><li>Retry of a job that isn't `failed`: 409.</li><li>Retry while the job's last run is still finishing: 409 (uses an injected fake pending task).</li><li>Retry with a damaged archive after a lost delete answer: checkpoints kept, "manual action needed".</li><li>Damaged archive and a metadata read that fails: checkpoints kept.</li><li>Retry after a lost delete answer (5.4): archive kept, delivered.</li><li>Retry with a damaged archive while the original still exists: re-read from Source.</li><li>A cron run retries every failed job.</li><li>The cron skips jobs at `AUTO_RETRY_MAX`.</li><li>The cron retries a waiting status write without a limit.</li><li>An exception in one run doesn't stop the cron.</li><li>Manual retry and a cron run together: 1 run.</li><li>A `queued` job with no task is resubmitted by the cron.</li><li>`nextRetryAt` shows the next cron run for eligible jobs, and is `null` otherwise or while the job has a task.</li><li>A retry after a vendor failure skips steps 1–4.</li></ul> |
| `api/tests/test_pipeline.py` | <ul><li>Call order across both stubs: get, get_content, archive_link, delete, vendor post, status.</li><li>With `MAX_CONCURRENT_JOBS=2`, 5 publishes run at most 2 at a time, while the others show `queued`.</li><li>The original is kept when archiving fails.</li><li>The original is kept when the archive copy is damaged before the delete.</li><li>An unknown document never reaches the vendor.</li><li>A status write refused with a 5xx or a 4xx other than 404 sets the job to `running`, and the next cron run writes it.</li><li>An unknown document (status write gets 404) ends `failed` with the "cannot be written" message, and Retry works.</li><li>An unexpected exception in step 6 doesn't kill the job.</li><li>A failure after the status was written still leaves the job `done` or `failed`, never `running`.</li><li>An unexpected exception before the outcome is set gives `Error`.</li><li>A damaged or missing archive copy after the delete is never sent to the vendor.</li><li>An empty exception message becomes the type name.</li><li>Long messages are cut to 500 characters.</li></ul> |
| `api/tests/test_startup.py` | <ul><li>Jobs left `queued` or `running` become `failed` with "Interrupted by a service restart", with `Error` written to Source.</li><li>A job interrupted after step 4 delivers the archive copy on retry.</li><li>A job interrupted during step 5 is delivered once on retry.</li><li>A job killed after its outcome was saved but before step 6 has its status written at startup.</li><li>A corrupt job file is set aside, startup continues, and publishing that document answers 409.</li><li>That block still holds after a second restart.</li><li>A second corruption of the same document does not overwrite the first `.corrupt-*` file.</li><li>A leftover `.json.tmp` file is deleted, and is not loaded as a job.</li><li>A step-6-only run from `queued` that fails sets `running`.</li><li>A job killed after delivering, before its outcome was saved, ends `done` with `Published`.</li><li>A `failed` job is not touched at startup.</li></ul> |
| `api/tests/test_documents.py` | <ul><li>`GET /documents` without a token: 401, with no call to Source.</li><li>With a token: Source's metadata objects, unchanged and in order, deleted ones included.</li><li>Each of these gives 502, never 500: Source unreachable; a timeout; a 4xx or 5xx answer; a body that isn't a JSON list; a list item that isn't a valid `Document`.</li><li>Source is called exactly once, with no retries.</li></ul> |
| `api/tests/test_archive.py` | <ul><li>Stable path.</li><li>Storing twice is harmless.</li><li>No `.partial` file is left behind.</li><li>A damaged or missing file is detected.</li><li>Filename cleaning, including the empty → `document` fallback.</li><li>Paths outside the root are refused.</li></ul> |
| `api/tests/test_store.py` | <ul><li>Lookup by document.</li><li>Reload from disk.</li><li>Newest first.</li><li>Atomic file write.</li></ul> |
| `api/tests/test_backoff.py` | <ul><li>Temporary errors are retried until success.</li><li>It gives up after the last attempt.</li><li>Permanent errors are not retried.</li></ul> |

The frontend has no unit tests. CI type-checks and builds it, and it is checked by hand in the browser.

The stubs have no tests of their own. The API tests run against the real stub apps in-process, so the stub behaviour the service relies on is exercised there. Behaviour used only under Compose is checked by hand: `DATA_FILE` saving and loading (restart a stub and confirm its data survives), the random `FAILURE_RATE` outcomes, and the vendor's 400 for a missing key.

## 10. Settings (environment variables of `api`)

| Variable | Default | Meaning |
|---|---|---|
| `CLIENT_ID`, `CLIENT_SECRET` | **required**; Compose: `demo-client`, `demo-secret` | The one API client |
| `TOKEN_SECRET` | **required**; Compose: a local demo value | JWT signing secret |
| `TOKEN_TTL_SECONDS` | 300 | Token lifetime |
| `SOURCE_API_URL`, `VENDOR_API_URL` | `http://source-stub:8001`, `http://vendor-stub:8002` | Upstream addresses |
| `DATA_DIR` | `/data` | Archive and job files |
| `HTTP_TIMEOUT` | 10 | Seconds allowed for each step of an upstream call (connect, read, write) |
| `SOURCE_MAX_ATTEMPTS`, `VENDOR_MAX_ATTEMPTS` | 5, 5 | Quick attempts inside a run |
| `RETRY_BASE_DELAY`, `RETRY_MAX_DELAY` | 0.5, 8 | Backoff, in seconds |
| `AUTO_RETRY_INTERVAL` | 3600 | Seconds between retry cron runs |
| `AUTO_RETRY_MAX` | 24 | Limit for automatic retries of steps 1–5 |
| `MAX_CONCURRENT_JOBS` | 10 | Jobs that may run at the same time |
| `CORS_ORIGINS` | `http://localhost:8080` | Allowed browser origins |

The API refuses to start when a required setting is missing. Tests pass their own values.

Compose supplies the required settings with demo defaults, and the API's own defaults cover the rest, so no `.env` file is needed. `.env.example`, which is committed to git, lists the values worth overriding: `CLIENT_ID`, `CLIENT_SECRET`, `TOKEN_SECRET`, `TOKEN_TTL_SECONDS` (a short value helps test token renewal), and `VENDOR_FAILURE_RATE` (passed to the vendor stub as `FAILURE_RATE`). Copying it to `.env`, which git and Docker ignore, changes them for `docker compose`.

## 11. Docker and CI

### Images

All Dockerfiles live in `docker/`. Every image is built with the repository root as its context, and copies only the files it needs.

- **`docker/api.Dockerfile`:** `python:3.12-slim`, runs as a non-root user, started with `uvicorn app.main:create_app --factory`. The image creates `/data/archive` and `/data/jobs` owned by that user. A new named volume copies that ownership, so the user can write to it.
- **`docker/stubs.Dockerfile`:** one image; Compose runs it twice with different commands and `DATA_FILE` values. The image creates `/data` owned by its non-root user.
- **`docker/web.Dockerfile`:** built with `node:20-alpine`, served by `nginxinc/nginx-unprivileged:1.27-alpine`, which runs as a non-root user on port 8080 (Compose maps `8080:8080`). It falls back to `index.html`, except under `/assets/`, where a missing file is a 404.
- **`docker/tests.Dockerfile`:** `python:3.12-slim`. It installs `api/requirements.txt`, `stubs/requirements.txt` and the test packages from `docker/tests-requirements.txt`. Working folder `/workspace/api`, where `api/pytest.ini` is found, `ENTRYPOINT ["pytest"]`. It sets `PYTHONDONTWRITEBYTECODE=1`, and `pytest.ini` turns off the pytest cache (`-p no:cacheprovider`), so no cache files land in the project folder.

### Compose (`docker-compose.yml`)

- **Containers and volumes:** the four containers and three named volumes from section 3.
- **`.dockerignore`:** one file in the repository root serves every image. It keeps dependencies and caches (`**/node_modules`, `**/__pycache__`, `**/*.pyc`, `**/.pytest_cache`, `**/.venv`), `.git`, `.claude`, `docs`, `web/dist`, every `**/.env*` file except `.env.example`, and `*.docx` out of the build context.
- **Health checks:** `api` and both stubs have a health check; `tests` has none. Each health check runs a short Python one-liner that calls `/health`, because the slim image has no curl.
- **Startup order:** `api` waits for both stubs (`depends_on: condition: service_healthy`), and `web` waits for `api`.
- **`tests` service:**
  - Built from `docker/tests.Dockerfile`, with the project folder mounted at `/workspace`, so code changes are used without a rebuild.
  - It is in the Compose profile `test`, so `docker compose up` does not start it; `docker compose run --rm -T tests` does.
  - It needs no other container, because the tests run everything in-process.
  - Its packages change only when a requirements file changes; then it needs `docker compose build tests`.
- **`lint` service:**
  - Same image and mount as `tests`, but its entrypoint is `ruff check` and its working folder is `/workspace`, so it checks `api/` and `stubs/`.
  - Also in the `test` profile.
- **`ruff.toml`** (repository root):
  - line length 120;
  - rules `E`, `F`, `W`, `B`, `UP`, `SIM` and `I` (import order).
  - `ruff` is pinned in `docker/tests-requirements.txt`.

### CI (`.github/workflows/ci.yml`, GitHub Actions)

1. **Backend:** run `docker compose run --rm -T lint`, then `docker compose run --rm -T tests`, the same commands as on a developer machine. A lint error fails the job.
2. **Frontend (`web/`):** Node 20; `npm ci`; `npm run build`, which type-checks with `vue-tsc` and builds.
3. **Images:** `docker compose build`. It runs only after jobs 1 and 2 pass.

## 12. Build order

The system is built as working slices. Each feature gets its own plan in `docs/superpowers/plans/`, written just before it is built, and follows the project workflow in `CLAUDE.md`.

A feature only uses things built in earlier features. Its tests only cover what exists at that point; the test cases that need a later feature are written in that later feature (the "Deferred tests" column).

| # | Feature | Spec sections | Deferred tests and notes |
|---|---|---|---|
| 1 | Project structure: Docker Compose, Dockerfiles in `docker/` (including the `tests` image and service and `.dockerignore`), an empty Vite + Vue scaffold for `web`, `/health` on `api` and both stubs, the `config` unit, the test setup (`api/pytest.ini`, `conftest.py`, ASGITransport wiring; helpers are added as they are needed) | 3, 9 (setup and layout), 10 (the settings used so far), 11 (images, Compose) | Later features add their own settings to `config`. |
| 2 | Source API stub, with the shared event log | 7.3 | No tests of its own, an exception to the CLAUDE.md test-first flow approved by the owner. Checked by hand with `curl` under Compose, and exercised by the API tests from feature 5 on. |
| 3 | Vendor API stub | 7.4 | No tests of its own, an exception to the CLAUDE.md test-first flow approved by the owner. Checked by hand with `curl` under Compose, and exercised by the API tests from feature 7 on. |
| 4 | Login: token endpoint, token check, CORS, login page | 6, 7.2, 8 | `test_auth.py` here covers the token endpoint and the token check. Its cases for `/documents` are written in feature 5. Its cases for `/publish` (anonymous call, broken bodies, escaping id), `/jobs` and `/jobs/{id}` are written in feature 6, including the brief's required rejected-call test. Its case for `/jobs/{id}/retry` is written in feature 10. |
| 5 | Document list: `GET /documents` and the documents table | D14, 7.2, 8 | The table's Publish buttons are added in feature 6. |
| 6 | Publish: the document-id field and the row Publish buttons, steps 1–4, archive, job store, duplicates, job list, Job object, concurrency limit (test in `test_pipeline.py`). Any failure in steps 1–4 sets `failed` with the error. | 1 (R2, R3), 2 (D5), 3, 4.1–4.3, 5.5, 7.1 (without the retry fields), 7.2, 8 | No status write yet. The `test_idempotency.py` "1 delivery" checks are written in feature 7; "duplicate after a restart" in feature 9. In `test_pipeline.py`, the call-order test grows with features 7 and 8. |
| 7 | Vendor delivery (step 5); quick retries for both Source and Vendor calls | 4.2, 5.1 | In `test_vendor_failure.py`, "Error in Source" is written in feature 8, and "`nextRetryAt` shows the next cron run" in feature 11. |
| 8 | Status write-back (step 6); the job completes; final-state rules and outer guard | 4.3–4.5, 5.2 | A failed status write leaves the job `running`; writing it again comes with the cron, and its test is written in feature 11. The "404, then Retry works" test is written in feature 10. |
| 9 | Restart handling: interrupted jobs, corrupt and temp job files, shutdown | 4.1, 4.6 | The "delivered on retry" tests for interrupted jobs are written in feature 10. |
| 10 | Retry button and `retry()` | 3 (rule 2), 4.2 (checking an existing archive copy), 5.3, 5.4, 7.2, 8 | Also adds the deferred tests from features 4, 8 and 9. "Manual retry and a cron run together" is written in feature 11. |
| 11 | Retry cron, `nextRetryAt`, `autoRetries`, `autoRetryMax` | 5.3, 7.1, 8 ("Next automatic retry"), 10 (`AUTO_RETRY_*`) | Writes the cron tests in `test_retry.py`, and adds the deferred tests from features 7, 8 and 10. |
| 12 | CI | 11 (CI) | — |

## 13. Local environment

- **Docker Desktop** must be running. It is needed for the services and for the tests, because the tests run in the `tests` container.
- **No local Python.** The machine's Python 3.8 is not used.
- **Node 20** (already installed) is used for frontend development and builds outside Docker.
- **The user does all git operations.**
