# Database Schema — Hexalog Warehouse Tool V1

**Version:** 1.0 (post-review, all bugs corrected)  
**Status:** Ready for Arpit Review  
**Review date:** 16 June 2026  
**Source:** PRD v1.0 + Staff Engineer Schema Review  
**Stack:** PostgreSQL 16 · Async SQLAlchemy · asyncpg · Alembic

---

## Schema Overview

| Metric | Value |
|--------|-------|
| Total tables | 16 |
| Append-only tables | 4 (`inward_scans`, `outward_scans`, `inventory_ledger_entries`, `audit_logs`) |
| BIGSERIAL PKs | 4 high-volume tables: `inward_scans`, `outward_scans`, `outward_po_lines`, `inventory_ledger_entries` |
| Status enum types | 11 |
| Total indexes | 31 |
| Critical FIFO index | `idx_po_lines_org_ean_remaining` on `outward_po_lines` |
| Critical ledger index | `idx_ledger_org_customer_ean` on `inventory_ledger_entries` |

---

## Table Inventory

| # | Table | Module | Type |
|---|-------|--------|------|
| 1 | `organisations` | Shared | Master |
| 2 | `users` | Shared | Master |
| 3 | `user_organisations` | Shared | Junction |
| 4 | `user_roles` | Shared | Junction |
| 5 | `customers` | Shared | Master |
| 6 | `item_master` | Shared | Optional Master |
| 7 | `audit_logs` | Shared | Append-only |
| 8 | `counters` | Shared | Mutable Counter |
| 9 | `inward_references` | Inward | Transactional |
| 10 | `inward_boxes` | Inward | Transactional |
| 11 | `inward_scans` | Inward | Append-only |
| 12 | `outward_pos` | Outward | Transactional |
| 13 | `outward_po_lines` | Outward | Transactional |
| 14 | `outward_boxes` | Outward | Transactional |
| 15 | `outward_scans` | Outward | Append-only |
| 16 | `inventory_ledger_entries` | Ledger | Append-only |

---

## Column Conventions

> Every table includes `id` (PK), `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`.  
> Transactional tables also include `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` and `created_by INTEGER REFERENCES users(id) ON DELETE SET NULL`.  
> Append-only tables omit `updated_at`.  
> Every table except `organisations` and `users` carries `organisation_id INTEGER NOT NULL REFERENCES organisations(id)`.

---

## Table Definitions

---

### 1. organisations

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| name | TEXT | NOT NULL | — | Display name |
| is_active | BOOLEAN | NOT NULL | true | Soft-disable without deleting |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |

> `slug` column is **not present** — removed in schema review (not in PRD).

---

### 2. users

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| email | TEXT | NOT NULL | — | Login credential. UNIQUE globally. |
| password_hash | TEXT | NOT NULL | — | bcrypt hash |
| full_name | TEXT | NOT NULL | — | Display name |
| is_active | BOOLEAN | NOT NULL | true | Admin can deactivate |
| tokens_invalidated_at | TIMESTAMPTZ | NULL | NULL | JWT invalidation — tokens with `iat` < this value are rejected |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |

**Unique:** `(email)`

---

### 3. user_organisations

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| user_id | INTEGER | NOT NULL | — | FK → users(id) ON DELETE CASCADE |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) ON DELETE CASCADE |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |

**PK:** `(user_id, organisation_id)`

---

### 4. user_roles

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| user_id | INTEGER | NOT NULL | — | FK → users(id) ON DELETE CASCADE |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) ON DELETE CASCADE |
| role | user_role_enum | NOT NULL | — | `admin` / `inward_operator` / `packer` |
| assigned_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |

**Unique:** `(user_id, organisation_id, role)`  
**Note:** Roles are revoked by hard-deleting this row (the only hard-delete in the system). The audit log records the revocation.

---

### 5. customers

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) RESTRICT |
| name | TEXT | NOT NULL | — | Display name |
| code | TEXT | NOT NULL | — | 2–3 uppercase letters. Embedded in Box IDs and Inscan Numbers. |
| status | customer_status_enum | NOT NULL | `active` | `active` / `inactive` |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |

