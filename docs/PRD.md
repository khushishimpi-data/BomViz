# BOMViz Product Requirements

## Problem

Manufacturing bills of materials live in heterogeneous Excel and CSV files. Engineers,
purchasing and planning teams struggle to understand structure, quantities, suppliers and change
impact without opening and cross-referencing spreadsheets.

BOMViz turns a messy spreadsheet BOM into a validated, traceable, explorable dataset within
minutes.

## Users

| Persona | Needs | Role |
|---|---|---|
| Manufacturing Engineer | Upload, map, validate, import and maintain BOMs | `ENGINEER` |
| Procurement Lead | Suppliers, quantities, costs, purchasing roll-ups | `PROCUREMENT` |
| Production Planner | Assembly structure and component requirements | `PRODUCTION` |
| Quality Auditor | Trace uploads, revisions and source files | `VIEWER` (+ audit access) |
| Admin | Manage users and workspace access | `ADMIN` |

## Core journey

Log in → upload Excel/CSV → choose sheet / delimiter → map columns → validate → review errors
and warnings → preview → confirm import → explore (indented tree, flattened roll-up, where-used,
diff) → export.

## Scope (must have)

1. Excel / CSV upload (≤ 20 MB) with checksum, duplicate detection and original-file retention.
2. Column mapping with auto-suggestion. Required fields: part number, part name, quantity.
3. Row-level validation pipeline: 12 rule codes, with ERROR and WARNING severities.
4. Atomic import with source-row traceability, a status state machine, retry and soft delete.
   Error rows can optionally be excluded, with explicit confirmation (ADR 0001).
5. Indented BOM tree with search, filters, orphan handling and cycle safety.
6. One-click toggle to a flattened purchasing roll-up, using MRP explosion and a configurable
   build quantity.
7. Where-Used reverse lookup: direct and indirect parents, multiple paths, cycle warning.
8. Two-panel diff viewer (indented and flattened modes) with field-level changes.
9. CSV/XLSX export of BOM, roll-up, diff, where-used and validation reports.
10. Authentication, workspace-scoped RBAC, audit logging, import/export history.

## Non-functional requirements

- The BOM dashboard renders a 5,000-row BOM in under 2 seconds.
- Users only ever see data from workspaces they belong to.
- Technical errors are never shown to users. Every error carries a request id.
- Every major action is audit-logged.

## Delivery

Delivery happens in eight phases (0–7), listed in [ARCHITECTURE.md](ARCHITECTURE.md#delivery-phases).
