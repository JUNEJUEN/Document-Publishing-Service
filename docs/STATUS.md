# Project status

Read this first when picking the project up in a new session. It says where things stand, what's left, and how to check and work on the project.

**Which document to trust:** the code and the spec ([2026-11-08-document-publishing-service.md](specs/2026-11-08-document-publishing-service.md)) describe how things work now. Plans record what was built at the time; some have a "Later change" note, and their embedded code may be out of date. The working rules are in [CLAUDE.md](../CLAUDE.md).

## Done

Feature numbers follow the spec's build order (section 12).

| Feature | What it delivers | Plan |
|---|---|---|
| 1 | Project structure: Docker Compose, Dockerfiles, test setup | [feature-01](plans/2026-11-08-feature-01-project-structure.md) |
| 2, 3 | Source and Vendor stubs | [feature-02](plans/2026-11-08-feature-02-source-stub.md), [feature-03](plans/2026-11-08-feature-03-vendor-stub.md) |
| 4 | Login: token endpoint, token check, login page | [feature-04](plans/2026-11-08-feature-04-login.md) |
| 5 | `GET /documents` | [feature-05](plans/2026-11-08-feature-05-document-list.md) |
| — | Routers, `Document` model, ruff | [api-structure-and-lint](plans/2026-11-08-api-structure-and-lint.md) |
| 6 | Publish: steps 1–4, archive, job store, duplicates, job list | [feature-06](plans/2026-11-08-feature-06-publish.md) |
| 7 | Vendor delivery (step 5) and quick retries (3 attempts) | [feature-07](plans/2026-11-08-feature-07-vendor-delivery.md) |
| 8 | Status write-back (step 6), final states, outer guard | [feature-08](plans/2026-11-08-feature-08-status-write-back.md) |
| 9 | Restart handling: interrupted jobs, damaged and temp job files | [feature-09](plans/2026-11-08-feature-09-restart-handling.md) |
| 10 | Retry button: re-run a failed job from its checkpoints | [feature-10](plans/2026-11-08-feature-10-retry-button.md) |
| — | `canRetry`: Retry also writes a status Source refused earlier | [retry-owed-status](plans/2026-11-08-retry-owed-status.md) |
| 12 | CI on GitHub Actions: lint, tests, web build, images | [feature-12](plans/2026-11-08-feature-12-ci.md) |

Also built outside the numbered features:
- **One job list with a type-or-pick publish field and "Publish all"**, which replaced the documents table. The picker is described in [document-picker.md](specs/2026-11-08-document-picker.md).
- **Demo settings:** 100 seeded documents, and the Vendor stub failing 60% of calls under Compose. The brief's rate is 30%: to use it, set `VENDOR_FAILURE_RATE=0.3` in `.env` (`.env.example` has 0.6) and run `docker compose up -d` again.

## Skipped

**Feature 11: hourly retry cron** (`nextRetryAt`, `autoRetries`, `AUTO_RETRY_INTERVAL`, `AUTO_RETRY_MAX`). It was left out to save time.
- **Without it:** failed jobs, and jobs whose status Source refused, are retried only by the Retry button.
- **Already designed:** the spec's D9 and sections 5.3, 7.1, 8 and 10 describe it, and section 12 lists the tests deferred to it from features 7, 8 and 10.

## Not started

**Feature 6b: a session that survives a page reload.** It was numbered 6b in the plans; it isn't in spec section 12. Today a reload logs the user out, because the token lives only in memory. The design was a refresh token in an `HttpOnly` cookie, with `POST /oauth/refresh` and `POST /oauth/logout`. Its design and plan files are no longer in the repo. If it's picked up again, its plan review raised these points:
1. **Medium:** after a logout, `authFetch` can send an empty token, get a 401, and refresh the session back to life. Guard: return early when the sent token was empty.
2. **Low:** the access token issued at login must not outlive the refresh token when `REFRESH_TTL_SECONDS` is below `TOKEN_TTL_SECONDS`.
3. **Low:** don't copy the "Cannot reach the API" text; export it from `http.ts`.
4. **Nit:** write the logout endpoint as `async def logout(response: Response, …) -> None` with `status_code=204` in the decorator.

## Open items from the final review

Not fixed yet, from most to least important:
1. **A possible 500 under load:** `job_or_404` (`api/app/routers/jobs.py`) is plain `def`, so FastAPI runs it in a worker thread. Its `JobStore.get` can then iterate the job dict while a publish adds to it ("dictionary changed size during iteration"). Fix: make it `async def`, and the trivial getters in `api/app/dependencies.py` too, to avoid needless thread hops.
2. **The spec still describes the cron as built** in D9, 4.6, 5.3, 7.1 (`autoRetries`, `autoRetryMax`, `nextRetryAt`), 8, 9 and 10, and still mentions the old documents table in places. Mark those parts "not built" and remove the table leftovers.
3. **README:**
   - the brief asks for a "What I knowingly left out" section;
   - typos: "throuh", "CLAUDE.MD", "Login with :";
   - note that only the variables in `.env.example` reach the containers. The other settings in spec section 10 (`MAX_CONCURRENT_JOBS`, `HTTP_TIMEOUT`, the attempts and delays, `CORS_ORIGINS`) can't be set through `.env`;
   - note that the page must be opened at `localhost:8080`, not `127.0.0.1:8080` (CORS).
4. **Small ones:**
   - an `OSError` while saving a job file could put a server path into the error shown to users;
   - run outside Compose, an empty numeric env var (e.g. `TOKEN_TTL_SECONDS=`) stops the API, while empty text values fall back to defaults. Compose's `${…:-default}` hides this;
   - the document field is narrow on desktop;
   - `tsconfig.json` has an unused `"jsx"` option;
   - `env.d.ts` repeats a type reference.

## How to check the project

- **Setup:** Docker Desktop, running; everything runs in containers, so don't use the machine's own Python (3.8). For the web app outside Docker, Node 20 and `(cd web && npm ci)` once.
- **Run:** `docker compose up -d --wait --build`, then open http://localhost:8080 (not `127.0.0.1`) and log in with `demo-client` / `demo-secret`. Fresh data: `docker compose down -v`, which wipes all jobs, archive copies and Vendor deliveries.
- **Checks before handing off:**

```
docker compose run --rm -T lint              # ruff, rules in ruff.toml
docker compose run --rm -T tests             # pytest, all must pass (180 at the time of writing)
(cd web && npm run build)                    # vue-tsc type-check, then Vite build
```

- **One test file:** `docker compose run --rm -T tests tests/test_retry.py`. After changing a requirements file, run `docker compose build tests`.
- **CI** ([ci.yml](../.github/workflows/ci.yml)) runs the same checks on pushes to `main` and on every pull request, then builds the Docker images.

## How to work on it

- **Workflow:** follow [CLAUDE.md](../CLAUDE.md): design → AI review → plan → AI review → tests that fail first → code → tests pass → AI code review, with the owner's sign-off after each review.
- **Git:** the owner does all git writes. Work on the current branch; don't create branches or worktrees.
- **This file:** update it at the end of every task (`CLAUDE.md` rule 8).