**Unique:** `(organisation_id, code)`  
**Note:** Inactive customers excluded from dropdowns; never hard-deleted (referential integrity).

---

### 6. item_master *(optional)*

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| customer_id | INTEGER | NOT NULL | — | FK → customers(id) |
| ean | TEXT | NOT NULL | — | |
| description | TEXT | NULL | — | Shown during scan feedback |
| image_url | TEXT | NULL | — | Product image URL — shown on Accepted scan so packer can visually verify |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |

**Unique:** `(organisation_id, customer_id, ean)`  
**Note:** If no row exists for a scanned EAN, the system shows the raw code. Operations are never blocked on missing item master data (PRD A-4).

---

### 7. audit_logs *(append-only)*

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | BIGSERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NULL | — | FK → organisations(id). NULL for login events. |
| user_id | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |
| module | audit_module_enum | NOT NULL | — | `shared` / `inward` / `outward` / `reports` |
| action | TEXT | NOT NULL | — | e.g. `inward_box.submitted`, `outward_scan.accepted` |
| resource_type | TEXT | NOT NULL | — | Table name e.g. `inward_boxes` |
| resource_id | **BIGINT** | NULL | — | PK of affected row. **BIGINT** to accommodate BIGSERIAL PKs on scan tables. |
| before_data | JSONB | NULL | — | Row snapshot before change. NULL for inserts. |
| after_data | JSONB | NULL | — | Row snapshot after change. NULL for hard-deletes. Exclude `password_hash`. |
| ip_address | INET | NULL | — | Client IP |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |

**No UPDATE or DELETE ever touches this table.**

---

### 8. counters

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| customer_id | INTEGER | NOT NULL | — | FK → customers(id) |
| counter_type | counter_type_enum | NOT NULL | — | `outward_box` / `inscan_number` |
| period | TEXT | NOT NULL | `'GLOBAL'` | `'GLOBAL'` for Box IDs; `'YYYYMMDD'` (IST) for Inscan Numbers |
| last_value | INTEGER | NOT NULL | 0 | Incremented atomically via UPSERT (see Counter Strategy section) |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |

**Unique:** `(organisation_id, customer_id, counter_type, period)`  
**Note:** `'GLOBAL'` sentinel avoids PostgreSQL's `NULL ≠ NULL` behaviour in unique constraints.

---

### 9. inward_references

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| customer_id | INTEGER | NOT NULL | — | FK → customers(id) RESTRICT |
| po_number | TEXT | NULL | — | At least one of `po_number` / `invoice_number` required — enforced in service layer |
| invoice_number | TEXT | NULL | — | |
| status | inward_reference_status_enum | NOT NULL | `open` | `open` / `completed` |
| completed_at | TIMESTAMPTZ | NULL | — | Set when operator clicks "Finish Delivery" |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |

**Note:** No unique constraint on `(customer_id, po_number, invoice_number)`. PRD handles duplicates via confirm dialog (IN-3). A constraint would block the "append boxes to existing delivery" use case.

---

### 10. inward_boxes

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| inward_reference_id | INTEGER | NOT NULL | — | FK → inward_references(id) RESTRICT |
| box_number | TEXT | NOT NULL | — | From physical carton. Unique within the InwardReference. |
| status | inward_box_status_enum | NOT NULL | `scanning` | `scanning` / `pending_verification` / `completed` |
| scanned_qty | INTEGER | NOT NULL | 0 | CHECK (scanned_qty >= 0). **Maintained by the application** — must be incremented on every scan INSERT and decremented on every scan soft-delete in the same transaction. |
| physical_qty | INTEGER | NULL | — | Manual count entered at verification. Submission blocked until `physical_qty = scanned_qty`. |
| inscan_number | TEXT | NULL | — | Generated on submission. Format: `INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>` |
| submitted_at | TIMESTAMPTZ | NULL | — | Timestamp of successful submission |
| is_deleted | BOOLEAN | NOT NULL | false | Admin soft-delete of abandoned boxes |
| deleted_at | TIMESTAMPTZ | NULL | — | |
| deleted_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |

