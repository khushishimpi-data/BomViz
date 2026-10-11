# Spec: Phase 2 — BOM upload and parsing

- **Date:** 2026-10-11
- **Author:** Khushi Shimpi
- **Status:** Done — awaiting user review (2026-10-11)
- **ClickUp task:** n/a

## Context

Phase 1 delivered auth, per-workspace RBAC, workspaces, products and audit logging (see
`specs/2026-10-10-phase-1-auth-workspaces.md` and its retro — reuse its patterns:
`require_workspace_permission`, scoped repositories, `AuditService`, commit-before-raise,
`apiRequest` + TanStack Query + MSW on the frontend). Phase 2 lets an engineer upload an Excel
or CSV BOM, keeps the original immutable in object storage, parses it in a Celery job, and
shows a raw preview with an auto-suggested column mapping. Read `docs/ARCHITECTURE.md`,
`docs/API.md` (BOM workflow table), `docs/DATABASE.md` and `docs/adr/0001-hierarchy-and-rollup.md`
first.

Decisions made during clarification (do not revisit):

- **Formats:** `.csv`, `.xlsx` and `.xls` (legacy via `xlrd`).
- **Preview scope:** raw preview **plus** a suggested mapping (column → standard field, with a
  confidence). Persisting/editing the mapping, the normalized stage and validation are Phase 3.
- **Permissions:** upload and (re-)parse require ADMIN or ENGINEER; viewing BOMs, previews and
  downloading the original are allowed to every workspace member. Every download is audited.
- **Limits:** 20 MB per file, 50,000 data rows, 200 columns, 32,767 characters per cell (the
  Excel cell limit), 50 sheets listed. Excel files are read in streaming mode with zip-bomb
  guards. Over-limit files fail parsing with a specific error code.

Design choices made here (flag in review if you disagree):

- **Upload starts parsing automatically** with auto-detected options, so the preview is ready
  without an extra click; users re-parse with explicit options when detection is wrong.
- **Preview snapshot (deviation A12):** the worker stores a bounded snapshot (detected options,
  sheets, columns, row count, first 100 rows, suggested mapping) in `bom_uploads.parse_result`
  JSONB. The preview endpoint reads it, so the API never parses a 20 MB file inside a request.
  Validation and import (Phases 3–4) still re-read the immutable original, as ARCHITECTURE says.
- **The full `upload_status` enum is created now** (later phases only start using values), to
  avoid enum-altering migrations whose downgrades are fragile.
- **Duplicate upload** of the same bytes into the same workspace (not soft-deleted) → 409
  `DUPLICATE_UPLOAD` with the existing BOM id; the same file in another workspace is allowed.

**Non-goals:** saving or editing the mapping, normalized preview, validation rules, import,
soft delete endpoint, retry endpoint and stale-job sweeper beyond the rule below (Phase 4),
exports, malware scanning, password-protected workbooks (rejected with a clear code), formula
evaluation (cached values only), merged-cell semantics beyond reading the top-left value.

**Machine constraints (local):** same as Phase 1 — at most two executors in parallel, never
`npm install` and Docker builds at the same time, frontend commands via
`docker compose exec -T frontend npm run -s <script>`, backend via
`cd backend && uv run --frozen <cmd>` (only T1 changes `uv.lock`). The full backend suite takes
~10 min: run touched files during development and the full suite once per task with
`timeout 1500`. The Celery worker does not auto-reload: run `docker compose restart worker`
after worker-side code changes before live checks.

## Design reference (all tasks)

### Database (migration `0004_bom_uploads`)

Enums: `file_format` (`CSV, XLSX, XLS`); `upload_status` (`UPLOADED, PARSING, PARSED,
PARSE_FAILED, MAPPED, VALIDATING, VALIDATED, VALIDATION_FAILED, IMPORTING, IMPORTED,
IMPORT_FAILED` — Phase 2 uses the first four); `job_type` (`PARSE, VALIDATE, IMPORT`);
`job_status` (`QUEUED, RUNNING, SUCCEEDED, FAILED`).

- `bom_uploads`: `id uuid PK`, `workspace_id FK workspaces ON DELETE RESTRICT` (indexed),
  `product_id FK products ON DELETE SET NULL NULL` (indexed), `product_revision_id FK
  product_revisions ON DELETE SET NULL NULL`, `bom_name text NOT NULL`, `revision_label text
  NULL`, `original_filename text NOT NULL` (sanitized, display only), `file_format file_format
  NOT NULL`, `content_type text NOT NULL` (server-chosen from format), `size_bytes bigint NOT NULL
  CHECK (> 0)`, `checksum_sha256 char(64) NOT NULL`, `original_file_key text NOT NULL UNIQUE`,
  `status upload_status NOT NULL DEFAULT 'UPLOADED'` (indexed), `parse_options jsonb NULL`
  (effective options of the last successful parse), `detected_columns jsonb NULL`
  (`[{index, name}]`), `parse_result jsonb NULL` (snapshot, see below), `row_count int NULL`,
  `column_count int NULL`, `uploaded_by_id FK users ON DELETE SET NULL NULL`, `uploaded_at
  timestamptz NOT NULL DEFAULT now()`, `is_deleted bool NOT NULL DEFAULT false`, `deleted_at
  timestamptz NULL`, `created_at`, `updated_at`. Indexes: `(workspace_id, uploaded_at DESC)`;
  partial unique `uq_bom_uploads_workspace_id_checksum_sha256 (workspace_id, checksum_sha256)
  WHERE NOT is_deleted` (A6).
