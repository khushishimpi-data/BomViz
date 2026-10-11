# Database

PostgreSQL 16, schema managed by Alembic (`backend/alembic/versions`). Conventions:

- UUID primary keys, `timestamptz` timestamps, `NUMERIC(18,6)` for quantities and money (never
  floats; `Decimal` in Python).
- Deterministic constraint names (`app/db/base.py`) so autogenerate and downgrades stay stable.
- Every migration has a working `downgrade()`. CI runs upgrade → downgrade → upgrade on every
  push (`tests/integration/test_database.py`).

## Migration plan

| Revision | Phase | Contents | Status |
|---|---|---|---|
| `0001_baseline` | 0 | Extensions `citext`, `pg_trgm` | ✅ applied |
| `0002_auth` | 1 | `users`, `refresh_tokens`, `login_throttles` | ✅ applied |
| `0003_workspaces_products` | 1 | Enum types `user_role`, `product_status`, `revision_status`; `workspaces`, `workspace_members`, `products`, `product_revisions`, `audit_logs` | ✅ applied |
| `0004_bom_uploads` | 2 | `bom_uploads` (+ `parse_options`, `detected_columns`), `import_jobs` | planned |
| `0005_mapping_validation` | 3 | `column_mappings`, `validation_issues` | planned |
| `0006_bom_items` | 4 | `bom_items` with CHECKs and unique child constraint, trigram search index | planned |
| `0007_rollup_cache` | 5 | `bom_rollups` | planned |
| `0008_diff_cache` | 6 | `bom_diff_results` | planned |
| `0009_export_jobs` | 7 | `export_jobs` (+ `export_type`, `parameters`, `workspace_id`) | planned |

Creating extensions requires a privileged role. On managed PostgreSQL, have an administrator
run `0001` or pre-create the extensions.

## Approved deviations from the original schema

| Id | Change | Reason |
|---|---|---|
| A1 | `bom_uploads.parse_options JSONB`, `detected_columns JSONB` | Re-parse the immutable original consistently at preview, validation and import |
| A2 | `export_jobs.export_type`, `parameters JSONB`, `workspace_id`; nullable `bom_upload_id` | One job table serves BOM, roll-up, diff, where-used and validation exports |
| A3 | No `export_jobs.file_url` | Signed URLs expire; `/download` issues a fresh one each time |
| A6 | `UNIQUE NULLS NOT DISTINCT (bom_upload_id, parent_item_id, part_number)`; partial unique `(workspace_id, checksum_sha256) WHERE NOT is_deleted` | Duplicate-child and duplicate-upload rules enforced in the database |
| A10 | `users.is_platform_admin boolean NOT NULL DEFAULT false` replaces `users.role` | Roles are per workspace (`workspace_members.role`). No Phase 1 endpoint uses `is_platform_admin`. |
| A11 | `product_revisions` has no `workspace_id`; it is scoped through its parent `products` row | Avoids a denormalized copy that could drift from the product's workspace |

## Authentication and workspace tables (Phase 1)

- **`users`.** `email` is `citext` and unique. `password_hash` holds an Argon2id hash.
  `is_active` defaults to `true`; `last_login_at` is set on each successful login.
- **`refresh_tokens`.** Only the SHA-256 hash of each token is stored (`token_hash`, unique).
  `family_id` groups the tokens of one login session. When a token is rotated, `revoked_at` is
  set and `replaced_by_id` points to its successor (`ON DELETE SET NULL`); the successor copies
  its `expires_at`, so a session ends `REFRESH_TOKEN_TTL_DAYS` after login. A revoked token with
  `replaced_by_id` set is what makes a later presentation count as reuse; logout sets only
  `revoked_at`. `user_id` uses
  `ON DELETE CASCADE`. Indexed on `user_id` and `family_id`.
- **`login_throttles`.** A keyed counter for failed logins, not an entity, so it has no `id` or
  `created_at`. `key_hash` (primary key) is the SHA-256 hex of `lower(email) + "|" + ip`, where
  `ip` is the client identity (an IPv6 address is reduced to its /64); the raw email and IP are
  never stored. `failure_count` has `CHECK (failure_count >= 0)`.
  `last_failure_at` is indexed for cleanup; `blocked_until` is set once a pair is throttled.
  A successful login deletes the pair's row.
- **`workspaces`.** `slug` is `citext` and unique. `owner_id` uses `ON DELETE RESTRICT`.
- **`workspace_members`.** `UNIQUE (workspace_id, user_id)`; `role` is `user_role`. Both
  foreign keys use `ON DELETE CASCADE`. Removing a member deletes only this row, never the
  user.
- **`products`.** `UNIQUE (workspace_id, product_code)`; `status` defaults to `DRAFT`.
  **`product_revisions`:** `UNIQUE (product_id, revision_code)`; `status` defaults to `DRAFT`.
  On both tables `created_by_id` uses `ON DELETE SET NULL`.