**Unique:** `(inward_reference_id, box_number)` — enforces IN-4 at DB level.  
**Unique (partial, org-scoped):** `(organisation_id, inscan_number) WHERE inscan_number IS NOT NULL` — prevents duplicate Inscan Numbers within an organisation.

> **Critical invariant on `scanned_qty`:** The value must equal `COUNT(*) FROM inward_scans WHERE inward_box_id = ? AND is_deleted = false`. The application service layer owns this invariant. No DB trigger enforces it; the `CHECK (scanned_qty >= 0)` is the safety net against underflow bugs.

---

### 11. inward_scans *(append-only)*

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | BIGSERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| inward_box_id | INTEGER | NOT NULL | — | FK → inward_boxes(id) |
| code_scanned | TEXT | NOT NULL | — | Raw EAN or style code |
| code_type | inward_code_type_enum | NOT NULL | — | `ean` / `style_code` |
| is_manual_entry | BOOLEAN | NOT NULL | false | True when typed rather than scanned |
| is_deleted | BOOLEAN | NOT NULL | false | Soft delete — row retained for audit |
| deleted_at | TIMESTAMPTZ | NULL | — | |
| deleted_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |
| scanned_at | TIMESTAMPTZ | NOT NULL | now() | Serves as `created_at` |
| scanned_by | INTEGER | NOT NULL | — | FK → users(id) ON DELETE SET NULL |

**Note:** Rows are inserted and soft-deleted only. `is_deleted` is the only field that changes after insert. All other fields are immutable.

---

### 12. outward_pos

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| customer_id | INTEGER | NOT NULL | — | FK → customers(id) RESTRICT |
| po_number | TEXT | NOT NULL | — | From uploaded file |
| status | outward_po_status_enum | NOT NULL | `open` | `open` / `closed` |
| uploaded_at | TIMESTAMPTZ | NOT NULL | now() | **Drives FIFO allocation order.** Do not allow the application to override this value. |
| uploaded_by | INTEGER | NOT NULL | — | FK → users(id) ON DELETE SET NULL |
| closed_at | TIMESTAMPTZ | NULL | — | |
| closed_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |

**Note:** No unique constraint on `(organisation_id, po_number)`. Two uploads for the same PO number may represent amendments. The preview step (OUT-2) surfaces duplicates to the Admin.

---

### 13. outward_po_lines

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | BIGSERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| outward_po_id | INTEGER | NOT NULL | — | FK → outward_pos(id) RESTRICT |
| ean | TEXT | NOT NULL | — | EAN after consolidation |
| ordered_qty | INTEGER | NOT NULL | — | CHECK (ordered_qty > 0) |
| packed_qty | INTEGER | NOT NULL | 0 | CHECK (packed_qty >= 0 AND packed_qty <= ordered_qty). Incremented atomically by conditional UPDATE. |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |

**Unique:** `(outward_po_id, ean)` — consolidation enforced at DB level.  
**Critical:** The `CHECK (packed_qty <= ordered_qty)` constraint combined with `UPDATE ... WHERE packed_qty < ordered_qty` makes over-packing impossible by construction (NFR-4).

---

### 14. outward_boxes

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | SERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| box_id | TEXT | NOT NULL | — | Format: `B-<CUSTCODE>-<6-digit>`. Unique within the organisation. |
| customer_id | INTEGER | NOT NULL | — | FK → customers(id) RESTRICT |
| status | outward_box_status_enum | NOT NULL | `open` | `open` / `in_use` / `closed` |
| print_count | INTEGER | NOT NULL | 0 | Incremented on every (re)print |
| closed_at | TIMESTAMPTZ | NULL | — | Set on Mark Box Full or admin force-close |
| closed_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| updated_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL. This field identifies the packer. |