- `import_jobs` (one table for parse/validate/import jobs): `id`, `bom_upload_id FK bom_uploads
  ON DELETE CASCADE` (indexed), `job_type job_type NOT NULL`, `status job_status NOT NULL DEFAULT
  'QUEUED'`, `parameters jsonb NOT NULL DEFAULT '{}'` (requested parse options), `attempts int NOT
  NULL DEFAULT 0 CHECK (>= 0)`, `celery_task_id text NULL`, `error_code text NULL`,
  `error_message text NULL` (user-safe text only), `requested_by_id FK users ON DELETE SET NULL
  NULL`, `queued_at timestamptz NOT NULL DEFAULT now()`, `started_at`, `finished_at timestamptz
  NULL`, `created_at`, `updated_at`. Partial unique `uq_import_jobs_bom_upload_id_active
  (bom_upload_id) WHERE status IN ('QUEUED','RUNNING')` — at most one active job per BOM.

### Upload flow (`POST /api/v1/boms/upload`, multipart)

Fields: `file`, `workspace_id`, `bom_name` (1–200, stripped), `revision_label?` (1–64),
`product_id?`, `product_revision_id?` (revision must belong to that product; both must be in the
workspace, else 404 `NOT_FOUND`; revision without product → 422). Order:

1. Permission `BOM_UPLOAD` on `workspace_id` (404 non-member, 403 lacking role). Rate limit
   `bom_upload` 30/hour per user.
2. Reject early when `Content-Length` exceeds 20 MB + 64 KB form overhead → 413
   `FILE_TOO_LARGE`. Stream the part into a temp file counting bytes and hashing (SHA-256); stop at
   20 MB → 413. Empty file → 400 `EMPTY_FILE`.
3. Format from the extension **and** magic bytes, never the client content type: `.xlsx` must
   start with `PK\x03\x04`; `.xls` with `D0 CF 11 E0 A1 B1 1A E1`; `.csv` must not start with
   either signature and must contain no NUL byte in the first 64 KB. Mismatch or other extension
   → 400 `UNSUPPORTED_FILE_TYPE`. (An OLE file named `.xlsx` is usually an encrypted workbook →
   400 `ENCRYPTED_WORKBOOK`.)
4. Duplicate check (same workspace, same checksum, not deleted) → 409 `DUPLICATE_UPLOAD`,
   `details: [{existing_bom_id}]`.
5. Object key `workspaces/{workspace_id}/boms/{bom_id}/original.{ext}` (client filename never in
   keys). Put the object (`ContentType` from format), then insert `bom_uploads` + a `PARSE`
   `import_jobs` row with empty parameters, audit `BOM_UPLOADED`, commit. If the DB insert fails,
   delete the object (best effort, log). Storage errors → 503 `STORAGE_UNAVAILABLE`. A concurrent
   duplicate hitting the partial unique index → 409 `DUPLICATE_UPLOAD`.
6. **After commit**, enqueue the Celery task `boms.parse(job_id)`. If enqueueing fails, mark the
   job `FAILED` with `QUEUE_UNAVAILABLE` and the upload `PARSE_FAILED` (user can re-parse).
7. Respond 201 `BomDetailResponse`.

`original_filename`: basename only (strip any path), control characters removed, max 255 chars,
fallback `upload.<ext>`.

### Parse job (worker)

`app/workers/tasks.py` task `boms.parse` (`acks_late`, no automatic retry for parse errors;
storage/DB connection errors retried 3× with backoff 5/20/60 s, then `FAILED`
`STORAGE_UNAVAILABLE`). It calls `asyncio.run(run_parse_job(job_id))`, which builds its own engine
(NullPool), session and storage — the same async services as the API.

1. Lock the job row; if not `QUEUED` (already ran / superseded) → no-op. Set `RUNNING`,
   `started_at`, `attempts += 1`; upload `PARSING`; commit.
2. Download the original to a temp file; parse with `parameters` (explicit options override
   detection).
3. Success: write `parse_options` (effective), `detected_columns`, `row_count`, `column_count`,
   `parse_result`; upload `PARSED`; job `SUCCEEDED`, `finished_at`; audit `BOM_PARSED` (actor =
   requested_by, metadata `{row_count, column_count}`).
4. `ParseError` (user-facing code + safe message) → job `FAILED` with that code/message, upload
   `PARSE_FAILED`, audit `BOM_PARSE_FAILED {error_code}`. Unexpected exception → `FAILED`
   `INTERNAL_ERROR` "The file could not be parsed." (details only in logs with `job_id`,
   `bom_upload_id`).

