# Architecture Overview — Hexalog Warehouse Tool V1

**Version:** 1.0  
**Date:** 16 June 2026  
**Status:** Approved

---

## System Overview

The Hexalog Warehouse Tool is a multi-tenant web application used inside the warehouse on desktops and laptops with USB barcode scanners. It has two operational modules on a shared platform:

- **Inward (Inscan):** Operators receive incoming cartons, scan items, verify counts, and submit. Each submission generates a unique Inscan Number.
- **Outward (Packing):** Admins upload customer POs. Packers generate box labels, scan items into boxes against open POs, and mark boxes full. The system validates and FIFO-allocates every scan.
- **Shared Platform:** Authentication, RBAC, organisation multi-tenancy, customer master, inventory ledger, reports, and audit trail.

A barcode scanner behaves as a keyboard — it types the scanned code followed by Enter. Scan input fields must maintain keyboard focus aggressively at all times (NFR-6).

---

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend | React 18 + Vite + TypeScript | Fast build tooling, type safety, component reuse across admin and packer views |
| Backend | FastAPI (async) | High-throughput async request handling; critical for < 300 ms scan NFR; auto-generated OpenAPI docs |
| Database | PostgreSQL 16 | Transactional integrity for concurrent scan allocation; native enum types; partial unique indexes; conditional UPDATEs |
| ORM | Async SQLAlchemy + asyncpg | Non-blocking DB I/O; asyncpg is the fastest PostgreSQL Python driver |
| Migrations | Alembic | Schema version control; autogenerate from SQLAlchemy models |
| Auth | JWT + `tokens_invalidated_at` | Stateless tokens; force-logout without a session store |
| App Logging | structlog | Structured JSON log output; consistent `key=value` context across all log lines |
| Audit Logging | `audit_logs` DB table | Business-event audit visible to Admins in the UI; written in the same transaction as the event |
| PDF Generation | WeasyPrint | On-demand box label PDFs; one label per page; A6 / 4×6 inch thermal stock |

> **Redis is explicitly rejected for V1.** No session store, no cache, no queue. Keep the deployment footprint minimal.

---

## System Architecture
┌─────────────────────────────────────────────────────────────────────┐
│ Browser (Warehouse PC) │
│ React + Vite + TypeScript │
│ Inward Scan UI │ Pack Items UI │ Admin UI │ Reports UI │
└────────────────────────────┬────────────────────────────────────────┘
│ HTTPS / REST + JSON
▼
┌─────────────────────────────────────────────────────────────────────┐
│ FastAPI Application │
│ │
│ /api/auth /api/inward /api/outward /api/reports │
│ │
│ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐ │
│ │ JWT Auth │ │ Inward │ │ Outward │ │
│ │ Middleware │ │ Service │ │ Service │ │
│ └─────────────┘ └──────────────┘ └──────────────┘ │
│ │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ Async SQLAlchemy Session (asyncpg) │ │
│ └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────┬────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────────────┐
│ PostgreSQL 16 │
│ │
│ │ organisations  users  user_organisations  user_roles  customers │
│ item_master  counters  audit_logs                               │
│ inward_references inward_boxes inward_scans │
│ outward_pos outward_po_lines outward_boxes outward_scans │
│ inventory_ledger_entries │
└─────────────────────────────────────────────────────────────────────┘
▲
│ (migration time only — sync psycopg2)
┌────────┴────────┐
│ Alembic CLI │
└─────────────────┘