**Unique:** `(organisation_id, box_id)` — org-scoped. Two tenants with the same customer code will not collide.  
**Partial unique:** `(created_by) WHERE status = 'in_use'` — enforces one active box per packer at DB level (OUT-10).

---

### 15. outward_scans *(append-only with one permitted transition)*

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | BIGSERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| outward_box_id | INTEGER | NOT NULL | — | FK → outward_boxes(id) |
| ean | TEXT | NOT NULL | — | As scanned |
| result | outward_scan_result_enum | NOT NULL | — | `accepted` / `rejected` / `deleted` |
| reject_reason | TEXT | NULL | — | Populated for `rejected` only. See PRD §11 for verbatim messages. |
| allocated_po_line_id | BIGINT | NULL | — | FK → outward_po_lines(id). NULL for `rejected` scans. |
| stock_flagged | BOOLEAN | NOT NULL | false | True if ledger balance ≤ 0 at scan time (informational only — scan still accepted) |
| scanned_at | TIMESTAMPTZ | NOT NULL | now() | |
| scanned_by | INTEGER | NOT NULL | — | FK → users(id) ON DELETE SET NULL |
| deleted_at | TIMESTAMPTZ | NULL | — | Set when result transitions `accepted` → `deleted` |
| deleted_by | INTEGER | NULL | — | FK → users(id) ON DELETE SET NULL |

**Permitted mutation:** `result`, `deleted_at`, `deleted_by` may be updated exactly once, from `accepted` to `deleted`. All other fields are immutable post-insert. This is the only permitted update in this table.

---

### 16. inventory_ledger_entries *(append-only)*

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | BIGSERIAL | NOT NULL | auto | PK |
| organisation_id | INTEGER | NOT NULL | — | FK → organisations(id) |
| customer_id | INTEGER | NOT NULL | — | FK → customers(id) |
| ean | TEXT | NOT NULL | — | Stock tracked per customer per EAN |
| qty_delta | INTEGER | NOT NULL | — | `+1` (inward or reversal) or `−1` (outward scan) |
| source_type | ledger_source_type_enum | NOT NULL | — | `inward_submission` / `outward_scan` / `outward_scan_deletion` |
| source_id | BIGINT | NOT NULL | — | PK of the causing row: `inward_boxes.id` or `outward_scans.id`. Polymorphic — no FK constraint. |
| created_at | TIMESTAMPTZ | NOT NULL | now() | |
| created_by | INTEGER | NOT NULL | — | FK → users(id) ON DELETE SET NULL |

**No UPDATE or DELETE ever touches this table (LG-1).**  
**Balance query:** `SELECT SUM(qty_delta) FROM inventory_ledger_entries WHERE organisation_id = ? AND customer_id = ? AND ean = ?` — covered by `idx_ledger_org_customer_ean`.

---

## Enum Types

| Enum Name | Values |
|-----------|--------|
| `user_role_enum` | `admin` · `inward_operator` · `packer` |
| `customer_status_enum` | `active` · `inactive` |
| `inward_reference_status_enum` | `open` · `completed` |
| `inward_box_status_enum` | `scanning` · `pending_verification` · `completed` |
| `inward_code_type_enum` | `ean` · `style_code` |
| `outward_po_status_enum` | `open` · `closed` |
| `outward_box_status_enum` | `open` · `in_use` · `closed` |
| `outward_scan_result_enum` | `accepted` · `rejected` · `deleted` |
| `ledger_source_type_enum` | `inward_submission` · `outward_scan` · `outward_scan_deletion` |
| `audit_module_enum` | `shared` · `inward` · `outward` · `reports` |
| `counter_type_enum` | `outward_box` · `inscan_number` |

---

## Indexes — Complete List

### user_organisations
| Index | Columns | Type |
|-------|---------|------|
| PK | `(user_id, organisation_id)` | Primary |
| `idx_user_orgs_org_id` | `(organisation_id)` | B-tree |

### user_roles
| Index | Columns | Type |
|-------|---------|------|
| `uq_user_roles` | `(user_id, organisation_id, role)` | Unique |
| `idx_user_roles_org_id` | `(organisation_id)` | B-tree |