**Stale jobs:** a `RUNNING` job whose `started_at` is older than 15 minutes, or a `QUEUED` job
older than 15 minutes, no longer blocks a new parse request: the request marks it `FAILED`
`STALE_JOB` and proceeds.

### Parser (`app/services/parsing/`)

Pure, synchronous, file-path based, unit-testable; one module per format plus shared helpers.
Output `ParseOutcome(effective_options, sheets, columns, row_count, sample_rows, skipped_empty_rows)`.

- **CSV:** encoding — explicit option, else BOM sniffing (`utf-8-sig`, `utf-16`), else strict
  UTF-8, else `charset-normalizer` best match restricted to the allowlist; failure →
  `PARSE_ENCODING_UNKNOWN`. Delimiter — explicit, else `csv.Sniffer` over the first 64 KB limited
  to `, ; \t |`, fallback `,`. `csv.field_size_limit(32767)`; longer cell → `PARSE_CELL_TOO_LONG`.
- **XLSX:** before opening, inspect the zip: total uncompressed size ≤ 200 MB, no member with
  compression ratio > 100, ≤ 10,000 members, else `PARSE_FILE_TOO_COMPLEX`; not a valid zip →
  `PARSE_FILE_CORRUPT`. `openpyxl.load_workbook(read_only=True, data_only=True)` with
  `defusedxml` installed (openpyxl uses it automatically). Formulas yield their cached value
  (empty if never calculated).
- **XLS:** `xlrd.open_workbook(on_demand=True, formatting_info=False)`; dates converted with the
  workbook datemode; corrupt → `PARSE_FILE_CORRUPT`; encrypted → `ENCRYPTED_WORKBOOK`.
- **Sheets:** list `[{name, visible}]` (max 50 names). Default sheet = first visible sheet with
  any data. Unknown `sheet_name` → `PARSE_SHEET_NOT_FOUND`. No data anywhere → `PARSE_EMPTY_FILE`.
- **Cell values → text:** strings stripped of trailing `\r`; ints as integers; floats without
  float noise (`Decimal(repr(v)).normalize()`, no exponent for normal ranges); booleans
  `TRUE/FALSE`; dates/datetimes ISO 8601 (`2026-10-11`, `2026-10-11T08:30:00`); empty → `""`.
- **Header row:** explicit `header_row` (1-based source row) else the first row within the first
  20 non-empty rows that has ≥ 2 non-empty cells and where non-numeric text cells are ≥ 50% of
  its non-empty cells; none found → `PARSE_NO_HEADER`. Column names: stripped header text;
  blank → `Column N`; duplicates → `Name (2)`, `Name (3)`. Trailing columns that are empty in the
  header and in every data row are dropped.
- **Limits:** data rows after the header (non-empty) ≤ 50,000 → `PARSE_TOO_MANY_ROWS`; columns ≤
  200 → `PARSE_TOO_MANY_COLUMNS`. Fully empty rows are skipped and counted.
- **Sample:** first 100 data rows `{row_number (1-based source row), cells: [str]}`, each cell
  truncated to 500 characters for the snapshot.

### Suggested mapping (`app/services/mapping_suggestions.py` + `app/domain/bom_fields.py`)

Standard target fields (key, label, required, synonyms): `part_number`* (part no, pn, item
number, component, sku…), `part_name`* (name, part description short, item name…), `quantity`*
(qty, qty per, quantity per assembly, usage…), `parent_part_number` (parent, parent pn, assembly,
next higher assembly…), `bom_level` (level, lvl, indent, find number, item no. when dotted…),
`unit_of_measure` (uom, unit, units), `description`, `unit_cost` (price, unit price, cost each),
`total_cost` (extended cost, ext price), `currency`, `supplier_name` (vendor, supplier),
`supplier_part_number` (vendor pn), `manufacturer_name` (mfr, manufacturer), `manufacturer_part_number`
(mpn, mfr pn), `material`, `lead_time_days` (lead time, lt days), `reference_designator` (ref
des, designator), `notes` (remarks, comment). Normalize headers (lowercase, `&`→and, strip
punctuation/whitespace, `#`/`no`/`num`/`number` unified). Score: exact synonym 1.0; otherwise
`difflib.SequenceMatcher` ratio against label and synonyms, kept if ≥ 0.8. Assign greedily by
descending score so each column and each target is used at most once. Output per column
`{source_column_index, source_column_name, target_field | null, confidence}` plus
`missing_required: [field]`. Stored in `parse_result.suggested_mapping`.

### Endpoints (all under `/api/v1`)

