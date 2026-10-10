# ADR 0001: BOM hierarchy model, roll-up semantics and error-row exclusion

- **Status:** Accepted (2026-10-10)
- **Deciders:** Product owner, engineering

## Context

Uploaded spreadsheets express hierarchy in different ways (a `parent_part_number` column, an
integer `bom_level`, or a dotted find number such as `1.2.3`). The same sub-assembly can be used
under several parents. Purchasing needs consolidated quantities, and imports must not be blocked
forever by a handful of bad rows.

## Decisions

### 1. Hierarchy source and parent resolution

1. If `parent_part_number` is mapped, it defines the parent.
2. Otherwise `bom_level` (integer or dotted) defines the parent positionally, using a stack of
   the most recent item seen at each level.
3. `parent_item_id` is resolved **after validation, during import**:
   - level-based BOMs: the positional parent row;
   - parent-number BOMs: the single row with that part number, or the first occurrence when the
     part appears more than once.
4. `parent_part_number` is always kept as imported, for source traceability.
5. All traversals (tree, roll-up, where-used, cycle detection) track the current path and stop
   at a node already on that path, reporting the cycle instead of recursing.

### 2. Duplicate children

Enforced in the database after parent resolution:
`UNIQUE NULLS NOT DISTINCT (bom_upload_id, parent_item_id, part_number)` (PostgreSQL 15+).
`NULLS NOT DISTINCT` makes the rule also apply to root-level items. The validation rule
`DUPLICATE_PART_UNDER_PARENT` reports the same condition earlier, as a row-level error.

### 3. Roll-up uses standard MRP explosion (multiplied quantities)

A component's quantity per finished unit is the product of the quantities along its path,
summed over every path where it appears:

```
quantity_per_unit(part) = Σ over paths (Π of quantities along the path, root → part)
required_quantity       = quantity_per_unit × build_quantity
extended_cost           = required_quantity × unit_cost
```

Example: an M4 screw used 4×, 2× and 6× in three assemblies, each assembly used once per
product: 12 per unit, 1,200 for 100 units, and ₹2,400 extended at ₹2 each. If the first
assembly were used 2× per product, the screw would need 16 per unit instead of 12.

`levels_used_in` counts the distinct depths the part appears at. Items with status `ERROR` or
`EXCLUDED` are skipped together with their descendants.

### 4. Optional exclusion of error rows

By default, blocking errors prevent import. The user may instead explicitly confirm
**"Exclude error rows and import the rest"**:

- Error rows are stored with status `EXCLUDED`, keeping `raw_row_data` and `row_number` for
  traceability. They are never committed with a status that makes them look valid.
- Rows whose parent is excluded are excluded as well, so no orphaned branch is presented as
  complete.
- Excluded rows do not appear in the tree, flattened roll-up, where-used results, diff or
  exports. The validation report still lists them.
- The import request must carry `exclude_error_rows: true` and `confirm_exclusion: true`.
  The audit log records a `bom.rows_excluded` event with the count and row numbers.

An excluded row can hold values that would violate the row constraints (e.g. quantity `-2` or
a missing part number). Every excluded row is therefore stored in `bom_items` with status
`EXCLUDED`, and the original values are kept in `raw_row_data`. The constraints are
written so that they stay strict for every row that is not excluded:

```sql
CHECK (status = 'EXCLUDED' OR quantity > 0)
CHECK (status = 'EXCLUDED' OR (part_number IS NOT NULL AND part_name IS NOT NULL))
CHECK (unit_cost IS NULL OR unit_cost >= 0)  -- invalid values of an excluded row are stored as NULL
```

Structured columns of an excluded row hold a value only if it parsed cleanly. Anything else is
`NULL`, with the raw text in `raw_row_data`.

## Consequences

- Roll-up and diff (flattened mode) need the full graph in memory. 5,000 rows are well within
  budget; results are cached in `bom_rollups` / `bom_diff_results` per immutable upload.
- Ambiguous parent-number BOMs still render correctly, because traversal is keyed by part
  number, not by `parent_item_id`.