### customers
| Index | Columns | Type |
|-------|---------|------|
| `uq_customers_org_code` | `(organisation_id, code)` | Unique |
| `idx_customers_org_status` | `(organisation_id, status)` | B-tree |

### audit_logs
| Index | Columns | Type |
|-------|---------|------|
| `idx_audit_org_created` | `(organisation_id, created_at)` | B-tree |
| `idx_audit_resource` | `(resource_type, resource_id)` | B-tree |
| `idx_audit_user` | `(user_id, created_at)` | B-tree |

### counters
| Index | Columns | Type |
|-------|---------|------|
| `uq_counters` | `(organisation_id, customer_id, counter_type, period)` | Unique |

### inward_references
| Index | Columns | Type |
|-------|---------|------|
| `idx_inward_refs_customer` | `(customer_id)` | B-tree |
| `idx_inward_refs_org_status` | `(organisation_id, status)` | B-tree |
| `idx_inward_refs_customer_po` | `(customer_id, po_number)` | B-tree |
| `idx_inward_refs_customer_inv` | `(customer_id, invoice_number)` | B-tree |

### inward_boxes
| Index | Columns | Condition | Type |
|-------|---------|-----------|------|
| `uq_inward_boxes_ref_boxnum` | `(inward_reference_id, box_number)` | — | Unique |
| `uq_inward_boxes_org_inscan` | `(organisation_id, inscan_number)` | `WHERE inscan_number IS NOT NULL` | Partial Unique |
| `idx_inward_boxes_reference` | `(inward_reference_id)` | — | B-tree |
| `idx_inward_boxes_org_status` | `(organisation_id, status)` | — | B-tree |
| `idx_inward_boxes_created_by` | `(created_by)` | — | B-tree |

### inward_scans
| Index | Columns | Condition | Type |
|-------|---------|-----------|------|
| `idx_inward_scans_box` | `(inward_box_id)` | — | B-tree |
| `idx_inward_scans_box_active` | `(inward_box_id)` | `WHERE is_deleted = false` | Partial B-tree |

### outward_pos
| Index | Columns | Type |
|-------|---------|------|
| `idx_outward_pos_customer` | `(customer_id)` | B-tree |
| `idx_outward_pos_org_status` | `(organisation_id, status)` | B-tree |
| `idx_outward_pos_org_uploaded` | `(organisation_id, uploaded_at)` | B-tree |

### outward_po_lines
| Index | Columns | Condition | Type |
|-------|---------|-----------|------|
| `uq_po_lines_po_ean` | `(outward_po_id, ean)` | — | Unique |
| `idx_po_lines_po_id` | `(outward_po_id)` | — | B-tree |
| `idx_po_lines_org_ean` | `(organisation_id, ean)` | — | B-tree |
| **`idx_po_lines_org_ean_remaining`** | **`(organisation_id, ean)`** | **`WHERE packed_qty < ordered_qty`** | **Partial B-tree — critical FIFO index** |

### outward_boxes
| Index | Columns | Condition | Type |
|-------|---------|-----------|------|
| `uq_outward_boxes_org_boxid` | `(organisation_id, box_id)` | — | Unique |
| `uq_outward_boxes_packer_active` | `(created_by)` | `WHERE status = 'in_use'` | Partial Unique |
| `idx_outward_boxes_customer` | `(customer_id)` | — | B-tree |
| `idx_outward_boxes_org_status` | `(organisation_id, status)` | — | B-tree |
| `idx_outward_boxes_created_by` | `(created_by)` | — | B-tree |

### outward_scans
| Index | Columns | Type |
|-------|---------|------|
| `idx_outward_scans_box` | `(outward_box_id)` | B-tree |
| `idx_outward_scans_scanned_by` | `(scanned_by)` | B-tree |
| `idx_outward_scans_alloc_line` | `(allocated_po_line_id)` | B-tree |
| `idx_outward_scans_box_result` | `(outward_box_id, result)` | B-tree |