| Method | Path | Permission | Success |
|---|---|---|---|
| POST | `/boms/upload` | BOM_UPLOAD on `workspace_id` | 201 `BomDetailResponse` |
| GET | `/boms?workspace_id=…&status&product_id&q&limit&offset` | BOM_READ on `workspace_id` | 200 page of `BomSummaryResponse`, newest first; `q` ILIKE (escaped) on bom_name/original_filename/revision_label |
| GET | `/boms/{id}` | BOM_READ | 200 `BomDetailResponse` (fields, product/revision ids, counts, `latest_job`, last 10 `jobs`) |
| GET | `/boms/{id}/file` | BOM_READ | 200 `{url, expires_at}`; presigned GET via `S3_PUBLIC_ENDPOINT_URL`, `SIGNED_URL_TTL_SECONDS`, `ResponseContentDisposition: attachment; filename*=UTF-8''…`; audit `BOM_FILE_DOWNLOADED` |
| POST | `/boms/{id}/parse` | BOM_UPLOAD | 202 `JobResponse`; body `{sheet_name? (1–255), header_row? (1–1000), delimiter? (",", ";", "\t", "|"), encoding? (allowlist: utf-8, utf-8-sig, utf-16, cp1252, latin-1, iso-8859-15)}`; allowed in `UPLOADED, PARSED, PARSE_FAILED` else 409 `INVALID_STATE`; active non-stale job → 409 `JOB_IN_PROGRESS`; audit `BOM_PARSE_REQUESTED` |
| GET | `/boms/{id}/preview?stage=raw` | BOM_READ | 200 `PreviewResponse` `{status, parse_options, sheets, columns, row_count, column_count, skipped_empty_rows, rows, suggested_mapping, missing_required, target_fields}`; not `PARSED` → 409 `NOT_PARSED`; `stage=normalized` → 422 (Phase 3) |
| GET | `/templates/{csv\|xlsx}` | authenticated | 200 file download with the standard field labels as headers (required first) |

BOM ids resolve with one scoped query (`bom_uploads JOIN workspace_members` on the caller,
`NOT is_deleted`); missing / other workspace → 404 `NOT_FOUND` "BOM not found.". Job ids are never
accepted from clients in Phase 2.

Permissions: add `BOM_READ` (all five roles) and `BOM_UPLOAD` (ADMIN, ENGINEER) to
`app/core/permissions.py`.

New error codes: `FILE_TOO_LARGE` (413), `EMPTY_FILE` / `UNSUPPORTED_FILE_TYPE` /
`ENCRYPTED_WORKBOOK` (400), `DUPLICATE_UPLOAD` / `JOB_IN_PROGRESS` / `NOT_PARSED` / `INVALID_STATE`
(409), `STORAGE_UNAVAILABLE` (503); job-level `PARSE_*`, `QUEUE_UNAVAILABLE`, `STALE_JOB`,
`INTERNAL_ERROR`.

Audit actions (add to `AuditAction`): `BOM_UPLOADED {file_format, size_bytes}`,
`BOM_PARSE_REQUESTED {job_id}`, `BOM_PARSED {row_count, column_count}`, `BOM_PARSE_FAILED
{error_code}`, `BOM_FILE_DOWNLOADED {}`. Never the filename, checksum or URL.

### Frontend

`src/features/boms/`: `api.ts` (hooks; BOM detail polls every 2 s while the latest job is
`QUEUED`/`RUNNING`), `BomsPage` (`/workspaces/:workspaceId/boms`: table with status badges, `q` and
status filters in the URL, pagination, "Upload BOM" for writers, template download links),
`UploadBomPage` (`…/boms/new`: file input accepting `.csv,.xlsx,.xls`, client checks for
extension and 20 MB, name, revision label, optional product and revision selects from the
products API, upload progress via `XMLHttpRequest` upload events or a plain pending state;
`DUPLICATE_UPLOAD` links to the existing BOM; on success navigate to the detail),
`BomDetailPage` (`…/boms/:bomId`: metadata, status and job state with error code/message,
"Download original" (fetch signed URL then `window.location.assign`), re-parse form for
writers (sheet select from `sheets`, header row, delimiter, encoding; disabled while a job
runs), raw preview table (sticky header, horizontal scroll, row numbers from the source, each
header showing the suggested field and confidence, a warning listing `missing_required`)).
Workspace overview gets a "BOMs" link. No new npm dependencies.

## Skill routing table

| Work type | Governing skill | Source plugin |
|---|---|---|
| FastAPI routes, multipart, schemas, error codes | api-conventions | python |
| Cross-cutting API rules | api-standard | governance |
| Models, migration, queries | data-conventions | postgres |
| Upload/download authz, object-level access | authentication-authorization | security |
| Untrusted files: type sniffing, limits, zip bombs, filenames, CSV/XML parsing | input-validation | security |
| Baseline appsec, signed URLs, secrets | appsec-conventions | security |
| New third-party libraries | dependency-security, dependency-policy | security, governance |
| Job failures, retries, user-safe errors | error-handling, resilience-patterns | developer, architect |
| Logs and audit events | logging | developer |
| Backend tests | testing-standard | governance |
| React components, hooks, state | component-standards | react-web |
| Component and e2e tests | ui-testing | react-web |
| TypeScript typing | typescript-conventions, type-safety-patterns | typescript |
| Forms, tables, file input accessibility | html-semantics-and-accessibility | html-css |
| Docs | docs-standard | governance |
| Review / verification | code-reviewer agent, verifier agent | core, quality-assurance |