- **`audit_logs`.** Append-only, with `created_at` and no `updated_at`.
  - `workspace_id` uses `ON DELETE RESTRICT`, so history is never cascaded away.
  - `actor_id` uses `ON DELETE SET NULL`.
  - Rows reference `users`, not memberships, so removing a member leaves every audit row they
    authored intact.
  - `request_id` links a row to the request's log lines.
  - `action_metadata` (`jsonb`, default `{}`) never holds emails, IP addresses, passwords,
    tokens or throttle key hashes.
  - Indexed on `(workspace_id, created_at DESC)` and `(actor_id, created_at DESC)`.

## Integrity rules

- `bom_items.quantity > 0` and required fields present, for every row not `EXCLUDED`;
  `unit_cost`, `total_cost` and `lead_time_days` are `NULL` or `>= 0` for all rows.
- `bom_items.parent_item_id <> id`.
- Duplicate child under the same parent: see A6. Enforced after parent resolution, and
  `parent_part_number` is retained for traceability.
- Error rows excluded on explicit user confirmation are stored with `status = 'EXCLUDED'` (see
  [ADR 0001](adr/0001-hierarchy-and-rollup.md)).

## Planned indexes

`workspace_id`, `product_id`, `status`, `(workspace_id, uploaded_at DESC)`,
`(bom_upload_id, part_number)`, `parent_part_number`, `parent_item_id`, `supplier_name`,
`material`, `(bom_upload_id, severity)`, `(bom_upload_id, row_number)`, and a GIN trigram
index on part number / name. The Phase 1 indexes are listed above.

## Entity relationship diagram

```mermaid
erDiagram
    users ||--o{ refresh_tokens : has
    refresh_tokens |o--o| refresh_tokens : "replaced_by_id"
    users ||--o{ workspaces : owns
    users ||--o{ workspace_members : "member of"
    workspaces ||--o{ workspace_members : has
    workspaces ||--o{ products : contains
    workspaces ||--o{ bom_uploads : contains
    workspaces ||--o{ audit_logs : scopes
    products ||--o{ product_revisions : has
    products |o--o{ bom_uploads : "optionally for"
    product_revisions |o--o{ bom_uploads : "optionally for"
    bom_uploads ||--o{ column_mappings : "mapped by"
    bom_uploads ||--o{ bom_items : contains
    bom_uploads ||--o{ validation_issues : produces
    bom_uploads ||--o{ import_jobs : "processed by"
    bom_uploads |o--o{ export_jobs : "exported via"
    bom_uploads ||--o{ bom_rollups : caches
    bom_uploads ||--o{ bom_diff_results : "baseline/target"
    bom_items |o--o{ bom_items : "parent_item_id"
    bom_items |o--o{ validation_issues : "flagged by"
    users ||--o{ audit_logs : acts

    users { uuid id PK; citext email UK; text password_hash; text full_name; bool is_active; bool is_platform_admin; timestamptz last_login_at }
    refresh_tokens { uuid id PK; uuid user_id FK; uuid family_id; text token_hash UK; timestamptz expires_at; timestamptz revoked_at; uuid replaced_by_id FK }
    login_throttles { text key_hash PK; int failure_count; timestamptz last_failure_at; timestamptz blocked_until }
    workspaces { uuid id PK; text name; citext slug UK; uuid owner_id FK }
    workspace_members { uuid id PK; uuid workspace_id FK; uuid user_id FK; user_role role }
    products { uuid id PK; uuid workspace_id FK; text product_code; text name; product_status status; uuid created_by_id FK }
    product_revisions { uuid id PK; uuid product_id FK; text revision_code; revision_status status }
    bom_uploads { uuid id PK; uuid workspace_id FK; text original_file_key; char64 checksum_sha256; upload_status status; jsonb parse_options; jsonb detected_columns; bool is_deleted }
    column_mappings { uuid id PK; uuid bom_upload_id FK; text source_column_name; text target_field }
    bom_items { uuid id PK; uuid bom_upload_id FK; int row_number; text part_number; text parent_part_number; uuid parent_item_id FK; numeric quantity; numeric unit_cost; item_status status; jsonb raw_row_data }
    validation_issues { uuid id PK; uuid bom_upload_id FK; int row_number; text field_name; severity severity; text issue_code }
    import_jobs { uuid id PK; uuid bom_upload_id FK; job_type job_type; job_status status; int attempts }
    export_jobs { uuid id PK; uuid workspace_id FK; uuid bom_upload_id FK; export_type export_type; export_format export_format; export_status status; text file_key }
    audit_logs { uuid id PK; uuid workspace_id FK; uuid actor_id FK; text action; text entity_type; jsonb action_metadata; text request_id }
    bom_rollups { uuid id PK; uuid bom_upload_id FK; numeric build_quantity; text part_number; numeric required_quantity }
    bom_diff_results { uuid id PK; uuid baseline_bom_id FK; uuid target_bom_id FK; diff_view_mode view_mode; jsonb result_payload }
```