### inventory_ledger_entries
| Index | Columns | Type |
|-------|---------|------|
| **`idx_ledger_org_customer_ean`** | **`(organisation_id, customer_id, ean)`** | **B-tree — critical for balance queries** |
| `idx_ledger_source` | `(source_type, source_id)` | B-tree |
| `idx_ledger_created_at` | `(organisation_id, created_at)` | B-tree |

---

## Counter Generation Strategy

### Box ID — `B-<CUSTCODE>-<6-digit>`

Counters table key: `(organisation_id, customer_id, 'outward_box', 'GLOBAL')`

Access pattern (single atomic statement):
```sql
INSERT INTO counters (organisation_id, customer_id, counter_type, period, last_value, updated_at)
VALUES (:org, :customer, 'outward_box', 'GLOBAL', :batch_size, now())
ON CONFLICT (organisation_id, customer_id, counter_type, period)
DO UPDATE SET
last_value = counters.last_value + :batch_size,
updated_at = now()
RETURNING last_value
```

`range_end = RETURNING last_value`  
`range_start = range_end - batch_size + 1`  
Box IDs: `B-<code>-{range_start:06d}` through `B-<code>-{range_end:06d}`
### Inscan Number — `INS-<CUSTCODE>-<YYYYMMDD>-<4-digit>`
Counters table key: `(organisation_id, customer_id, 'inscan_number', 'YYYYMMDD')` where the date string is today in `APP_TIMEZONE` (e.g. `Asia/Kolkata`), computed by the application before the DB call.
Access pattern — executed inside the box submission transaction:
```sql
INSERT INTO counters (organisation_id, customer_id, counter_type, period, last_value, updated_at)
VALUES (:org, :customer, 'inscan_number', :today_str, 1, now())
ON CONFLICT (organisation_id, customer_id, counter_type, period)
DO UPDATE SET
last_value = counters.last_value + 1,
updated_at = now()
RETURNING last_value
```