---
## Module Breakdown
### Shared Platform
- **Authentication:** JWT access tokens with 12-hour TTL. Force-logout is implemented via `tokens_invalidated_at` on the `users` table — tokens issued before this timestamp are rejected.
- **RBAC:** Three roles (`admin`, `inward_operator`, `packer`) enforced on every API endpoint. Role checks happen in the service layer, not just the UI. One user may hold multiple roles.
- **Multi-tenancy:** Every request includes the active `organisation_id` from the JWT payload. Every table carries `organisation_id`. All queries filter by it. Admins cannot see data from other organisations.
- **Customer Master:** Shared between both modules. Customer `code` (2–3 uppercase letters) is embedded in generated IDs. Inactive customers are excluded from dropdowns.
- **Audit Log:** Application-layer service writes to `audit_logs` in the same transaction as every business event. structlog mirrors each write to stdout for log aggregation. Never written by a DB trigger.
### Inward Module
Single-page six-step flow. Each step unlocks the next. The operator handles one box at a time; customer and PO selection persist across boxes in the same delivery.
Select Customer → Enter PO/Invoice → Enter Box Number
→ Scan Items (EAN / Style Code) → Complete Box & Verify Count → Submit
↑ ↑
[Reopen Box] [Inscan Number generated]

Submission is atomic: Inscan Number generation, box status update, and all ledger entries succeed or fail together in one transaction.
### Outward Module
Two actors: Admin uploads POs; Packers pack against them.
Admin: Upload PO CSV/XLSX → Preview → Confirm → PO is Open
↕ Toggle
PO is Closed

Packer: Scan Box Label (or type Box ID) → Scan EANs → [Accepted / Rejected]
↓
Mark Box Full → Box Closed

The Pack Items scan path runs in under 300 ms via the FIFO allocation query backed by a partial composite index on `outward_po_lines`.
### Inventory Ledger
An append-only event log. Balance = `SUM(qty_delta)` per `(organisation_id, customer_id, ean)`, computed on read. The ledger never blocks any operation in V1 — it provides visibility and feeds the Inventory Variance Report. Negative balances are expected during adoption.
---
## Authentication Flow
POST /api/auth/login
→ verify email + password_hash
→ check users.is_active
→ check organisation membership via user_organisations
→ issue JWT: { user_id, organisation_id, roles[], iat, exp }

Every subsequent request:
→ extract JWT from Authorization: Bearer header
→ verify signature and exp
→ check users.tokens_invalidated_at: reject if iat < tokens_invalidated_at
→ inject user + org context into request state
→ service layer checks role before proceeding

**12-hour session:** The access token expires 12 hours after issuance. This is a 12-hour working-session model, not a true inactivity timeout. Agreed V1 simplification — no refresh token mechanism.
---
## Key Architectural Decisions
| Decision | What Was Chosen | Why |
|----------|----------------|-----|
| D-1 | One tool, two modules | Both modules share customers, scanning, boxes, reports, audit, and roles |
| D-2 | Two separate PO entities (InwardReference / OutwardPO) | Different parties, fields, and lifecycles |
| D-3 | Ledger tracks, never blocks | Gives visibility without making operations dependent on perfect inward data on day one |
| D-4 | Three roles; one user can hold multiple | Unified from two conflicting BRDs |
| — | Integer PKs throughout | Simpler; BIGSERIAL only on high-volume scan/ledger tables |
| — | FIFO over explicit PO selection | Deterministic allocation; prevents human error in PO assignment |
| — | `packed_qty <= ordered_qty` CHECK + conditional UPDATE | Two-layer concurrency protection; over-packing impossible by construction |
| — | Counter UPSERT (not SELECT FOR UPDATE) | Race-free on first use per customer; single atomic SQL statement |
| — | `outward_scans` result: Accepted → Deleted (one-way) | Satisfies PRD's soft-delete requirement while keeping the row for audit |
| — | Alembic uses sync psycopg2 URL; app uses asyncpg | asyncpg is async-only; Alembic runs synchronously. Two separate env vars. |
---
## FIFO Allocation — Concurrency Design
The scan validation path is the most concurrency-sensitive part of the system.
EAN scan received
│
▼ (inside one DB transaction)
Query outward_po_lines
WHERE organisation_id = :org
AND ean = :ean
AND packed_qty < ordered_qty ← uses idx_po_lines_org_ean_remaining
JOIN outward_pos ON status = 'open'
ORDER BY uploaded_at ASC, id ASC
LIMIT 1
│
├─ No rows → Reject: "EAN not found in open POs"
├─ All full → Reject: "Quantity complete for all open POs"
│
▼ Row found (target line)
UPDATE outward_po_lines
SET packed_qty = packed_qty + 1
WHERE id = :line_id
AND packed_qty < ordered_qty ← atomic guard prevents over-pack
│
├─ 0 rows affected → concurrent race; retry from top (max 5)
│
▼ 1 row affected
Write OutwardScan (Accepted)
Write −1 InventoryLedgerEntry
│
COMMIT → return feedback < 300 ms

