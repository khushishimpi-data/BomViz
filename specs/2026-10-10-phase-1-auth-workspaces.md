# Spec: Phase 1 — Auth, RBAC, workspaces and products

- **Date:** 2026-10-10
- **Author:** Khushi Shimpi
- **Status:** Done — awaiting user review (2026-10-11)
- **ClickUp task:** n/a

## Context

BOMViz (FastAPI + async SQLAlchemy + Alembic backend in `backend/`, React 19 + Vite + TanStack
Query frontend in `frontend/`) finished Phase 0: config, JSON logging, error envelope, health
checks, Alembic baseline `0001` (citext, pg_trgm), CI. Phase 1 adds authentication, per-workspace
RBAC, workspaces with members, products with revisions, and audit logging — backend and UI.
Read `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/DATABASE.md` before any task.

Decisions made during clarification (do not revisit):

- **Per-workspace roles.** `workspace_members.role` (`ADMIN | ENGINEER | PROCUREMENT |
  PRODUCTION | VIEWER`) governs everything inside a workspace. A user may hold different roles in
  different workspaces. The ERD's `users.role` becomes `users.is_platform_admin boolean` (no
  Phase 1 endpoint uses it; deviation A10).
- **Open signup, add members by email.** Anyone may register. Creating a workspace makes the
  creator its `ADMIN` member and `owner_id`. Workspace ADMINs add *existing* users by email with
  a role. No invitation emails, no mail service.
- **Standard hardening + progressive login throttling (amendment A).** Argon2id; passwords
  12–128 chars; Redis fixed-window rate limits on register, login (per IP) and refresh; failed
  logins tracked per **(email, IP)** pair with **progressive delays** (not a fixed per-account
  lockout); uniform responses whether or not the email exists; refresh-token rotation with reuse
  detection; every auth event audit-logged without sensitive data.
- **Member removal (amendment B).** The UI asks for confirmation showing the member's name, email
  and role. The last workspace ADMIN can never be removed or demoted. Removal deletes only the
  membership row (never the user) and is audited with actor, target user, previous role,
  workspace and timestamp. Historical audit rows and future BOM attribution reference `users`,
  not memberships, so they survive removal; `audit_logs.workspace_id` uses `ON DELETE RESTRICT`.
- **Frontend covers every Phase 1 endpoint:** login/register, silent refresh, protected routes,
  workspace switcher + create, member management, products and revisions.

Project conventions that override generic skill defaults (already published in Phase 0 docs):
lists return `{items, total, limit, offset}` (not `{data, meta}`); primary keys are UUIDs from
`gen_random_uuid()` (not UUIDv7); errors use the existing `AppError` + envelope in
`app/core/exceptions.py`. Resource routes live under `/api/v1`.

**Non-goals:** BOM upload or anything from Phases 2+; password reset / email verification / MFA;
invitations; OAuth/SSO; audit-log read endpoints or UI; platform-admin screens; deleting
workspaces or products; editing products/revisions after creation.

**Machine constraints (local):** RAM is tight. Never run more than two executors in parallel;
never run `npm install` or Docker builds concurrently with each other. Do **not** add frontend npm
dependencies — build UI primitives from Tailwind (no new Radix packages). Frontend commands run in
the container: `docker compose exec -T frontend npm run -s <script>`. Backend commands run
locally: `cd backend && uv run --frozen <cmd>` (only T1 may change `uv.lock`). If
`uv` reports "Waiting to acquire exclusive lock", delete `backend/.venv/.lock`.

## Design reference (all tasks)

### Database (migrations `0002_auth`, `0003_workspaces_products`)

All tables: `id uuid PK DEFAULT gen_random_uuid()`, `created_at`/`updated_at timestamptz NOT NULL
DEFAULT now()` (`updated_at` maintained by SQLAlchemy `onupdate`). Constraint names come from
`app/db/base.py` NAMING_CONVENTION.

- Enum `user_role`: `ADMIN, ENGINEER, PROCUREMENT, PRODUCTION, VIEWER`.
- `users`: `email citext UNIQUE NOT NULL`, `password_hash text NOT NULL`, `full_name text NOT
  NULL`, `is_active bool NOT NULL DEFAULT true`, `is_platform_admin bool NOT NULL DEFAULT false`,
  `last_login_at timestamptz NULL`.
- `login_throttles` (in `0002`): `key_hash text PK` (SHA-256 hex of `lower(email) + "|" + ip`;
  raw email/IP are never stored), `failure_count int NOT NULL DEFAULT 0 CHECK (failure_count >= 0)`,
  `last_failure_at timestamptz NOT NULL`, `blocked_until timestamptz NULL`. No `id`/`created_at`
  (a keyed counter, not an entity). Index on `last_failure_at` for cleanup.
- `refresh_tokens`: `user_id FK users ON DELETE CASCADE`, `family_id uuid NOT NULL` (one family
  per login session), `token_hash text UNIQUE NOT NULL` (SHA-256 hex of the raw token),
  `expires_at timestamptz NOT NULL`, `revoked_at timestamptz NULL`, `replaced_by_id uuid NULL FK
  refresh_tokens ON DELETE SET NULL`. Indexes: `user_id`, `family_id`. (No `updated_at`.)
- `workspaces`: `name text NOT NULL`, `slug citext UNIQUE NOT NULL`, `owner_id FK users ON DELETE
  RESTRICT` (indexed).
- `workspace_members`: `workspace_id FK workspaces ON DELETE CASCADE`, `user_id FK users ON DELETE
  CASCADE` (indexed), `role user_role NOT NULL`; `UNIQUE (workspace_id, user_id)`.
- Enum `product_status`: `DRAFT, ACTIVE, OBSOLETE`. Enum `revision_status`: `DRAFT, RELEASED,
  OBSOLETE`.
- `products`: `workspace_id FK workspaces ON DELETE CASCADE` (indexed), `product_code text NOT
  NULL`, `name text NOT NULL`, `description text NULL`, `status product_status NOT NULL DEFAULT
  'DRAFT'`, `created_by_id FK users ON DELETE SET NULL NULL`; `UNIQUE (workspace_id,
  product_code)`.
