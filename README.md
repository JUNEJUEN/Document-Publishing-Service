# Document Publishing Service

## Table of Contents
- [AI Usage](#ai-usage)
- [I. Getting started](#i-getting-started)
  - [Start the service](#start-the-service)
  - [Open the portal to publish documents](#open-the-portal-to-publish-documents)
  - [Reset the test data](#reset-the-test-data)
  - [Run API Tests](#run-api-tests)
  - [Run code style check](#run-code-style-check)
- [II. Decisions I made](#ii-decisions-i-made)
  - [Tech Stack](#tech-stack)
  - [API Design](#api-design)
  - [Authentication Flow](#authentication-flow)
  - [Running a Job Flow](#running-a-job-flow)
  - [Error Handling](#error-handling)
- [III. What I would change for real production use](#iii-what-i-would-change-for-real-production-use)
  - [Authentication between frontend and backend](#authentication-between-frontend-and-backend)
  - [Each job should be handled by a lambda / worker queue](#each-job-should-be-handled-by-a-lambda--worker-queue)
  - [Cloud storage and managed database](#cloud-storage-and-managed-database)
  - [Dedicated Cron/Scheduler service](#dedicated-cronscheduler-service)

## AI Usage
AI Tool : Claude
- **Design & Planning**: I provided the task requirements and my draft design ideas. The AI reviewed them and helped complete the final design.
- **Implementation Plan**: I created a draft step-by-step plan. The AI reviewed it and completed the detailed technical plan.
- **Coding**: The AI wrote the code based on the approved implementation plan.
- **Review & Testing**: I performed the code review and did manual checks to ensure everything works correctly and meets the requirements.

The AI worked based on CLAUDE.MD

## I. Getting started
### Start the service
``` 
docker compose up -d --wait --build
```
### Open the portal to publish documents
http://localhost:8080/

Login with :
- Client ID = demo-client
- Client Secret = demo-secret

You can set up the Client ID and Client Secret in .env (Copy .env.example)

### Reset the test data
Publishing deletes documents from the Source stub. To start again with a fresh set of 100 documents, remove the data volumes and start the service again:
```
docker compose down -v
docker compose up -d --wait --build
```
This also clears the job history, archive and Vendor deliveries, so all services start clean together.

The number of test documents of source stub could be set throuh SEED_DOCUMENTS

### Run API Tests
``` 
docker compose run --rm -T tests
```

### Run code style check
``` 
docker compose run --rm -T lint
```

## II. Decisions I made
### Tech Stack
- Backend: Python 3.12 + FastAPI.
- Frontend: Vue 3 + TypeScript + Vite, served by Nginx.
- Storage: JSON files on local disk (one file per job) plus local archive folder, no external database needed.
- Testing & Tooling: Pydantic, Pytest, Ruff, Docker Compose.

### API Design

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/oauth/token` | No | Exchange client ID and secret for a Bearer JWT|
| `POST` | `/publish` | Yes | Start a publish job (or return existing job for document)|
| `GET` | `/documents` | Yes | Get the document list from Source CRM|
| `GET` | `/jobs` | Yes | List all jobs, newest first |
| `GET` | `/jobs/{id}` | Yes | Get current status and details for one job|
| `POST` | `/jobs/{id}/retry` | Yes | Manually retry a failed job |

### Authentication Flow
1. User logs in with `Client ID` and `Client Secret`.
2. Backend returns a short-lived Bearer JWT (valid for 5 minutes).
3. Frontend keeps the token and credentials in memory only (no cookies or local storage).
4. Every API call sends `Authorization: Bearer <token>`.
5. On a `401 Unauthorized` response, the frontend automatically requests a new token in the background and retries the call.

### Running a Job Flow 
Each job runs in the background as its own asyncio task, so the API replies right away instead of waiting for the job to finish. An `asyncio.Semaphore` lets at most 10 jobs run at once (set by `MAX_CONCURRENT_JOBS`); extra jobs wait as "Waiting" until a slot frees up, so a burst of requests can't overload the server or flood the Source CRM and Vendor APIs.

1. **Read**: Download document metadata and content from Source CRM.
2. **Archive**: Save file to /data/archive/{docId}/{sha256}/{filename} and verify its **SHA-256 hash** to confirm the file was saved without disk corruption.
3. **Link**: Save the archive link back to Source CRM.
4. **Delete**: Verify the archive hash again before deleting the CRM original to ensure the original is never deleted without a safe backup.
5. **Deliver**: Read and verify the archive hash one last time before sending it to Vendor (with Idempotency-Key) to ensure we deliver the exact file we archived.
6. **Report**: Update the final status (Published or Error) in Source CRM.   

### Error Handling
1. **Quick retry inside a run:**
   Temporary errors (network issues, 10s timeouts, 429, 5xx) are automatically retried up to 3 times using exponential backoff with jitter. Permanent errors (4xx, hash mismatch) fail immediately.
2. **When system crashes or restarts:**
   On startup, before taking any requests, the system finds every job that was still in progress and gives it a final result:
   - If the Vendor already received the file, the job is marked **Published** (`done`).
   - Otherwise, it is marked **Error** with `"Interrupted by a service restart"` (`failed`).

   This result is then written back to Source CRM. The system does **not** continue the interrupted work on its own. A failed job only runs again when a user clicks **Retry**, and it picks up from its saved checkpoints instead of starting over.
3. **Manual Retry:**
   Failed jobs show a **Retry** button in the UI (`POST /jobs/{id}/retry`), allowing manual re-execution from the last valid checkpoint. A retry skips every step whose checkpoint is already saved:

   | Checkpoint saved | Step it skips |
   |---|---|
   | `archive_path` (with `sha256`) | 1 Read + 2 Archive |
   | `archive_linked = True` | 3 Link |
   | `source_deleted = True` | 4 Delete |
   | `vendor_receipt` | 5 Deliver |

   Step 6 (Report) always runs again so Source CRM gets the new outcome.

## III. What I would change for real production use

### Authentication between frontend and backend
Currently, the frontend holds the raw client secret in browser memory to silently renew expired access tokens.

In production, we should replace in-memory client credentials renewal with standard OAuth2 Authorization Code flow with PKCE, or use secure, HTTP-only session cookies and refresh tokens.

### Each job should be handled by a lambda / worker queue
Move background jobs out of the FastAPI process into a distributed message queue (such as AWS SQS + Lambda, or Redis + Celery/ARQ) so the API server does not block or drop tasks during traffic spikes.

### Cloud storage and managed database
Replace local disk storage (`/data/jobs/` and `/data/archive/`) with cloud object storage (like AWS S3 with Object Lock for immutable archives) and a managed database (PostgreSQL or DynamoDB) for concurrent state management.

### Dedicated Cron/Scheduler service
Currently, there is no cron job or automated scheduler running in the system. If a job fails, it stays in the failed state and will not run again on its own. Retrying a failed job currently depends entirely on a user manually clicking the Retry button in the web portal.

In production, we should add a managed scheduler (like AWS EventBridge or Kubernetes CronJobs) to automatically find and retry eligible failed jobs on a regular schedule.