Project conventions override skill defaults as in Phase 1 (`{items,total,limit,offset}`,
`gen_random_uuid()` ids, `workspace_id` tenant key, fetch-based `apiRequest`, MSW).

## Tasks

### [x] T1 — Dependencies and settings
- **Files:** `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/core/config.py`, `backend/.env.example`, `backend/tests/unit/test_config.py`
- **Skills:** dependency-security, dependency-policy, testing-standard
- **Depends on:** none · **Parallel-safe:** yes (with T10)
- **Do:** `uv add "openpyxl>=3.1" "xlrd>=2.0" "charset-normalizer>=3.3" "python-multipart>=0.0.9" "defusedxml>=0.7"`; dev group `uv add --dev "xlwt>=1.3"` (test fixtures for `.xls` only). Report licences of each new package. Settings with defaults: `max_upload_bytes=20*1024*1024`, `parse_max_rows=50_000`, `parse_max_columns=200`, `parse_max_cell_chars=32_767`, `parse_sample_rows=100`, `parse_max_uncompressed_bytes=200*1024*1024`, `job_stale_after_minutes=15`; document in `.env.example`; config tests.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_config.py && uv run --frozen mypy && uv run --frozen ruff check .`

### [x] T2 — Models and migration 0004
- **Files:** `backend/app/models/{enums,bom_upload,import_job,__init__}.py`, `backend/alembic/versions/0004_bom_uploads.py`, `backend/tests/integration/test_database.py`, `backend/tests/conftest.py` (truncate list comes from metadata — check)
- **Skills:** data-conventions, testing-standard
- **Depends on:** T1 · **Parallel-safe:** no
- **Do:** Exactly the Database section; enums created/dropped in the migration; partial unique indexes via `postgresql_where`. Extend the round-trip and model/schema diff tests to the new tables and enums.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/integration/test_database.py && uv run --frozen mypy && uv run --frozen ruff check .`

### [x] T3 — Parser package
- **Files:** `backend/app/services/parsing/{__init__,errors,cells,header,csv_parser,xlsx_parser,xls_parser,parse}.py`, `backend/tests/unit/parsing/*.py`, `backend/tests/fixtures/boms/` (generated in tests where possible; tiny committed `.xls` fixtures built with xlwt by a script `backend/tests/fixtures/boms/make_xls.py`)
- **Skills:** input-validation, error-handling, testing-standard
- **Depends on:** T1 · **Parallel-safe:** yes (with T2)
- **Do:** Exactly the Parser section. `ParseError(code, message)` with user-safe messages. Tests: each format happy path; encodings (UTF-8 BOM, UTF-16, cp1252 with "€", invalid bytes with an explicit wrong encoding); delimiters `; \t |`; header auto-detection with title rows above the header; explicit header_row; blank/duplicate header names; trailing empty columns; empty rows skipped; numbers without float noise, booleans, dates (xlsx and xls datemode); formulas → cached value; multiple sheets incl. hidden; unknown sheet; empty file; each limit (rows, columns, cell length — use small overridden limits); zip bomb (high-ratio member) and corrupt zip; OLE file named .xlsx; corrupt xls.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/parsing && uv run --frozen mypy && uv run --frozen ruff check .`

### [x] T4 — Standard fields and mapping suggestions
- **Files:** `backend/app/domain/{__init__,bom_fields}.py`, `backend/app/services/mapping_suggestions.py`, `backend/tests/unit/test_mapping_suggestions.py`
- **Skills:** testing-standard
- **Depends on:** none · **Parallel-safe:** yes
- **Do:** Exactly the Suggested mapping section. Tests: exact synonyms, punctuation/case variants ("Part #", "QTY.", "Mfr. P/N"), fuzzy match threshold boundary, one column per target and vice versa, missing_required, unrelated headers stay unmapped, deterministic tie-breaking (lower column index wins).
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_mapping_suggestions.py && uv run --frozen mypy`