PostgreSQL's UPDATE re-evaluates the WHERE clause after waiting for any concurrent lock on the same row. This means the guard `packed_qty < ordered_qty` is checked against the committed state — concurrent packers cannot both take the last unit.
---
## Inward Submission — Transaction Design
Operator clicks Submit
│
▼ (one DB transaction)
Validate physical_qty == scanned_qty ← application layer assertion
│
UPSERT counters:
INSERT (org, customer, 'inscan_number', 'YYYYMMDD', 1)
ON CONFLICT DO UPDATE last_value + 1
RETURNING last_value ← daily sequence number
│
Format inscan_number = INS-{code}-{date}-{seq:04d}
│
UPDATE inward_boxes SET status='completed', inscan_number=..., submitted_at=now()
│
INSERT INTO inventory_ledger_entries ← one row per non-deleted scan
│
INSERT INTO audit_logs
│
COMMIT

If any step fails, the entire transaction rolls back. The box stays in `pending_verification`. The counter is not incremented. No Inscan Number is issued.
---
## Generated ID Formats
| ID | Format | Example | Counter Type |
|----|--------|---------|-------------|
| Box ID | `B-<CUSTCODE>-<6-digit seq>` | `B-KIR-000001` | Per organisation + customer; never resets |
| Inscan Number | `INS-<CUSTCODE>-<YYYYMMDD>-<4-digit seq>` | `INS-NM-20260611-0001` | Per organisation + customer + date (IST); resets daily |
Both counters use the same atomic UPSERT on the `counters` table:
INSERT INTO counters (..., last_value) VALUES (..., :N)
ON CONFLICT (...) DO UPDATE SET last_value = counters.last_value + :N
RETURNING last_value

`range_end = RETURNING last_value`; `range_start = range_end - N + 1`.
---
## Error Messages (Verbatim — from PRD §11)
These messages are fixed. UI must use them exactly as written.
| Condition | Message |
|-----------|---------|
| Same customer + PO/invoice already inwarded | *An inward already exists for this PO/Invoice. Continue adding boxes to it?* |
| Duplicate box number within PO/invoice | *Box number already used for this PO/Invoice. Enter a different box number.* |
| Manual count ≠ scanned qty | *Scanned Quantity and Physical Quantity do not match. Please verify before submission.* |
| EAN on no open PO | *EAN not found in open POs* |
| EAN exists but every open PO line is full | *Quantity complete for all open POs* |
| Packer scans second box with one active | *Mark the current box full before starting another box.* |
| Scanned/entered box is Closed | *This box is closed. Showing details in read-only mode.* |
| Manual Box ID doesn't exist | *Box ID not found* |
| Accepted scan but no inward stock | *Note: no recorded inward stock for this item.* |
| Missing required column in PO upload | *Upload failed: missing required column(s): \<names\>* |
---
## Open Items Before Build
| Item | Needed Before | Owner |
|------|--------------|-------|
| Confirm `APP_TIMEZONE=Asia/Kolkata` with Ops (affects Inscan Number dates) | Phase 0 sign-off | Arpit / Ops |
| Confirm JWT 12h TTL = "12h working session" acceptable (not true inactivity) | Phase 0 sign-off | Adesh / Ops |
| Label printer model + exact label dimensions (A6 assumed) | Phase 2 | Ops |
| Style-code scanning on outward side? (Q-3 from PRD) | Phase 3 | Adesh / Ops |