# Feature 1: Project Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Project rule (CLAUDE.md): never run `git add`, `git commit`, `git push`, `git merge` or `git checkout`. Each task ends with a suggested commit message for the user instead.**

**Goal:** A skeleton where `docker compose up --build` starts four containers: `api`, `source-stub`, `vendor-stub` and `web`. Each Python service answers `GET /health`, and `docker compose run --rm -T tests` runs pytest in a Python 3.12 container.

**Architecture:**
- **`api`:** a FastAPI app built by `create_app()` (`api/app/main.py`), with a minimal `config` unit.
- **Stubs:** two single-file FastAPI apps (`stubs/source_api.py`, `stubs/vendor_api.py`), each with `create_app()` and a module-level `app`.
- **`web`:** an empty Vite + Vue 3 + TypeScript app, served by nginx.
- **Tests:** they run in their own image (`tests.Dockerfile`), with the project folder mounted. They test the apps in-process with `httpx.ASGITransport`.

**Tech stack:** Python 3.12, FastAPI 0.115.6, uvicorn 0.34.0, pytest 8.3.4, pytest-asyncio 0.25.0, httpx 0.28.1, Vue 3.5, Vite 6, TypeScript 5.7, nginx 1.27, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-08-document-publishing-service.md`, feature 1 in section 12. It covers sections 3, 9 (setup and layout), 10 (the settings used so far) and 11 (images, Compose).

**Out of scope for this feature:**
- the settings used by later features (auth, URLs, retries), which each feature adds to `config`;
- the stubs' real endpoints and their `DATA_FILE` saving (features 2 and 3);
- CORS and `VITE_API_URL` (feature 4);
- CI (feature 12).

---

## File structure

| File | Responsibility |
|---|---|
| `.gitignore` | Keeps generated files out of git |
| `.dockerignore` | Keeps `node_modules`, `.git`, build output and caches out of the root build context (`tests` image) |
| `pytest.ini` | One pytest config for `api/tests` and `stubs/tests` |
| `requirements-dev.txt` | Test-only packages |
| `tests.Dockerfile` | Image that runs pytest with all the Python packages |
| `docker-compose.yml` | The services `api`, `source-stub`, `vendor-stub`, `web` and `tests`, three volumes, health checks |
| `api/requirements.txt` | API runtime packages |
| `api/Dockerfile` | API image (non-root, owns `/data/archive` and `/data/jobs`) |
| `api/.dockerignore` | Keeps tests and caches out of the API image |
| `api/app/__init__.py` | Marks the package |
| `api/app/config.py` | `Settings`, read from the environment |
| `api/app/main.py` | `create_app()` with `GET /health` |
| `api/tests/conftest.py` | The `settings` and `api` fixtures |
| `api/tests/test_config.py` | Settings tests |
| `api/tests/test_health.py` | API health test |
| `stubs/requirements.txt` | Stub runtime packages |
| `stubs/Dockerfile` | One image for both stubs (non-root, owns `/data`) |
| `stubs/.dockerignore` | Keeps tests and caches out of the stub image |
| `stubs/source_api.py` | Source stub: `create_app()` with `GET /health` |
| `stubs/vendor_api.py` | Vendor stub: `create_app()` with `GET /health` |
| `stubs/tests/test_source_api.py` | Source stub health test |
| `stubs/tests/test_vendor_api.py` | Vendor stub health test |
| `web/package.json`, `package-lock.json` (generated), `tsconfig.json`, `vite.config.ts`, `index.html` | Vite + Vue project |
| `web/src/main.ts`, `web/src/App.vue` | Placeholder app |
| `web/Dockerfile`, `web/nginx.conf`, `web/.dockerignore` | Build with Node, serve with nginx |

Commands are written for Git Bash, run from the repository root `C:\Users\June\Desktop\Fineline Global Task`.

---

### Task 0: Check the tools

- [ ] **Step 1: Docker Desktop is running**

Run: `docker info --format '{{.ServerVersion}}'`
Expected: a version number such as `28.3.2`, not "error during connect". If it fails, ask the user to start Docker Desktop and wait.

- [ ] **Step 2: Node is available**

Run: `node --version && npm --version`
Expected: `v20.x` and `10.x`.

---

### Task 1: Repository files and the `tests` container

**Files:**
- Create: `.gitignore`, `.dockerignore`, `pytest.ini`, `requirements-dev.txt`, `tests.Dockerfile`, `docker-compose.yml` (only the `tests` service for now), `api/requirements.txt`, `stubs/requirements.txt`

- [ ] **Step 1: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
node_modules/
dist/
# the task brief stays out of the deliverable
*.docx
```

- [ ] **Step 2: Write `.dockerignore`**

```
**/node_modules
**/__pycache__
.git
web/dist
*.docx
```

- [ ] **Step 3: Write `pytest.ini`**

```ini
[pytest]
pythonpath = api stubs
testpaths = api/tests stubs/tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
addopts = -p no:cacheprovider
```

- [ ] **Step 4: Write the requirement files**

`requirements-dev.txt`:
```
pytest==8.3.4
pytest-asyncio==0.25.0
httpx==0.28.1
```