### [x] T5 — Storage service extensions
- **Files:** `backend/app/services/storage_service.py`, `backend/tests/fakes.py` (`FakeStorage`), `backend/tests/unit/test_storage_service.py`
- **Skills:** appsec-conventions, error-handling, testing-standard
- **Depends on:** T1 · **Parallel-safe:** yes
- **Do:** async `put_file(key, path, content_type)`, `download_to(key, path)`, `delete(key)`, `presigned_download_url(key, filename, content_type, ttl) -> (url, expires_at)` signed with a client bound to `S3_PUBLIC_ENDPOINT_URL` (fallback to the internal endpoint) and `ResponseContentDisposition` attachment with RFC 5987 encoding; map botocore/connection errors to `StorageUnavailableError` (503 `STORAGE_UNAVAILABLE`). `FakeStorage` in-memory for tests. Unit tests for disposition encoding (non-ASCII, quotes, path-like names) and error mapping.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/unit/test_storage_service.py && uv run --frozen mypy`

### [x] T6 — Upload service and endpoints (upload, list, detail, file)
- **Files:** `backend/app/repositories/bom_repository.py`, `backend/app/services/bom_service.py`, `backend/app/services/upload_intake.py` (streaming size/hash/magic checks, filename sanitizing), `backend/app/schemas/bom.py`, `backend/app/api/boms.py`, `backend/app/core/{permissions,dependencies}.py`, `backend/app/services/audit_service.py`, `backend/app/main.py`, `backend/tests/unit/test_upload_intake.py`, `backend/tests/unit/test_permissions.py`, `backend/tests/integration/test_boms_api.py`
- **Skills:** api-conventions, authentication-authorization, input-validation, data-conventions, testing-standard
- **Depends on:** T2, T5 · **Parallel-safe:** no
- **Do:** Upload flow steps 1–7, list, detail, file endpoints and permissions/audits per the spec. Enqueueing goes through an injectable `JobQueue` protocol (`enqueue_parse(job_id)`); tests use a recording fake. Tests: every error code of the upload flow (oversize by header and by stream, empty, wrong extension, magic mismatch, CSV with NUL, OLE-as-xlsx), duplicate in same workspace vs allowed in another, product/revision scoping, VIEWER 403 on upload but can list/detail/download, non-member 404, object key never contains the filename, DB-failure cleanup deletes the object, enqueue failure marks job/upload failed, signed URL fields and audit row, rate limit 429, list filters/pagination/`q` escaping.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/integration/test_boms_api.py tests/unit && uv run --frozen mypy && uv run --frozen ruff check . && uv run --frozen ruff format --check .`