`inscan_number = 'INS-' + customer.code + '-' + today_str + '-' + str(last_value).zfill(4)`
This UPSERT replaces the original `SELECT FOR UPDATE` approach. It is race-free on first use, requires no retry loop, and handles concurrent batch creates without a separate initialization step.
---
## Inventory Ledger Strategy
The ledger is an **event log** — not a running balance table.
**Balance = `SUM(qty_delta)`** per `(organisation_id, customer_id, ean)`, computed on read.
### Events
| Event | `qty_delta` | `source_type` | `source_id` |
|-------|-------------|--------------|-------------|
| Inward box submitted | +1 per non-deleted scan in the box | `inward_submission` | `inward_box.id` |
| Outward scan Accepted | −1 | `outward_scan` | `outward_scan.id` |
| Accepted outward scan deleted | +1 | `outward_scan_deletion` | `outward_scan.id` |
All ledger entries are written in the **same transaction** as the event that causes them (LG-1). A submitted box with no ledger entries is impossible.
### Stock-flag logic (LG-2)
Before writing the −1 entry for an accepted outward scan:
1. Compute `SUM(qty_delta)` for `(organisation_id, customer_id, ean)` in the current transaction
2. If balance ≤ 0 → set `outward_scans.stock_flagged = true`
3. Write the −1 entry regardless — packing is never blocked
4. UI shows: *"Note: no recorded inward stock for this item."* (informational only)
---
## ER Diagram
┌─────────────────────────────────────────────────────────────────────────────┐
│ SHARED PLATFORM │
│ │
│ ┌───────────────┐ ┌───────────────────────┐ ┌───────────────┐ │
│ │ organisations │───│ user_organisations │───│ users │ │
│ └───────┬───────┘ └───────────────────────┘ └───────┬───────┘ │
│ │ │ │
│ │ ┌───────────────────────┐ │ │
│ └──────────>│ user_roles │<──────────┘ │
│ │ └───────────────────────┘ │
│ │ │
│ │ ┌──────────────────┐ ┌─────────────┐ ┌──────────────────┐ │
│ └──>│ customers │──>│ item_master │ │ audit_logs │ │
│ └────────┬─────────┘ └─────────────┘ └──────────────────┘ │
│ │ │
│ ┌────────┴──────────────────────────────┐ │
│ │ counters (outward_box + inscan_num) │ │
│ └────────────────────────────────────────┘ │
└─────────────────────────┬──────────────────────────┬──────────────────────────┘
│ │
┌────────────────┘ └────────────────┐
│ │
▼ INWARD MODULE ▼ OUTWARD MODULE
┌──────────────────────┐ ┌─────────────────────┐
│ inward_references │ │ outward_pos │
│ ────────────────── │ │ ───────────────── │
│ customer_id (FK) │ │ customer_id (FK) │
│ po_number │ │ po_number │
│ invoice_number │ │ status │
│ status │ │ uploaded_at ←FIFO │
└──────────┬───────────┘ └──────────┬──────────┘
│ │
▼ ▼
┌──────────────────────┐ ┌─────────────────────┐
│ inward_boxes │ │ outward_po_lines │
│ ────────────────── │ │ ───────────────── │
│ box_number │ │ ean │
│ status │ │ ordered_qty │
│ scanned_qty ≥ 0 │ │ packed_qty ←atomic │
│ physical_qty │ └──────────┬──────────┘
│ inscan_number │ │ allocated_to
└──────────┬───────────┘ │
│ │
▼ ▼
┌──────────────────────┐ ┌─────────────────────┐
│ inward_scans │ │ outward_boxes │
│ (append-only) │ │ ───────────────── │
│ ────────────────── │ │ box_id (org-unique)│
│ code_scanned │ │ status │
│ code_type │ │ print_count │
│ is_deleted │ └──────────┬──────────┘
└──────────┬───────────┘ │
│ ▼
│ ┌─────────────────────┐
│ │ outward_scans │
│ │ (append-only) │
│ │ ───────────────── │
│ │ result (acc/rej/del│
│ │ allocated_line(FK) │
│ │ stock_flagged │
│ └──────────┬──────────┘
│ │
│ INVENTORY LEDGER │
│ ┌──────────────────────────────────┐ │
└───>│ inventory_ledger_entries │<───────────────┘
(inward_submit) │ ────────────────────────────── │ (outward_scan)
│ customer_id + ean │
│ qty_delta (+1 / −1) │
│ source_type + source_id │
└──────────────────────────────────┘

---
## Schema Revision History
| Rev | Date | Change | Bug Fixed |
|-----|------|--------|-----------|
| 1.0 | 12 Jun 2026 | Initial design from PRD | — |
| 1.1 | 16 Jun 2026 | `audit_logs.resource_id` INTEGER → BIGINT | BUG-1 |
| 1.1 | 16 Jun 2026 | `outward_boxes` UNIQUE global → `(organisation_id, box_id)` | BUG-2 |
| 1.1 | 16 Jun 2026 | `inward_boxes` inscan_number unique global → `(organisation_id, inscan_number)` partial | BUG-3 |
| 1.1 | 16 Jun 2026 | FIFO index leading column `outward_po_id` → `organisation_id` | BUG-4 |
| 1.1 | 16 Jun 2026 | Added `idx_inward_refs_customer_po` and `idx_inward_refs_customer_inv` | BUG-5 |
| 1.1 | 16 Jun 2026 | Added `idx_user_orgs_org_id` and `idx_user_roles_org_id` | BUG-6 |
| 1.1 | 16 Jun 2026 | Counter strategy: SELECT FOR UPDATE → atomic UPSERT | BUG-7 |
| 1.1 | 16 Jun 2026 | `organisations.slug` column removed (not in PRD) | BUG-8 |
| 1.1 | 16 Jun 2026 | `inward_boxes.scanned_qty` added `CHECK (scanned_qty >= 0)` | BUG-9 |
| 1.1 | 16 Jun 2026 | Alembic env.py: two-URL strategy documented (psycopg2 for migrations, asyncpg for app) | BUG-10 |