`api/requirements.txt`:
```
fastapi==0.115.6
uvicorn[standard]==0.34.0
```

`stubs/requirements.txt`:
```
fastapi==0.115.6
uvicorn[standard]==0.34.0
```

- [ ] **Step 5: Write `tests.Dockerfile`**

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Requirements go to /tmp: /workspace is replaced by the bind mount.
COPY api/requirements.txt /tmp/api-requirements.txt
COPY stubs/requirements.txt /tmp/stubs-requirements.txt
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/api-requirements.txt -r /tmp/stubs-requirements.txt -r /tmp/requirements-dev.txt
WORKDIR /workspace
ENTRYPOINT ["pytest"]
```

- [ ] **Step 6: Write `docker-compose.yml` with only the `tests` service.** The other services are added in Task 5, once their code exists.

```yaml
services:
  tests:
    build:
      context: .
      dockerfile: tests.Dockerfile
    profiles: ["test"]
    volumes:
      - .:/workspace
```

- [ ] **Step 7: Build and run the empty test suite**

Run: `docker compose build tests && docker compose run --rm -T tests`
Expected: the build succeeds, then pytest reports `no tests ran` (exit code 5), with no import or config errors. The paths in `testpaths` don't exist yet, so pytest may also warn about them. That's fine at this step.

Suggested commit: `chore: add test container and repo config`

---

### Task 2: API `config` and `/health`

**Files:**
- Create: `api/tests/conftest.py`, `api/tests/test_config.py`, `api/tests/test_health.py`
- Then create: `api/app/__init__.py`, `api/app/config.py`, `api/app/main.py`

- [ ] **Step 1: Write the failing tests**

`api/tests/conftest.py`:
```python
"""Shared fixtures: the API runs in-process over ASGITransport."""
import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture
async def api(settings):
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client
```

`api/tests/test_config.py`:
```python
from pathlib import Path

from app.config import Settings