### [x] T7 — Parse job runner, Celery task and re-parse/preview endpoints
- **Files:** `backend/app/services/parse_job_service.py`, `backend/app/workers/tasks.py`, `backend/app/workers/celery_app.py` (include tasks), `backend/app/services/celery_job_queue.py`, `backend/app/api/boms.py`, `backend/app/schemas/bom.py`, `backend/tests/integration/test_parse_job.py`, `backend/tests/integration/test_boms_api.py`
- **Skills:** error-handling, resilience-patterns, logging, api-conventions, testing-standard
- **Depends on:** T3, T4, T6 · **Parallel-safe:** no
- **Do:** Parse job section (locking, state transitions, success/failure writes, audits, retries for storage errors only, stale-job rule) and the `POST /boms/{id}/parse` + `GET /boms/{id}/preview` endpoints. Tests run `run_parse_job` directly against the real DB with `FakeStorage`: success snapshot contents; each failure path sets codes and statuses; a non-QUEUED job is a no-op; re-parse with explicit options changes the result; `JOB_IN_PROGRESS` vs stale job; `INVALID_STATE`; preview `NOT_PARSED`, `stage=normalized` 422; VIEWER reads preview, cannot re-parse.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/integration/test_parse_job.py tests/integration/test_boms_api.py && uv run --frozen mypy && uv run --frozen ruff check .`

### [x] T8 — Templates endpoint
- **Files:** `backend/app/api/templates.py`, `backend/app/services/template_service.py`, `backend/app/main.py`, `backend/tests/integration/test_templates_api.py`
- **Skills:** api-conventions, testing-standard
- **Depends on:** T4 · **Parallel-safe:** yes (after T6 merges main.py — coordinate)
- **Do:** CSV (UTF-8 with BOM so Excel opens it correctly) and XLSX (bold header row, frozen pane) with the standard labels, required fields first; `Content-Disposition: attachment; filename="bomviz-template.csv|xlsx"`; 401 without auth; the generated files parse back with T3's parser and the suggestions map every column with confidence 1.0.
- **Verify:** `cd backend && uv run --frozen pytest -q tests/integration/test_templates_api.py && uv run --frozen mypy`

### [x] T9 — Live stack wiring
- **Files:** `docker-compose.yml` (only if needed), `backend/.env.example`, `.env.example`
- **Skills:** appsec-conventions
- **Depends on:** T7 · **Parallel-safe:** no
- **Do:** Rebuild (`docker compose up --build --wait`), apply 0004, restart worker; verify on the live stack: upload a CSV through :5173 → job reaches SUCCEEDED within 30 s, preview returns rows and suggestions, signed URL downloads the identical bytes (compare SHA-256) from the browser-reachable endpoint, re-parse with another delimiter works. Fix config only (e.g. `S3_PUBLIC_ENDPOINT_URL`, Celery task routing); report evidence.
- **Verify:** evidence above captured in the report

### [x] T10 — Frontend BOM API layer and list page
- **Files:** `frontend/src/features/boms/{api.ts,BomsPage.tsx,BomsTable.tsx,BomStatusBadge.tsx,*.test.tsx}`, `frontend/src/tests/boms.ts`, `frontend/src/app/routes.tsx`, `frontend/src/features/workspaces/WorkspaceOverviewPage.tsx`
- **Skills:** component-standards, typescript-conventions, type-safety-patterns, html-semantics-and-accessibility, ui-testing
- **Depends on:** none (contract above) · **Parallel-safe:** yes (with backend tasks)
- **Do:** Types and hooks for every endpoint (multipart upload via `fetch` with `FormData`, bearer token and the same refresh-once behaviour as `apiRequest` — extend `api-client.ts` with an `apiUpload` helper rather than duplicating auth logic), polling rule, `BomsPage` per the Frontend section, overview link. Tests: list, filters in URL, pagination, writer-only upload button, template links, empty/loading/error states.
- **Verify:** `docker compose exec -T frontend npm run -s lint && … format:check && … typecheck && … test`

### [x] T11 — Frontend upload page
- **Files:** `frontend/src/features/boms/{UploadBomPage.tsx,UploadBomForm.tsx,*.test.tsx}`, `frontend/src/app/routes.tsx`
- **Skills:** component-standards, typescript-conventions, html-semantics-and-accessibility, ui-testing
- **Depends on:** T10 · **Parallel-safe:** no
- **Do:** Per the Frontend section. Tests: client rejects wrong extension and > 20 MB without a request; required name; product → revision dependent select; server error messages (UNSUPPORTED_FILE_TYPE, FILE_TOO_LARGE, ENCRYPTED_WORKBOOK); DUPLICATE_UPLOAD shows a link to the existing BOM; success navigates to the detail page; VIEWER sees a not-allowed message.
- **Verify:** same frontend commands

### [x] T12 — Frontend BOM detail and preview
- **Files:** `frontend/src/features/boms/{BomDetailPage.tsx,BomJobStatus.tsx,ReparseForm.tsx,RawPreviewTable.tsx,*.test.tsx}`, `frontend/src/app/routes.tsx`
- **Skills:** component-standards, typescript-conventions, html-semantics-and-accessibility, ui-testing
- **Depends on:** T11 · **Parallel-safe:** no
- **Do:** Per the Frontend section. Tests: polling stops when the job finishes (fake timers); failed job shows code and message; download fetches a signed URL and navigates; re-parse sends only changed options and is disabled while running; preview table renders source row numbers, suggested fields with confidence and the missing-required warning; NOT_PARSED state; VIEWER sees no re-parse form.
- **Verify:** same frontend commands

### [x] T13 — End-to-end upload journey
- **Files:** `frontend/e2e/bom-upload.spec.ts`, `frontend/e2e/fixtures/sample-bom.csv`, `frontend/e2e/fixtures/sample-bom.xlsx` (generated by a small script or committed)
- **Skills:** ui-testing
- **Depends on:** T9, T12 · **Parallel-safe:** no
- **Do:** Register → create workspace → upload CSV → wait for PARSED → preview shows rows and suggested Part number/Quantity → re-parse with header row changed → upload the same file again → duplicate message links to the first BOM → upload XLSX parses → download original returns 200. Run locally with the Playwright image (same command as Phase 1, `-v /work/node_modules`). Respect the 5/hour register limit (one user per run).
- **Verify:** Playwright run all green (smoke + Phase 1 journey + this spec)

### [x] T14 — Documentation
- **Files:** `docs/API.md`, `docs/DATABASE.md`, `docs/ARCHITECTURE.md`, `README.md`
- **Skills:** docs-standard
- **Depends on:** T7, T12 · **Parallel-safe:** yes (with T13)
- **Do:** Mark Phase 2 endpoints ✅ with request/response shapes, limits, error codes and job codes; DATABASE 0004 applied, deviation A12, ERD rows for bom_uploads/import_jobs; ARCHITECTURE "Upload and parsing (Phase 2)" (intake checks, storage keys, job lifecycle, stale rule, parser safety, snapshot); README status and how to try an upload; worker restart note.
- **Verify:** link check passes; `grep -n "Phase 2" docs/API.md README.md`

## Review fixes (full-diff review, 2026-10-11)

One Blocker and seven Should-fix items; all fixed in Phase 2. S3 (stale preview after re-parse,
also found by T13) is fixed in the frontend preview query (keyed by the parse job). Deferred:
proactive token refresh before large uploads (nit 9), redundant `ix_bom_uploads_workspace_id`
(nit 12, kept as specified).

### [x] G1 — Parser hardening (B1, S5, nits 6–8)
- **Skills:** input-validation, testing-standard
- **Do:** XLSX: stop with `PARSE_FILE_TOO_COMPLEX` when a row index exceeds 1,048,576; read with
  `max_col = max_columns + 1` (a non-empty cell beyond → `PARSE_TOO_MANY_COLUMNS`) so wide padded
  rows are never materialized; cap consecutive/total skipped empty rows (→ `PARSE_FILE_TOO_COMPLEX`);
  aggregate compression-ratio check across all members; avoid reading the chosen sheet twice. XLS:
  same column/empty-row caps. CSV: check `len(record)` before converting cells (extra non-empty →
  `PARSE_TOO_MANY_COLUMNS`, extra empty → dropped); `row_number` = physical line where the record
  starts. Tests include the reviewer's crafted files with wall-clock bounds.

### [x] G2 — Parse job ownership, fencing and time limits (B1, S1, S2, nits 4, 10, 11)
- **Skills:** error-handling, resilience-patterns, logging, testing-standard
- **Do:** the API generates the Celery task id and stores it on the job before enqueueing
  (`send_task(task_id=…)`); the worker passes `task.request.id`; a job runs if `QUEUED`, or
  `RUNNING` with the same task id (retry/redelivery) while `attempts < 3`, else it fails with
  `INTERNAL_ERROR`; final writes re-lock (upload, then job) and only apply if the job is still
  `RUNNING` with the same attempts (otherwise the result is dropped and logged); the enqueue-failure
  path only fails a still-`QUEUED` job; `soft_time_limit` 10 min / `time_limit` 11 min on
  `boms.parse` (soft limit → `FAILED PARSE_FILE_TOO_COMPLEX`); exhausted DB retries →
  `INTERNAL_ERROR`, and failures of `mark_job_failed` are logged at error; one shared enqueue
  helper; Celery app construction moved out of the API import path's side effects.

### [x] G3 — Upload intake order and transaction scope (S4, S6, nits 1–3, 5)
- **Skills:** authentication-authorization, input-validation, data-conventions, testing-standard
- **Do:** in the upload route class, before the body is read: authenticate the bearer token
  (same checks as `CurrentUserDep`, 401 without reading the body) and apply a per-user
  `bom_upload_body` limit; end the read-only transaction (product scope, duplicate check) before
  copying/storing the file; `Content-Length` must be ASCII digits; CSV with a UTF-16 BOM must decode
  as UTF-16 in the sniffed head; strip `Zl`/`Zp` from filenames; on ambiguous commit errors keep the
  object and log, and clean up on cancellation.

### [x] G4 — Docs reconciled
- **Skills:** docs-standard
- **Do:** ARCHITECTURE/API reflect G1–G3 and the S3 fix (intake order, worker ownership/fencing,
  time limits, retries, XLSX/CSV guards).

## Review & delivery

- [x] Full-diff review by code-reviewer — 1 Blocker + 7 Should-fix resolved (G1–G4, S3 preview fix)
- [x] Live verification against the stack (T9 probe and a final probe after the fixes: unauthenticated upload 401, parse, preview, duplicate 409, signed download identical bytes, re-parse, audit rows)
- [x] Backend lint, format, typecheck, tests green (906 passed, run in four batches) (`cd backend && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy && uv run --frozen pytest -q`)
- [x] Frontend lint, format, typecheck, tests green (249 passed)
- [x] Playwright suite green (5/5)
- [x] Alembic round trip on the Compose DB (0004 → base → 0004) (`docker compose run --rm migrate sh -c "alembic upgrade head && alembic downgrade base && alembic upgrade head"`), then `docker compose restart backend worker`
- [x] Local commit with `khushishimpi0@gmail.com`, no AI attribution; push only when the user asks
- [ ] Phase summary (files, endpoints, permissions, audit events, job/error codes, verification commands, deviations) — then stop for review before Phase 3

## Retro

**Deviations from the spec (accepted):**
- Upload check order: auth and a per-user body limit (`bom_upload_body` 60/h) run before any body byte; FastAPI parses the form before dependencies, so permission/`bom_upload` limit/product scope follow the form parse. Bad `Content-Length` → 400.
- `bom_uploads.id` is generated in the app (the object key exists before the insert).
- Parse jobs: the API owns the Celery task id; runs are fenced by task id + attempts (max 3); soft/hard time limits; parse runs on the worker's main thread so the soft limit can interrupt it; retries jittered.
- Parser: CSV `header_row`/`row_number` are physical line numbers; delimiter falls back to a per-line consistency heuristic; column names capped at 255; mapping suggestions never cascade to a column's second-best target.
- UTF-16 CSVs (with BOM, decodable) are allowed despite the NUL-byte rule.
- Frontend: the preview query is keyed by the parse job that produced it (S3, also caught by the Playwright journey).

**Known limitations:** XLSX data beyond column `PARSE_MAX_COLUMNS + 1` is not read when that column is empty; a hard-killed parse may stay `RUNNING` until the 15-minute stale rule; cancellation during the storage PUT itself is not cleaned up.

**Deferred:** proactive token refresh before large uploads; redundant `ix_bom_uploads_workspace_id`.

**Skill gaps:** no skill covers untrusted spreadsheet parsing (zip bombs, padded XLSX rows, CSV record width) or Celery job ownership/fencing — both came from review.

**Environment:** low RAM repeatedly killed background jobs and stalled executors; Vite HMR missed bind-mount changes (restart the frontend container); two Playwright containers wedged Docker and the bind mount until `wsl --shutdown` plus a restart.