- `product_revisions`: `product_id FK products ON DELETE CASCADE` (indexed), `revision_code text
  NOT NULL`, `description text NULL`, `status revision_status NOT NULL DEFAULT 'DRAFT'`,
  `created_by_id FK users ON DELETE SET NULL NULL`; `UNIQUE (product_id, revision_code)`.
  Workspace scoping is via the parent product join (deviation A11: no denormalized
  `workspace_id`).
- `audit_logs` (in `0003`): `workspace_id FK workspaces ON DELETE RESTRICT NULL` (history is
  never cascaded away), `actor_id FK
  users ON DELETE SET NULL NULL`, `action text NOT NULL`, `entity_type text NOT NULL`, `entity_id
  uuid NULL`, `action_metadata jsonb NOT NULL DEFAULT '{}'`, `request_id text NULL`, `created_at`
  only. Indexes: `(workspace_id, created_at DESC)`, `(actor_id, created_at DESC)`.

### Auth flow

- **Access token:** PyJWT HS256 with claims `sub` (user id), `sid` (refresh family id), `iss =
  "bomviz"`, `aud = "bomviz-api"`, `typ = "access"`, `iat`, `exp` (`ACCESS_TOKEN_TTL_MINUTES`).
  Decoding pins `algorithms=[settings.jwt_algorithm]` and requires all claims. Expired → 401
  `TOKEN_EXPIRED`; any other failure → 401 `TOKEN_INVALID`; missing header → 401
  `UNAUTHENTICATED`. Every authenticated request loads the user (must exist and be active) **and**
  checks that the `sid` family still has a non-revoked, unexpired refresh token — so logout and
  reuse detection invalidate access tokens immediately.
- **Refresh token:** `secrets.token_urlsafe(32)`, stored only as SHA-256 hex. Cookie
  `bomviz_refresh`: `HttpOnly`, `Secure=settings.refresh_cookie_secure`, `SameSite=Strict`,
  `Path=/api/v1/auth`, `Max-Age = REFRESH_TOKEN_TTL_DAYS`.
- **Rotation (`POST /auth/refresh`):** look up by hash `FOR UPDATE`. Unknown → 401
  `TOKEN_INVALID`. Already revoked → **reuse**: revoke every refresh token of that user, audit
  `AUTH_REFRESH_REUSED`, clear cookie, 401 `TOKEN_REUSED`. Expired → 401 `TOKEN_EXPIRED`.
  Otherwise revoke it, insert a successor in the same family (`replaced_by_id` set), set cookie,
  return a new access token.
- **Login (amendment A).** Order of checks:
  1. Per-IP rate limit (Redis, 30 per 15 min) → 429 `RATE_LIMITED`.
  2. Load the `login_throttles` row for `key_hash(email, ip)` `FOR UPDATE`. If `last_failure_at`
     is older than `LOGIN_THROTTLE_RESET_HOURS` (24) treat the count as 0. If `blocked_until >
     now()` → 429 `LOGIN_THROTTLED`, message `"Too many sign-in attempts. Please wait and try
     again."`, `Retry-After` = remaining seconds. The password is **not** checked while blocked.
  3. Verify the password (unknown email → verify against a fixed dummy hash for uniform timing).
     Wrong password, unknown email and inactive user all return 401 `INVALID_CREDENTIALS`
     `"Invalid email or password."` and increment the pair's `failure_count` (upsert). Throttling
     applies identically to unknown emails, so neither the 401 nor the 429 reveals whether an
     account exists.
  4. Progressive delay after a failure: `failure_count < LOGIN_FREE_ATTEMPTS` (4) → no delay;
     otherwise `blocked_until = now() + min(LOGIN_BASE_DELAY_SECONDS (30) * 2 ** (failure_count -
     LOGIN_FREE_ATTEMPTS), LOGIN_MAX_DELAY_SECONDS (900))` → 30 s, 60 s, 120 s … capped at 15 min.
  5. Success deletes the pair's throttle row, sets `last_login_at`, rehashes if
     `check_needs_rehash`, opens a new refresh family.
  Throttle rows and failure audits are committed even though the request fails.
- **Register:** `email` (EmailStr, lower-cased), `password` (12–128 chars, must not equal the
  email), `full_name` (1–200 chars, stripped). Duplicate email → 409 `EMAIL_TAKEN` (accepted
  enumeration trade-off, mitigated by rate limit). Returns 201 `UserResponse`; does not log in.
- **Logout:** revoke the presented cookie's whole family if found; always clear cookie; 204;
  idempotent; no auth header required.
- **Rate limits** (Redis `INCR` + `EXPIRE` fixed window, key `rl:<bucket>:<identity>`): register
  5/hour per IP; login 30 per 15 min per IP (separate from the per-(email, IP) throttle above);
  refresh 60 per 15 min per IP. Exceeded → 429 `RATE_LIMITED` with `Retry-After` seconds. If Redis errors, allow the
  request and log `rate_limiter_unavailable` at WARNING (fail-open, availability over strictness).
  Client IP: the direct peer, or — only when the peer is in `TRUSTED_PROXY_IPS` — the right-most untrusted `X-Forwarded-For` entry; IPv6 is keyed by its /64 (review fix F2).

### Authorization

`app/core/permissions.py` is the single policy:

```python
class Permission(StrEnum):
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_MANAGE_MEMBERS = "workspace:manage_members"
    PRODUCT_READ = "product:read"
    PRODUCT_WRITE = "product:write"

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    ADMIN: all, ENGINEER: {WORKSPACE_READ, PRODUCT_READ, PRODUCT_WRITE},
    PROCUREMENT / PRODUCTION / VIEWER: {WORKSPACE_READ, PRODUCT_READ},
}
def has_permission(role: UserRole, permission: Permission) -> bool
```

Workspace-scoped lookups always query `... JOIN workspace_members WHERE user_id = :current_user`.
Not a member (or nonexistent) → 404 `NOT_FOUND`. Member without the permission → 403
`FORBIDDEN`. Products/revisions are resolved through their workspace membership the same way.

### Endpoints (all under `/api/v1`)

