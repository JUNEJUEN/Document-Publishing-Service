# Document Publishing Service
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
   Unfinished running jobs are marked as `failed` with the error `"Interrupted by a service restart"`, and the status is reported to Source CRM. Safe checkpoints allow retries to pick up where the job left off without re-doing completed work.
3. **Manual Retry:**
   Failed jobs show a **Retry** button in the UI (`POST /jobs/{id}/retry`), allowing manual re-execution from the last valid checkpoint.

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