def test_data_dir_defaults_to_slash_data(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert Settings.from_env().data_dir == Path("/data")


def test_data_dir_comes_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert Settings.from_env().data_dir == tmp_path
```

`api/tests/test_health.py`:
```python
async def test_health_answers_ok(api):
    response = await api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose run --rm -T tests api/tests -v`
Expected: `ImportError while loading conftest '/workspace/api/tests/conftest.py'` with `ModuleNotFoundError: No module named 'app'` (exit code 4).

- [ ] **Step 3: Implement**

`api/app/__init__.py`: empty file.

`api/app/config.py`:
```python
"""Settings read from environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("/data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(data_dir=Path(os.environ.get("DATA_DIR", "/data")))
```

`api/app/main.py`:
```python
from fastapi import FastAPI

from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Document publishing service")
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `docker compose run --rm -T tests api/tests -v`
Expected: `3 passed`.

Suggested commit: `feat(api): add settings and health endpoint`

---

### Task 3: Stub skeletons with `/health`

**Files:**
- Create: `stubs/tests/test_source_api.py`, `stubs/tests/test_vendor_api.py`
- Then create: `stubs/source_api.py`, `stubs/vendor_api.py`

- [ ] **Step 1: Write the failing tests**

`stubs/tests/test_source_api.py`:
```python
import httpx

import source_api


async def test_health_answers_ok():
    transport = httpx.ASGITransport(app=source_api.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://source") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
```

`stubs/tests/test_vendor_api.py`:
```python
import httpx

import vendor_api


async def test_health_answers_ok():
    transport = httpx.ASGITransport(app=vendor_api.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://vendor") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `docker compose run --rm -T tests stubs/tests -v`
Expected: collection errors with `ModuleNotFoundError: No module named 'source_api'` and `'vendor_api'`.

- [ ] **Step 3: Implement**

`stubs/source_api.py`:
```python
"""Source API stub: the CRM that owns a document until it is published."""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Source API stub")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app


app = create_app()
```

`stubs/vendor_api.py`:
```python
"""Vendor API stub: the external partner that receives published files."""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Vendor API stub")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app


app = create_app()
```

- [ ] **Step 4: Run the whole suite and confirm it passes**

Run: `docker compose run --rm -T tests -v`
Expected: `5 passed` (3 api + 2 stubs).

Suggested commit: `feat(stubs): add source and vendor stub skeletons`

---

### Task 4: Empty web app and its image

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/src/main.ts`, `web/src/App.vue`, `web/Dockerfile`, `web/nginx.conf`, `web/.dockerignore`
- Generated: `web/package-lock.json`

- [ ] **Step 1: Write `web/package.json`**

```json
{
  "name": "publishing-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.5.13"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.2.1",
    "typescript": "~5.7.2",
    "vite": "^6.0.7",
    "vue-tsc": "^2.2.0"
  }
}
```

- [ ] **Step 2: Write `web/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "types": ["vite/client"],
    "strict": true,
    "noEmit": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "jsx": "preserve"
  },
  "include": ["src/**/*.ts", "src/**/*.vue"]
}
```

- [ ] **Step 3: Write `web/vite.config.ts`**

```ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
})
```

- [ ] **Step 4: Write `web/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Document publishing</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 5: Write `web/src/main.ts` and `web/src/App.vue`**

```ts
import { createApp } from 'vue'
import App from './App.vue'

createApp(App).mount('#app')
```

```vue
<template>
  <main>
    <h1>Document publishing</h1>
  </main>
</template>
```

- [ ] **Step 6: Install and build locally**

Run: `(cd web && npm install && npm run build)`
Expected: `vue-tsc` reports no errors, Vite writes `web/dist/`, and `web/package-lock.json` is created.

- [ ] **Step 7: Write `web/Dockerfile`, `web/nginx.conf` and `web/.dockerignore`**

`web/Dockerfile`:
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
```

`web/nginx.conf`:
```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    location / {
        try_files $uri /index.html;
    }
}
```

`web/.dockerignore`:
```
node_modules
dist
```

- [ ] **Step 8: Check that the image builds**

Run: `docker build -t publishing-web-check web && docker image rm publishing-web-check`
Expected: both stages succeed, then the check image is removed. The image is wired into Compose in Task 5.

Suggested commit: `feat(web): add empty Vue app and nginx image`

---

### Task 5: API and stub images, the full Compose file, health checks

**Files:**
- Create: `api/Dockerfile`, `api/.dockerignore`, `stubs/Dockerfile`, `stubs/.dockerignore`
- Modify: `docker-compose.yml` (all services and volumes)

- [ ] **Step 1: Write `api/Dockerfile`**

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# A new named volume copies this ownership, so appuser can write to it.
RUN useradd --create-home appuser \
    && mkdir -p /data/archive /data/jobs \
    && chown -R appuser /data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `api/.dockerignore` and `stubs/.dockerignore`** (same content)

```
tests/
**/__pycache__
```

- [ ] **Step 3: Write `stubs/Dockerfile`**

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY source_api.py vendor_api.py ./
RUN useradd --create-home stubuser && mkdir -p /data && chown stubuser /data
USER stubuser
# docker-compose.yml chooses which stub this container runs.
CMD ["uvicorn", "source_api:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 4: Replace `docker-compose.yml` with the full file**

```yaml
services:
  source-stub:
    build: ./stubs
    command: ["uvicorn", "source_api:app", "--host", "0.0.0.0", "--port", "8001"]
    ports: ["8001:8001"]
    volumes:
      - source-data:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"]
      interval: 5s
      timeout: 3s
      retries: 10

  vendor-stub:
    build: ./stubs
    command: ["uvicorn", "vendor_api:app", "--host", "0.0.0.0", "--port", "8002"]
    ports: ["8002:8002"]
    volumes:
      - vendor-data:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build: ./api
    ports: ["8000:8000"]
    volumes:
      - publishing-data:/data
    depends_on:
      source-stub:
        condition: service_healthy
      vendor-stub:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 5s
      timeout: 3s
      retries: 10

  web:
    build: ./web
    ports: ["8080:80"]
    depends_on:
      api:
        condition: service_healthy

  tests:
    build:
      context: .
      dockerfile: tests.Dockerfile
    profiles: ["test"]
    volumes:
      - .:/workspace

volumes:
  publishing-data:
  source-data:
  vendor-data:
```

- [ ] **Step 5: Start everything with one command and check it**

```bash
docker compose up -d --build
docker compose ps
```
Expected: four services. Within about 30 s, the STATUS column shows `Up … (healthy)` for `api`, `source-stub` and `vendor-stub`, and `Up …` for `web`. Re-run `docker compose ps` until it does. If a service stays unhealthy, check `docker compose logs <service>`.

```bash
curl -s localhost:8000/health; echo
curl -s localhost:8001/health; echo
curl -s localhost:8002/health; echo
```
Expected: `{"ok":true}` three times.

Run: `curl -s localhost:8080 | grep -o '<title>.*</title>'`
Expected: `<title>Document publishing</title>`

Run: `curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/some/deep/link`
Expected: `200` (nginx falls back to `index.html`).

- [ ] **Step 6: Check that the services can write to their volumes as non-root users**

Run: `docker compose exec -T api sh -c 'touch /data/archive/.probe /data/jobs/.probe && rm /data/archive/.probe /data/jobs/.probe && whoami'`
Expected: `appuser`, with no "Permission denied".

Run: `docker compose exec -T source-stub sh -c 'touch /data/.probe && rm /data/.probe && whoami'`
Expected: `stubuser`.

- [ ] **Step 7: Stop and clean up**

Run: `docker compose down`

Suggested commit: `build: add images and full compose setup`

---

### Task 6: Final check and reviews

- [ ] **Step 1: Run the full suite once more**

Run: `docker compose run --rm -T tests`
Expected: `5 passed`.

- [ ] **Step 2: AI code review.** Following CLAUDE.md, dispatch a fresh reviewer agent with only the files listed in "File structure" and spec section 12 (feature 1). Fix what it finds, re-run the tests, and review again until it comes back clean.

- [ ] **Step 3: User review.** Hand the result to the user, with the test output and the `docker compose ps` output captured in Task 5 Step 5, and wait for their sign-off.

Suggested commit (only if the review led to fixes): `fix: address feature 1 review findings`