| Method | Path | Permission | Success |
|---|---|---|---|
| POST | `/auth/register` | public, rate-limited | 201 `UserResponse` |
| POST | `/auth/login` | public, rate-limited | 200 `{access_token, token_type:"bearer", expires_in, user}` + cookie |
| POST | `/auth/refresh` | cookie, rate-limited | 200 `{access_token, token_type, expires_in}` + rotated cookie |
| POST | `/auth/logout` | cookie | 204 |
| GET | `/auth/me` | authenticated | 200 `MeResponse` = `UserResponse` + `memberships: [{workspace_id, workspace_name, workspace_slug, role}]` |
| POST | `/workspaces` | authenticated | 201 `WorkspaceResponse` (`{id, name, slug, owner_id, role, created_at}`; `role` = caller's role) |
| GET | `/workspaces` | authenticated | 200 page of `WorkspaceResponse` the caller belongs to |
| GET | `/workspaces/{id}` | WORKSPACE_READ | 200 `WorkspaceResponse` |
| GET | `/workspaces/{id}/members` | WORKSPACE_READ | 200 page of `MemberResponse` (`{id, user_id, email, full_name, role, created_at}`) |
| POST | `/workspaces/{id}/members` | WORKSPACE_MANAGE_MEMBERS | 201 `MemberResponse`; body `{email, role}`; unknown email → 404 `USER_NOT_FOUND`; already member → 409 `ALREADY_MEMBER` |
| PATCH | `/workspaces/{id}/members/{member_id}` | WORKSPACE_MANAGE_MEMBERS | 200 `MemberResponse`; body `{role}` |
| DELETE | `/workspaces/{id}/members/{member_id}` | WORKSPACE_MANAGE_MEMBERS | 204 |
| POST | `/workspaces/{id}/products` | PRODUCT_WRITE | 201 `ProductResponse`; duplicate code → 409 `PRODUCT_CODE_TAKEN` |
| GET | `/workspaces/{id}/products` | PRODUCT_READ | 200 page of `ProductResponse`; filter `q` (ILIKE on code/name), `status` |
| GET | `/products/{id}` | PRODUCT_READ | 200 `ProductDetailResponse` = product + `revisions` (newest first) |
| POST | `/products/{id}/revisions` | PRODUCT_WRITE | 201 `RevisionResponse`; duplicate → 409 `REVISION_CODE_TAKEN` |

Pagination: `limit` (default 50, 1–200), `offset` (≥ 0), response `{items, total, limit, offset}`.
Demoting or removing the last ADMIN of a workspace → 409 `LAST_ADMIN`. Workspace create body
`{name (1–100), slug? (^[a-z0-9]+(?:-[a-z0-9]+)*$, 3–48)}`; slug omitted → slugify name (ASCII,
lowercase, hyphens, max 40) and on collision append `-` + 4 random lowercase hex chars (retry up
to 5 times); a supplied slug that collides → 409 `SLUG_TAKEN`. Product create body
`{product_code (1–64, stripped), name (1–200), description? (≤ 2000), status? (default DRAFT)}`.
Revision body `{revision_code (1–32), description?, status? (default DRAFT)}`.

**Audit actions** (`app/services/audit_service.py`, written in the same transaction as the change,
`request_id` from `request_id_ctx`): `AUTH_REGISTERED`, `AUTH_LOGIN_SUCCEEDED`,
`AUTH_LOGIN_FAILED` (actor = user if the email exists, else NULL; metadata `{reason:
"bad_password"|"inactive"|"unknown_user", failure_count}`), `AUTH_LOGIN_THROTTLED` (written when a
failure sets a new `blocked_until`; metadata `{failure_count, delay_seconds}`; actor as above),
`AUTH_LOGIN_BLOCKED_ATTEMPT` (an attempt rejected while blocked; metadata `{retry_after_seconds}`),
`AUTH_REFRESH_REUSED`, `AUTH_LOGGED_OUT`, `WORKSPACE_CREATED`, `MEMBER_ADDED` (metadata
`{target_user_id, role}`), `MEMBER_ROLE_CHANGED` (metadata `{target_user_id, from, to}`),
`MEMBER_REMOVED` (actor_id, workspace_id, entity = membership id, metadata `{target_user_id,
previous_role}`, timestamp = `created_at`), `PRODUCT_CREATED`, `PRODUCT_REVISION_CREATED`.
Audit metadata **never** contains emails, IP addresses, passwords, tokens or throttle key hashes.
Failed-login audits must commit even though the request returns 401/429.

### Backend layout (new files)

`app/models/{__init__,enums,user,refresh_token,workspace,product,audit_log}.py`,
`app/repositories/{__init__,user_repository,refresh_token_repository,workspace_repository,product_repository,audit_repository}.py`,
`app/services/{auth_service,rate_limiter,audit_service,workspace_service,product_service}.py`,
`app/core/{security,permissions}.py`, `app/api/{auth,workspaces,products}.py`,
`app/schemas/{auth,workspace,product,pagination}.py`. Routes stay thin (parse → one service call →
response). Services own transactions' business rules; repositories own queries.

### Frontend layout (new files)

`src/components/ui/{input,label,card,alert,select}.tsx` (Tailwind-only, shadcn styling, no new
deps), `src/lib/auth-session.ts` (in-memory access token store), `src/lib/api-client.ts` (adds
`apiRequest`), `src/features/auth/*`, `src/features/workspaces/*`, `src/features/products/*`.
Routes: `/` public landing (existing HomePage; when signed in, redirect to the active
workspace), `/login`, `/register`, and protected `/workspaces`, `/workspaces/:workspaceId`
(→ products), `/workspaces/:workspaceId/members`, `/workspaces/:workspaceId/products`,
`/workspaces/:workspaceId/products/:productId`. The last-used workspace id is kept in
`localStorage` (try/catch, convenience only).

## Skill routing table

| Work type | Governing skill | Source plugin |
|---|---|---|
| FastAPI routes, Pydantic schemas, error codes | api-conventions | python |
| Cross-cutting API rules (pagination, idempotency, error shape) | api-standard | governance |
| SQLAlchemy models, Alembic migrations, queries | data-conventions | postgres |
| Login, tokens, sessions, RBAC, object-level access | authentication-authorization | security |
| Baseline appsec, tenant isolation, secrets in config | appsec-conventions | security |
| Request validation at the trust boundary | input-validation | security |
| Error paths, what to catch/surface/log | error-handling | developer |
| Log lines and audit events | logging | developer |
| Backend tests (unit + integration) | testing-standard | governance |
| React components, hooks, frontend state | component-standards | react-web |
| Vitest/RTL component tests, Playwright e2e | ui-testing | react-web |
| TypeScript typing | typescript-conventions, type-safety-patterns | typescript |
| Forms, landmarks, labels, focus | html-semantics-and-accessibility | html-css |
| Docs updates | docs-standard | governance |
| Full-diff review | code-reviewer agent | core |
| Verification of finished phase | verifier agent | quality-assurance |

Note: api-conventions/data-conventions mention UUIDv7, `{data, meta}` and `tenant_id`; this
project uses UUID (`gen_random_uuid`), `{items,total,limit,offset}` and `workspace_id` as the
tenant key — follow the project convention and say so in the task report.

## Tasks

### [x] T1 — Backend dependencies and auth settings
- **Files:** `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/core/config.py`, `backend/.env.example`, `backend/tests/unit/test_config.py`
- **Skills:** appsec-conventions, testing-standard
- **Depends on:** none
- **Parallel-safe:** yes (with T12)
- **Do:** `cd backend && uv add "pyjwt>=2.9" "argon2-cffi>=23.1" "email-validator>=2.2"`. Add settings (with defaults): `jwt_issuer="bomviz"`, `jwt_audience="bomviz-api"`, `refresh_cookie_name="bomviz_refresh"`, `refresh_cookie_path="/api/v1/auth"`, `login_free_attempts=4`, `login_base_delay_seconds=30`, `login_max_delay_seconds=900`, `login_throttle_reset_hours=24`, `rate_limit_enabled=True`. Document each new variable in `.env.example` with a one-line comment. Add tests: defaults load; `rate_limit_enabled` can be disabled via env.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_config.py && uv run --frozen mypy` — all pass

### [x] T2 — ORM models and migrations 0002/0003
- **Files:** `backend/app/models/*.py` (see layout), `backend/alembic/env.py` (import `app.models` so metadata is populated), `backend/alembic/versions/0002_auth.py`, `backend/alembic/versions/0003_workspaces_products.py`, `backend/tests/integration/test_database.py`
- **Skills:** data-conventions, testing-standard
- **Depends on:** T1
- **Parallel-safe:** no
- **Do:** Implement exactly the Database section above with SQLAlchemy 2 typed `Mapped[...]` models, Postgres ENUM types (`create_type` handled in migrations; drop them in `downgrade`), `server_default` for defaults. Write migrations by hand (revision ids `"0002"`, `"0003"`), each with a working `downgrade()`. Extend the round-trip test to assert all seven tables exist after upgrade, none after downgrade to base, and that `alembic check` style autogenerate produces no diff (use `alembic.autogenerate.compare_metadata` against the upgraded DB and assert an empty diff).
- **Verify:** `cd backend && uv run --frozen pytest -q -m integration tests/integration/test_database.py` — passes; `uv run --frozen mypy && uv run --frozen ruff check .`

### [x] T3 — Request-scoped DB session and integration test fixtures
- **Files:** `backend/app/db/session.py`, `backend/app/main.py`, `backend/app/core/dependencies.py`, `backend/tests/conftest.py`
- **Skills:** data-conventions, error-handling, testing-standard
- **Depends on:** T2
- **Parallel-safe:** no
- **Do:** Add `build_sessionmaker(engine) -> async_sessionmaker[AsyncSession]` (`expire_on_commit=False`); store on `app.state.sessionmaker` in lifespan. Add dependency `get_db_session` yielding a session inside `async with session.begin()` (commit on success, rollback on exception) and `DbSessionDep`. Services that must persist despite raising (failed-login audit) use `await session.commit()` explicitly before raising — document this in the dependency docstring. In `conftest.py` add fixtures: `migrated_database_url` (session scope; runs `alembic upgrade head` once on `database_url`), `db_settings`, `db_app` (create_app with DB settings, `rate_limit_enabled=False` by default), `db_client` (httpx ASGITransport **running the lifespan** via `asgi-lifespan`-free approach: use `app.router.lifespan_context(app)`), and an autouse-for-integration `truncate_tables` that `TRUNCATE ... RESTART IDENTITY CASCADE` all Phase 1 tables after each integration test. Add `tests/integration/test_session.py`: a route-free test that a failing unit of work rolls back.
- **Verify:** `cd backend && uv run --frozen pytest -q` (unit + integration) — passes

### [x] T4 — Security primitives
- **Files:** `backend/app/core/security.py`, `backend/tests/unit/test_security.py`
- **Skills:** authentication-authorization, appsec-conventions, testing-standard
- **Depends on:** T1
- **Parallel-safe:** yes (with T5, T9)
- **Do:** Functions: `hash_password(pw) -> str`, `verify_password(hash, pw) -> bool` (False on mismatch/invalid hash, never raises for wrong password), `password_needs_rehash(hash) -> bool`, `verify_dummy_password(pw) -> None` (verifies against a module-level precomputed hash), `create_access_token(settings, user_id, session_id, now=None) -> tuple[str, int]` (token, expires_in seconds), `decode_access_token(settings, token) -> AccessTokenClaims` raising `TokenExpiredError`/`TokenInvalidError` (subclasses of `AppError`, 401, codes `TOKEN_EXPIRED`/`TOKEN_INVALID`), `new_refresh_token() -> str`, `hash_refresh_token(raw) -> str` (sha256 hex). Use `argon2.PasswordHasher()` defaults. Tests: round trips; wrong password False; expired token; wrong audience/issuer/`typ`; `alg: none` token rejected; token signed with another key rejected; missing claim rejected.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_security.py && uv run --frozen mypy`

### [x] T5 — Redis rate limiter
- **Files:** `backend/app/services/rate_limiter.py`, `backend/app/core/dependencies.py` (add `get_rate_limiter`), `backend/tests/unit/test_rate_limiter.py`, `backend/tests/fakes.py`
- **Skills:** error-handling, logging, testing-standard
- **Depends on:** T1
- **Parallel-safe:** yes (with T4, T9) — coordinate: T5 owns the `get_rate_limiter` addition to `dependencies.py`; run after T3 if T3 is still editing that file
- **Do:** `class RateLimiter` with `async def hit(bucket: str, identity: str, limit: int, window_seconds: int) -> None` raising `AppError(429, "RATE_LIMITED", "Too many attempts. Please wait and try again.")` with `retry_after` exposed so the route can set `Retry-After` (add `headers: dict[str,str]` support to `AppError` + handler in `app/core/exceptions.py` if absent). Use a pipeline `INCR` + `EXPIRE NX`; identity is hashed (sha256) in the key so emails are not stored in Redis. Disabled when `settings.rate_limit_enabled` is False. Redis errors → allow + WARNING log `rate_limiter_unavailable`. `tests/fakes.py` provides `FakeRedis` (in-memory incr/expire/ttl/pipeline). Tests: under limit passes, over limit 429 with Retry-After, window reset, disabled, Redis error fails open.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_rate_limiter.py tests/unit/test_error_handling.py && uv run --frozen mypy`

### [x] T6 — Audit repository and service
- **Files:** `backend/app/repositories/__init__.py`, `backend/app/repositories/audit_repository.py`, `backend/app/services/audit_service.py`, `backend/tests/integration/test_audit_service.py`
- **Skills:** data-conventions, logging, testing-standard
- **Depends on:** T3
- **Parallel-safe:** yes (with T7 only after T7 starts from a finished T6 — run sequentially)
- **Do:** `AuditAction(StrEnum)` with every action listed above. `AuditService(session).record(action, *, entity_type, entity_id=None, actor_id=None, workspace_id=None, metadata=None)` adds an `AuditLog` row with `request_id_ctx.get()`. Reject metadata keys named like secrets (`password`, `token`, `email`) with `ValueError` (programming error guard). Integration test: record persists all fields; forbidden keys raise.
- **Verify:** `cd backend && uv run --frozen pytest -q -m integration tests/integration/test_audit_service.py`

### [x] T7 — Auth service and repositories
- **Files:** `backend/app/repositories/user_repository.py`, `backend/app/repositories/refresh_token_repository.py`, `backend/app/repositories/login_throttle_repository.py`, `backend/app/services/login_throttle.py` (pure delay function + service), `backend/app/services/auth_service.py`, `backend/tests/unit/test_login_throttle.py`, `backend/tests/integration/test_auth_service.py`
- **Skills:** authentication-authorization, data-conventions, error-handling, testing-standard
- **Depends on:** T4, T6
- **Parallel-safe:** no
- **Do:** Implement register, login (progressive (email, IP) throttle per amendment A, dummy verify, rehash, new family; `login(email, password, client_ip)`), refresh (rotation, reuse detection revoking all user tokens, `SELECT ... FOR UPDATE`), logout (revoke family), `authenticate_access_token(token) -> User` (claims + active user + live family), `get_me(user) -> MeResult`. Errors as `AppError` subclasses with codes from the Auth flow section; add any new codes to the table in `docs/API.md` during T17 (list them in your report). Clock injectable (`now: Callable[[], datetime]`) for throttle tests. Unit tests: the delay schedule (0 for the first 3 failures, then 30/60/120… capped at 900). Integration tests cover: register + duplicate; login success; 4th failure → next attempt `LOGIN_THROTTLED` with correct `Retry-After`, correct password while blocked still 429, success after the window and throttle row deleted; failures from IP A do not throttle the same email from IP B; unknown email gets identical 401/429 behaviour and bodies to a known one; stale counts reset after 24 h; no `login_throttles` column or audit metadata contains the raw email or IP; refresh rotates (old hash revoked, new token works); reuse of rotated token → `TOKEN_REUSED` and all tokens revoked and access token for that family rejected; logout invalidates the access token; inactive user rejected; failed-login audit rows exist.
- **Verify:** `cd backend && uv run --frozen pytest -q -m integration tests/integration/test_auth_service.py && uv run --frozen mypy`

### [x] T8 — Auth routes, schemas and current-user dependency
- **Files:** `backend/app/schemas/auth.py`, `backend/app/schemas/pagination.py`, `backend/app/api/auth.py`, `backend/app/core/dependencies.py`, `backend/app/main.py`, `backend/tests/integration/test_auth_api.py`
- **Skills:** api-conventions, api-standard, authentication-authorization, input-validation, testing-standard
- **Depends on:** T5, T7
- **Parallel-safe:** no
- **Do:** Routers mounted at `settings.api_v1_prefix`. `CurrentUserDep` reads `Authorization: Bearer`, calls `authenticate_access_token`. Cookie set/clear helpers use the settings above. Apply rate limits per the Auth flow section inside the route (bucket names `register`, `login_ip`, `refresh`); pass `request.client.host` to `login`. `LOGIN_THROTTLED` responses carry `Retry-After`. `Page[T]` generic schema `{items, total, limit, offset}` and `PaginationParams` dependency in `schemas/pagination.py`. Document responses (`responses=` with `ErrorResponse`) so OpenAPI lists 401/409/429. API tests: each endpoint happy path; register validation errors (short password, bad email); login sets cookie with HttpOnly, SameSite=Strict, Path=/api/v1/auth; refresh without cookie → 401; rotated cookie works and old one → `TOKEN_REUSED`; `/auth/me` without token → 401 `UNAUTHENTICATED`, with garbage → `TOKEN_INVALID`; 429 + `Retry-After` when limits enabled (use `FakeRedis`); response bodies never contain `password_hash`.
- **Verify:** `cd backend && uv run --frozen pytest -q && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy`

### [x] T9 — Permission policy
- **Files:** `backend/app/core/permissions.py`, `backend/app/models/enums.py` (read-only use), `backend/tests/unit/test_permissions.py`
- **Skills:** authentication-authorization, testing-standard
- **Depends on:** T2
- **Parallel-safe:** yes (with T4, T5)
- **Do:** Implement the Authorization section. Table-driven test over every (role, permission) pair asserting the exact matrix; assert every `UserRole` has an entry (deny-by-default: unknown role → False).
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_permissions.py && uv run --frozen mypy`

### [x] T10 — Workspaces and members
- **Files:** `backend/app/repositories/workspace_repository.py`, `backend/app/services/workspace_service.py`, `backend/app/schemas/workspace.py`, `backend/app/api/workspaces.py`, `backend/app/core/dependencies.py` (add `WorkspaceMembershipDep` factory `require_workspace_permission(permission)`), `backend/app/main.py`, `backend/tests/integration/test_workspaces_api.py`
- **Skills:** api-conventions, authentication-authorization, data-conventions, input-validation, testing-standard
- **Depends on:** T8, T9
- **Parallel-safe:** no
- **Do:** Endpoints and rules from the Endpoints table (create with slug rules, list mine, get, members list/add/patch/delete, `LAST_ADMIN` guard — enforced with `SELECT ... FOR UPDATE` on the workspace's ADMIN rows so two concurrent demotions cannot both succeed, audits per amendment B). Removal deletes only the `workspace_members` row. Membership lookup is a single scoped query; never fetch-then-compare. Tests include negative paths: non-member gets 404 for workspace, members, and member mutation; VIEWER gets 403 adding a member; adding unknown email → `USER_NOT_FOUND`; duplicate → `ALREADY_MEMBER`; demoting/removing last ADMIN → `LAST_ADMIN`; slug collision behaviours; member of workspace A cannot touch a member id belonging to workspace B (404); `MEMBER_REMOVED` row has actor, workspace, target user id, previous role and timestamp; the removed user and all audit rows they authored still exist afterwards with `actor_id` intact; a removed user gets 404 on that workspace and can be re-added.
- **Verify:** `cd backend && uv run --frozen pytest -q && uv run --frozen mypy && uv run --frozen ruff check .`

### [x] T11 — Products and revisions
- **Files:** `backend/app/repositories/product_repository.py`, `backend/app/services/product_service.py`, `backend/app/schemas/product.py`, `backend/app/api/products.py`, `backend/app/main.py`, `backend/tests/integration/test_products_api.py`
- **Skills:** api-conventions, authentication-authorization, data-conventions, input-validation, testing-standard
- **Depends on:** T10
- **Parallel-safe:** no
- **Do:** Endpoints from the table. `GET /products/{id}` and `POST /products/{id}/revisions` resolve the product via `products JOIN workspace_members` for the current user (404 if not a member). List supports `q` (escape `%`/`_` in ILIKE input) and `status`, ordered by `product_code`. Audits on create. Tests: ENGINEER creates, VIEWER 403 on create but can list/read, non-member 404 on detail and revision create, duplicate code/revision 409, `q` with `%` matches literally, pagination totals.
- **Verify:** `cd backend && uv run --frozen pytest -q && uv run --frozen mypy && uv run --frozen ruff check . && uv run --frozen ruff format --check .`

### [x] T12 — Frontend primitives, API client and session store
- **Files:** `frontend/src/components/ui/{input,label,card,alert,select}.tsx`, `frontend/src/lib/api-client.ts`, `frontend/src/lib/api-client.test.ts`, `frontend/src/lib/auth-session.ts`, `frontend/src/lib/auth-session.test.ts`
- **Skills:** component-standards, typescript-conventions, type-safety-patterns, html-semantics-and-accessibility, ui-testing
- **Depends on:** none (contract above)
- **Parallel-safe:** yes (with T1–T11)
- **Do:** UI primitives in shadcn style using `cn()` from `@/lib/utils`, forwarding refs/props, accessible (`Alert` uses `role="alert"` for errors). `auth-session.ts`: in-memory `accessToken` with `getAccessToken`, `setSession`, `clearSession`, `subscribe`; `refreshAccessToken()` single-flight within the tab and serialized across tabs with `navigator.locks.request('bomviz-refresh', ...)` when available; POSTs `/api/v1/auth/refresh` with `credentials: 'same-origin'`. `api-client.ts`: add `apiRequest<T>(method, path, { body?, signal? })` that sends JSON, attaches the bearer token, on 401 with code `TOKEN_EXPIRED` or `TOKEN_INVALID` refreshes once and retries once, on refresh failure clears the session and throws the `ApiError`; 204 returns `undefined`. Keep `getJson`. Tests with MSW: bearer attached, retry after refresh, refresh failure clears session, concurrent 401s trigger one refresh, 204 handling.
- **Verify:** `docker compose exec -T frontend npm run -s lint && docker compose exec -T frontend npm run -s typecheck && docker compose exec -T frontend npm run -s test`

### [x] T13 — Frontend auth feature and route protection
- **Files:** `frontend/src/features/auth/{api.ts,AuthProvider.tsx,useAuth.ts,LoginPage.tsx,RegisterPage.tsx,RequireAuth.tsx,UserMenu.tsx,*.test.tsx}`, `frontend/src/app/routes.tsx`, `frontend/src/app/App.tsx`, `frontend/src/app/layouts/AppLayout.tsx`, `frontend/src/app/pages/HomePage.tsx`, `frontend/src/app/routes.test.tsx`, `frontend/src/tests/server.ts`, `frontend/src/tests/render.tsx`
- **Skills:** component-standards, typescript-conventions, html-semantics-and-accessibility, ui-testing
- **Depends on:** T12
- **Parallel-safe:** yes (with backend tasks)
- **Do:** `AuthProvider` bootstraps on mount by calling `refreshAccessToken()` then `GET /auth/me` (status `loading | authenticated | anonymous`); exposes `user`, `memberships`, `login`, `register` (register then login), `logout`. Login/Register pages: labelled inputs, client-side length checks mirroring the server, server errors shown in `Alert` (message from `ApiError`), submit disabled while pending, `?next=` redirect after login (only same-origin relative paths starting with `/` and not `//`). `RequireAuth` shows a loading state, redirects anonymous users to `/login?next=<path>`. Header: Sign in / Create account links when anonymous; `UserMenu` (name, Sign out) when authenticated. HomePage stays public and keeps its heading; when authenticated it redirects to `/workspaces/<last-used or first>` or `/workspaces` if none. Default MSW handlers: refresh → 401 (anonymous) so existing tests keep passing; add helpers to sign a test user in. Tests: login success navigates to next; bad credentials show the server message; register validation; protected route redirects; sign out clears session.
- **Verify:** `docker compose exec -T frontend npm run -s lint && docker compose exec -T frontend npm run -s typecheck && docker compose exec -T frontend npm run -s test`

### [x] T14 — Frontend workspaces and members
- **Files:** `frontend/src/features/workspaces/{api.ts,WorkspaceSwitcher.tsx,WorkspacesPage.tsx,MembersPage.tsx,useActiveWorkspace.ts,*.test.tsx}`, `frontend/src/components/ui/confirm-dialog.tsx`, `frontend/src/app/routes.tsx`, `frontend/src/app/layouts/AppLayout.tsx`
- **Skills:** component-standards, typescript-conventions, html-semantics-and-accessibility, ui-testing
- **Depends on:** T13
- **Parallel-safe:** no (touches routes/layout)
- **Do:** TanStack Query hooks for every workspace/member endpoint (query keys `['workspaces']`, `['workspaces', id]`, `['workspaces', id, 'members']`; invalidate on mutation). `WorkspaceSwitcher` (native `<select>` via `ui/select`) in the header, navigates to the selected workspace and stores it as last-used. `WorkspacesPage`: list + create form (name, optional slug). `MembersPage`: table of members; ADMIN sees add-by-email form (email + role), per-row role select and Remove. Remove opens an accessible confirmation built on the native `<dialog>` element (`ui/confirm-dialog.tsx`; focus moves into it, Escape cancels, focus returns to the trigger) that shows the member's **name, email and role** and requires an explicit "Remove member" click; role changes that would demote the last ADMIN are blocked client-side with an explanation (server still enforces `LAST_ADMIN`); non-ADMIN sees read-only table; server errors (`USER_NOT_FOUND`, `ALREADY_MEMBER`, `LAST_ADMIN`) shown inline. Tests for create workspace, add member error display, read-only view for VIEWER, switcher navigation, the removal dialog showing name/email/role and cancelling without a request, confirming sends DELETE, and the last-admin block.
- **Verify:** `docker compose exec -T frontend npm run -s lint && docker compose exec -T frontend npm run -s typecheck && docker compose exec -T frontend npm run -s test`

### [x] T15 — Frontend products and revisions
- **Files:** `frontend/src/features/products/{api.ts,ProductsPage.tsx,ProductDetailPage.tsx,*.test.tsx}`, `frontend/src/app/routes.tsx`
- **Skills:** component-standards, typescript-conventions, html-semantics-and-accessibility, ui-testing
- **Depends on:** T14
- **Parallel-safe:** no
- **Do:** `ProductsPage` at `/workspaces/:workspaceId/products` (and the workspace index): search box (`q`, kept in URL search params, debounced 300 ms), status filter, table, pagination (limit 50) and a create form shown only to roles with product write (ADMIN, ENGINEER — read role from `memberships`; server still enforces). `ProductDetailPage`: product fields, revisions table, create-revision form for writers. Empty, loading and error states for every query. Tests: list renders and filters via URL, VIEWER sees no create form, create shows `PRODUCT_CODE_TAKEN` message, revision create adds a row.
- **Verify:** `docker compose exec -T frontend npm run -s lint && docker compose exec -T frontend npm run -s typecheck && docker compose exec -T frontend npm run -s test && docker compose exec -T frontend npm run -s format:check`

### [x] T16 — End-to-end journey
- **Files:** `frontend/e2e/auth-workspace.spec.ts`, `frontend/e2e/smoke.spec.ts`
- **Skills:** ui-testing
- **Depends on:** T11, T15
- **Parallel-safe:** no
- **Do:** Playwright spec against the real stack: register a unique user (`e2e-${Date.now()}@example.com`), sign in, create a workspace, create a product and a revision, reload the page (session survives via refresh cookie), sign out, and confirm a protected URL redirects to `/login`. Keep smoke tests passing (adjust only if the landing page text changed). Also cover member removal through the confirmation dialog (needs a second registered user). **Required before completion:** run it locally with the official Playwright image against the running stack (`docker run --rm --network host -v "<frontend abs path>:/work" -w /work mcr.microsoft.com/playwright:v1.64.0-noble sh -c "npm ci && npx playwright test"`, `E2E_BASE_URL=http://localhost:5173`); if the machine cannot, report it and the CI `End-to-end` job becomes the gate.
- **Verify:** e2e passes locally (or CI `End-to-end` job green, stated explicitly)

### [x] T17 — Documentation
- **Files:** `docs/API.md`, `docs/DATABASE.md`, `docs/ARCHITECTURE.md`, `README.md`
- **Skills:** docs-standard
- **Depends on:** T11, T15
- **Parallel-safe:** yes (with T16)
- **Do:** API.md: mark Phase 1 ✅, add member list/patch/delete endpoints, all new error codes (`INVALID_CREDENTIALS`, `LOGIN_THROTTLED` 429, `EMAIL_TAKEN`, `USER_NOT_FOUND`, `ALREADY_MEMBER`, `LAST_ADMIN`, `SLUG_TAKEN`, `PRODUCT_CODE_TAKEN`, `REVISION_CODE_TAKEN`, `RATE_LIMITED` 429), rate limits, cookie attributes. DATABASE.md: `0002`/`0003` applied; deviations A10 (`users.is_platform_admin` replaces `users.role`), A11 (revisions scoped via product); update the ERD rows for users, refresh_tokens, products. ARCHITECTURE.md: "Authentication and authorization (Phase 1)" subsection — token lifecycle, reuse detection, progressive (email, IP) throttling, permission matrix, member removal and audit preservation, cross-tab refresh lock. DATABASE.md also documents `login_throttles` and `audit_logs.workspace_id ON DELETE RESTRICT`. README: status line → Phase 1 complete; how to create the first account.
- **Verify:** `grep -n "Phase 1" docs/API.md README.md` shows updated status; links resolve

## Review fixes (from the full-diff review, 2026-10-11)

The code review found no Blockers and 8 Should-fix items; all are fixed in Phase 1. Nits N5–N8
(same-transaction revision ordering, duplicated OpenAPI error helpers, dependency placement,
doc wording) are deferred to the retro.

### [x] F1 — Backend auth hardening
- **Skills:** authentication-authorization, data-conventions, testing-standard
- **Do:** S1 serialize the per-(email, IP) throttle with `pg_advisory_xact_lock` on the key before the block check, so concurrent bursts on a fresh pair get at most `LOGIN_FREE_ATTEMPTS` password checks (concurrency test). S4 logout locks the presented token row `FOR UPDATE` before revoking the family, so it serializes with a concurrent refresh (concurrency test). N3 only a token with `replaced_by_id IS NOT NULL` counts as reuse; a token revoked by logout gets `TOKEN_INVALID` and revokes nothing else. N4 successors inherit the family's original `expires_at` (absolute session lifetime = `REFRESH_TOKEN_TTL_DAYS` from login). S5 rate-limit `POST /workspaces/{id}/members` per acting user (`member_add`, 30/hour). N1 raise the FastAPI floor to `>=0.121` (needed for `Depends(scope="function")`).

### [x] F2 — Client IP behind a trusted proxy
- **Skills:** appsec-conventions, input-validation, testing-standard
- **Do:** S6 new setting `TRUSTED_PROXY_IPS` (comma-separated IPs/CIDRs, default empty = trust nothing). `ClientIpDep` uses the right-most `X-Forwarded-For` address not in the trusted set, only when the direct peer is trusted; otherwise the peer address. Compose gives the frontend container a fixed address on a defined network and sets `TRUSTED_PROXY_IPS` to exactly that address, so direct host requests to :8000 cannot spoof the header; Vite proxy sends `X-Forwarded-For` (`xfwd: true`). S7 IPv6 addresses are normalized to their /64 before they become a throttle or rate-limit identity. Unit tests for parsing, spoofing attempts and IPv6 normalization.

### [x] F3 — Frontend session robustness
- **Skills:** component-standards, typescript-conventions, ui-testing
- **Do:** S2 clear the session only when refresh returns 401 (`UNAUTHENTICATED`, `TOKEN_INVALID`, `TOKEN_EXPIRED`, `TOKEN_REUSED`); 429/5xx/network errors keep the session and surface the error. S3 detect a user switch (token `sub` changes without passing through signed-out) → clear the query cache and reload `/auth/me`. S4 logout runs inside the `bomviz-refresh` Web Lock. N2 `created_by_id: string | null`.

### [x] F4 — Docs reconciled with code
- **Skills:** docs-standard
- **Do:** S8 document Product/Revision shapes, list ordering, `q` limits and literal wildcards, revision description limit, blank description → null, `SLUG_TAKEN` after exhausted suffixes and short-slug suffixing; document F1–F3 behaviour (member_add limit, reuse semantics, absolute session lifetime, trusted proxy + deployment note, IPv6 /64, refresh failure handling, user-switch cache clearing); N8 password-equals-email is case-insensitive.

## Review & delivery

- [x] Full-diff review by code-reviewer — no Blockers; 8 Should-fix items fixed (F1–F4)
- [x] Verifier agent run — 10/11 PASS live, 1 not runnable (register rate limit; covered by tests); 2 minor findings fixed
- [x] Backend lint, format, typecheck, tests green (402 passed; auth files re-run after the last fix: 74 passed): `cd backend && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy && uv run --frozen pytest -q`
- [x] Frontend lint, format, typecheck, tests green (150 passed): `docker compose exec -T frontend npm run -s <lint|format:check|typecheck|test>`
- [x] Playwright auth/workspace journey green (T16) — 4/4 locally
- [x] Alembic round trip against the Compose database (0003 → base → 0003): `docker compose run --rm migrate sh -c "alembic upgrade head && alembic downgrade base && alembic upgrade head"` (plus the integration test)
- [x] Local commit(s) with `khushishimpi0@gmail.com`; push only when the user asks
- [ ] Phase summary to the user: files created/modified, endpoints, roles/permissions, audit events, exact local verification commands, deviations — then stop for review before Phase 2

## Retro

**Deviations from the spec (all accepted):**
- T3: commit/rollback managed manually instead of `async with session.begin()` (needed for commit-before-raise); `Depends(scope="function")` so commit failures surface before the response.
- T2: `login_throttles.updated_at` kept; composite unique constraints given explicit full names.
- T10: derived slugs shorter than 3 chars always get a suffix; exhausted suffixes → 409 `SLUG_TAKEN`; only admins can remove themselves.
- T14/T15: extra small component files (file-size rule); the workspace index stays an overview page with links to Products and Members (not the product list).
- T14: Vitest/Testing Library timeouts raised (15 s / 3 s) because parallel backend runs starved the CPU; revisit if CI is fine without it.
- F2 changes a stated decision ("no proxy header trust"): trust is opt-in via `TRUSTED_PROXY_IPS`, set in Compose only to the frontend container, because without it amendment A degrades to a per-email throttle behind the dev proxy.
- After verification: logout of an already-ended session is a silent no-op (no audit row); README documents restarting backend/worker after re-running migrations (stale citext type ids).

**Deferred nits:** N5 same-transaction revision ordering, N6 duplicated OpenAPI error helpers, N7 `require_product_permission` placement, undocumented audit metadata keys (`family_id`, `slug`, `product_code`, `revision_code`).

**Skill gaps:** api-conventions/data-conventions assume UUIDv7, `{data, meta}` and `tenant_id` — every dispatch needed an override note. No skill covered trusted-proxy client IP handling or concurrency testing for auth flows.

**Environment:** low RAM repeatedly killed background jobs and stalled executors; the full backend suite (~10 min) had to be split to stay under tool limits.
