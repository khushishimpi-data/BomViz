# BOMViz

BOMViz is a manufacturing Bill of Materials platform. Upload Excel or CSV BOMs, map columns
to standard fields, validate every row, and import the result into PostgreSQL with full
source traceability. Then explore each BOM in four ways: an indented tree, a flattened
purchasing roll-up, a where-used graph, or a two-panel revision diff.

> **Status: Phase 0 (foundation) complete.** The stack, configuration, logging, health checks,
> migrations baseline, application shells and CI are in place. Product features arrive in
> Phases 1–7 (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#delivery-phases)).

## Stack

| Area | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, React Router, TanStack Query, Tailwind CSS 4, shadcn/ui, Vitest, React Testing Library, MSW, Playwright |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, Celery, uv |
| Infrastructure | PostgreSQL 16, Redis 7, MinIO (S3-compatible; `pgsty/minio` community build, because official images are no longer published), Docker Compose, GitHub Actions |

## Quick start (Docker)

Prerequisites: Docker with Compose v2.

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
docker compose up --build --wait
```

If ports 5432, 6379, 9000, 9001, 8000 or 5173 are already in use on your machine, change the
matching `*_HOST_PORT` values in `.env`.

| Service | URL |
|---|---|
| Web app | <http://localhost:5173> |
| API docs (Swagger) | <http://localhost:8000/api/v1/docs> |
| Readiness | <http://localhost:8000/health/ready> |
| MinIO console | <http://localhost:9001> |

`docker compose up` runs the following, in order:
1. PostgreSQL, Redis and MinIO start.
2. `minio-init` creates the private bucket.
3. `migrate` runs `alembic upgrade head`.
4. `backend`, `worker` and `frontend` start.

Each long-running service has a health check.

## Local development without Docker

Run PostgreSQL, Redis and MinIO from Compose
(`docker compose up -d postgres redis minio minio-init`) and point `backend/.env` at
`localhost` with the host ports from `.env`.

**Backend** (requires [uv](https://docs.astral.sh/uv/)):

```bash
cd backend
uv sync                                   # creates .venv from uv.lock
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
uv run celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
```

**Frontend** (Node 22+). Set `VITE_API_PROXY_TARGET=http://localhost:8000` in `frontend/.env`, then:

```bash
cd frontend
npm ci
npm run dev
```

## Quality commands

| Purpose | Backend (`cd backend`) | Frontend (`cd frontend`) |
|---|---|---|
| Lint | `uv run ruff check .` | `npm run lint` |
| Format check | `uv run ruff format --check .` | `npm run format:check` |
| Typecheck | `uv run mypy` | `npm run typecheck` |
| Unit tests | `uv run pytest -m "not integration"` | `npm test` |
| Integration tests | `uv run pytest -m integration` (Docker: starts Postgres via Testcontainers, or set `TEST_DATABASE_URL`) | none |
| E2E | none | `npx playwright install chromium` once, then `npm run test:e2e` against a running stack (`E2E_BASE_URL`, default `http://localhost:5173`) |
| Build | none | `npm run build` |

CI (`.github/workflows/ci.yml`) runs every one of these. The E2E job runs against the full
Docker Compose stack.

## Environment variables

### Root `.env` (Docker Compose)

| Variable | Purpose |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Database container credentials. Must match `DATABASE_URL`. |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `S3_BUCKET` | Object storage credentials and bucket. Must match the `S3_*` values in `backend/.env`. |
| `*_HOST_PORT` | Host ports published by each service |

### `backend/.env`

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | `local`, `test` or `production`. Production enforces secure settings. |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` | Log verbosity; `json` or `console` output |
| `API_V1_PREFIX` | `/api/v1` | API mount point |
| `CORS_ORIGINS` | none | Comma-separated allowed origins. Not needed when using the Vite proxy. |
| `DATABASE_URL` | **required** | `postgresql+asyncpg://…` |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | `10`, `10` | Connection pool |
| `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | localhost | Redis endpoints |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Run tasks inline (tests only) |
| `JWT_SECRET_KEY` | none | **Required in production**: at least 32 random characters |
| `JWT_ALGORITHM`, `ACCESS_TOKEN_TTL_MINUTES`, `REFRESH_TOKEN_TTL_DAYS` | `HS256`, `15`, `7` | Token settings |
| `REFRESH_COOKIE_SECURE` | `false` | Must be `true` in production |
| `S3_ENDPOINT_URL` | none | Storage endpoint the backend uses. Leave empty for AWS S3. |
| `S3_PUBLIC_ENDPOINT_URL` | none | Host used in signed URLs given to browsers |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET`, `S3_REGION` | none | Storage credentials. These never reach the frontend. |
| `SIGNED_URL_TTL_SECONDS` | `300` | Lifetime of download links |
| `READINESS_TIMEOUT_SECONDS` | `2` | Timeout for each readiness check |

### `frontend/.env`

| Variable | Purpose |
|---|---|
| `VITE_API_PROXY_TARGET` | Where the dev server proxies `/api` and `/health` (`http://backend:8000` in Docker) |

## Repository layout

```
backend/    FastAPI app (app/), Alembic migrations, pytest suite
frontend/   React app (src/), Vitest unit tests, Playwright e2e/
docs/       PRD, architecture, API, database, ADRs
.github/    CI workflow
```

## Documentation

- [Product requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API conventions and contract](docs/API.md). The live OpenAPI is at `/api/v1/docs`.
- [Database, migrations and ERD](docs/DATABASE.md)
- [ADR 0001: hierarchy, MRP roll-up and error-row exclusion](docs/adr/0001-hierarchy-and-rollup.md)
