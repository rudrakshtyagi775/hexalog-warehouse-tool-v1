# Feature List — Hexalog Warehouse Tool V1

**Source:** PRD v1.0 — Adesh Agarwal, 12 June 2026  
**Status:** Finalized. Do not add or remove features without a PRD amendment.  
**Requirement ID prefixes:** `SH` = Shared Platform · `IN` = Inward · `OUT` = Outward · `LG` = Ledger · `NFR` = Non-functional

---

## V1 Scope

### In Scope

| Area | Features |
|------|---------|
| Inward | Scanning, verification, submission, Inscan Numbers |
| Outward | PO upload, box labels, packing with validation and FIFO allocation |
| Ledger | Inventory tracking (track, don't block), variance reporting |
| Reports | 4 reports with CSV/XLSX download |
| Platform | Roles, permissions, full audit trail |

### Out of Scope (V1)

| Feature | Reason |
|---------|--------|
| Putaway / bin locations | Not in PRD |
| Blocking packing based on stock levels | Ledger is report-only in V1 (Decision D-3) |
| ERP / Tally / OMS integration | Future phase |
| Mobile app | Browser on desktop/tablet only |
| Returns (RTO) processing | Future phase |
| Cycle counts / stock adjustment UI | Future phase |
| Background export job queue | Phase 5 hardening |

---

## Shared Platform Features

| ID | Feature | Role(s) | Notes |
|----|---------|---------|-------|
| SH-1 | User login with email and password | All | Sessions expire after 12 hours (JWT TTL) |
| SH-2 | Role-based access enforced server-side on every API endpoint | Admin | Hiding buttons alone is insufficient |
| SH-3 | Organisation auto-selected on login; switchable via dropdown; all data partitioned by organisation | All | One user may belong to multiple orgs |
| SH-4 | Shared Customer master: name, unique 2–3 letter code (e.g. KIR), status (Active/Inactive) | Admin | Inactive customers excluded from dropdowns |
| — | User management: create, deactivate, assign/revoke roles | Admin | |
| — | Force-logout via `tokens_invalidated_at` (password change, admin deactivation) | Admin | |

---

## Inward Module Features

### Workflow (6 sequential steps per box)

| Step | ID | Feature | Notes |
|------|----|---------|-------|
| 1 | IN-1 | Searchable dropdown of Active customers — mandatory before anything else is enabled | |
| 2 | IN-2 | PO Number and/or Invoice Number — at least one required | |
| 2 | IN-3 | Duplicate inward check — if same customer + PO/invoice exists, confirm dialog to append or cancel | Never blocks a genuine multi-day delivery |
| 3 | IN-4 | Box number entry — mandatory, must be unique within the InwardReference; duplicate shows blocking alert | |
| 4 | IN-5 | Item scan (EAN or Style Code) — append-only, timestamped, real-time running quantity updated in < 300 ms | |
| 4 | IN-8 | Soft-delete of an individual scan while box is in Scanning state | Deleted scan retained for audit |
| 5 | IN-6 | Complete Box — locks scanning, requires Manual Physical Count; submission blocked until Manual Count = Scanned Qty | No override for mismatch in V1 |
| 5 | IN-7 | Reopen Box — returns box to Scanning state from Pending Verification; action audit-logged | |
| 6 | IN-9 | Submit box — atomic: marks box Completed, generates Inscan Number (`INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>`), writes ledger entries | All in one transaction |
| — | IN-10 | Inward History tab — completed boxes read-only, with drill-down to individual scans | |
| — | IN-11 | Finish Delivery — marks InwardReference Completed; no new boxes accepted after this | |

### Inward Edge Cases

| Scenario | Behaviour |
|----------|-----------|
| Operator abandons box mid-scan | Box stays in Scanning state; resumable by same operator |
| Unreadable barcode | Operator may type code manually; flagged `is_manual_entry = true` in audit |
| Wrong customer selected | Cannot change customer once box has scans; operator must delete scans or abandon and restart |
| Admin sees abandoned box | Admin can soft-delete abandoned boxes |

---

## Outward Module Features

### Admin — PO Management

| ID | Feature | Notes |
|----|---------|-------|
| OUT-1 | Upload Outward PO as CSV/XLSX | Required columns: `po_number`, `ean`, `quantity`. Extra columns ignored. |
| OUT-2 | Preview before confirm | Shows detected columns, first 10 rows, totals, problems, consolidation notice. Upload with structural errors cannot be confirmed. |
| OUT-3 | Duplicate EAN consolidation | Same EAN on multiple rows in one PO: quantities summed into one line |
| OUT-4 | Toggle PO status Open ↔ Closed | Closing stops new allocations instantly; packed history unchanged |

### Packer — Open POs Tab

| ID | Feature | Notes |
|----|---------|-------|
| OUT-5 | Read-only list of Open POs | PO number, customer, upload date, progress (packed/ordered), progress bar. Search by PO number; filter by date and customer. |

### Packer — Create Box Labels Tab

| ID | Feature | Notes |
|----|---------|-------|
| OUT-6 | Bulk-generate N box labels for a selected customer | IDs sequential, unique, never reused |
| OUT-7 | Labels as downloadable PDF | Large human-readable Box ID + QR or Code128 barcode; one label per page; targets A6 / 4×6 inch thermal stock (A-5) |
| OUT-8 | Label history: Box ID, customer, status, created date, print count | Reprint allowed at any time; every print increments audited `print_count` |

### Packer — Pack Items Tab

| ID | Feature | Notes |
|----|---------|-------|
| OUT-9 | Start packing by scanning a box label | Manual Box ID entry as fallback for damaged labels — validates existence and status |
| OUT-10 | One active box per packer | While a box is In Use, scanning a different box is blocked: *"Mark the current box full before starting another box."* |
| OUT-11 | Scanning a Closed box shows its details read-only | Cannot add items or reopen |
| OUT-12 | EAN scan validation + FIFO allocation | See FIFO algorithm below. Instant Accepted/Rejected feedback with PO number and remaining qty (Accepted) or exact reason (Rejected) |
| OUT-13 | Soft-delete an Accepted scan | Only from current active Open/In Use box before Mark Box Full; decrements PO line `packed_qty` and writes +1 ledger reversal |
| OUT-14 | Rejected and Deleted scans cannot be deleted again | Delete button never shown for them |
| OUT-15 | Mark Box Full | Closes the box permanently; packer is freed to scan the next box |
| OUT-16 | Packing History | Own boxes (ID, status, timestamps, item count) with drill-down to scans for the last 30 days |

### FIFO Allocation Algorithm (OUT-12)

Executed inside one atomic database transaction on every EAN scan:

1. Find all Open PO lines for this organisation where `ean = scanned_ean` and `packed_qty < ordered_qty`
2. If none exist → **Reject:** *"EAN not found in open POs"*
3. If lines exist but all fully packed → **Reject:** *"Quantity complete for all open POs"*
4. Pick the line whose parent PO has the oldest `uploaded_at` (FIFO). Tie-breaker: lower PO id
5. `UPDATE outward_po_lines SET packed_qty = packed_qty + 1 WHERE id = ? AND packed_qty < ordered_qty` — if 0 rows affected (concurrent race), retry from step 1 (max 5 retries)
6. Record `OutwardScan` as Accepted; write −1 ledger entry; if ledger balance ≤ 0, set `stock_flagged = true`
7. Return feedback to UI in < 300 ms

### Outward Edge Cases

| Scenario | Behaviour |
|----------|-----------|
| Two packers scan last remaining unit simultaneously | Exactly one Accepted, one Rejected — over-packing is impossible by construction |
| PO closed mid-pack | Subsequent scans flow to next FIFO PO or are rejected; packed history unchanged |
| Damaged label | Manual entry rejects IDs that don't exist or are Closed |
| Packer logs out with active box | Box stays In Use; resumed on next login by same packer |
| Admin force-close stuck box | Admin only; audit-logged |

---

## Inventory Ledger

| ID | Feature | Notes |
|----|---------|-------|
| LG-1 | Ledger entries written in the same transaction as the event that causes them | A submitted box with no ledger entries is impossible |
| LG-2 | Stock-flagged notice | If outward scan accepted while ledger balance ≤ 0 → scan still accepted, `stock_flagged = true`, UI shows *"Note: no recorded inward stock for this item."* Packing never blocked. |
| LG-3 | Inventory Variance Report exposes balance per customer + EAN | See Reports |
| LG-4 | Balances may go negative in V1 | Expected during adoption. Variance report is the visibility tool. |

**Ledger events:**

| Event | Delta |
|-------|-------|
| Inward box submitted | +1 per item scanned in the box |
| Outward scan Accepted | −1 |
| Accepted outward scan deleted | +1 (reversal) |

---

## Reports (Admin Only)

All reports: mandatory date range filter, optional customer filter, CSV and XLSX download.

| # | Report | Row Grain | Key Columns |
|---|--------|-----------|-------------|
| R-1 | Customer-wise Inscan Report | Item code per inward box | Inscan Number, Customer, PO Number, Invoice Number, Box Number, EAN/Style Code, Scanned Qty, Physical Qty, Variance, Date, User |
| R-2 | Outward PO Summary | PO line | PO Number, Customer, EAN, Ordered Qty, Packed Qty, Remaining Qty, PO Status, Upload Date |
| R-3 | Item Packing (EAN Scans) | Per outward scan event (including rejected and deleted) | Timestamp, User, Box ID, EAN, Result, Allocated PO Number, Reject Reason, Stock Flagged |
| R-4 | Inventory Variance | Customer + EAN | Customer, EAN, Total Inward Qty, Total Outward Qty, Current Balance, Stock-Flagged Scan Count, Last Movement Date |

---

## Audit Trail (All Modules)

Every action writes an append-only record: user, timestamp, transaction reference, action, before→after values.

| Module | Audited Actions |
|--------|----------------|
| Shared | Login, org switch, role changes, customer master changes |
| Inward | Customer selection, PO/invoice creation, box creation, every item scan, scan deletion, complete box, reopen box, verification entry, submission (with Inscan Number) |
| Outward | PO upload confirm, PO open/close, label batch creation, every print/reprint, box scan/manual entry, every EAN scan (accepted/rejected), scan deletion, mark box full, admin force-close |
| Reports | Every report download (who, which report, filter range) |

---

## Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | Scan validation (inward and outward) responds in < 300 ms under normal load |
| NFR-2 | All scan events, ledger entries, and audit logs are append-only; corrections happen via new records (soft delete / reversal) — never edits |
| NFR-3 | RBAC enforced server-side on every API endpoint, partitioned by organisation + role |
| NFR-4 | Allocation and counter generation are concurrency-safe; over-packing and duplicate IDs must be impossible, not merely unlikely |
| NFR-5 | Report exports for large date ranges complete reliably (streaming or paginated response) |
| NFR-6 | Scan input fields maintain keyboard focus aggressively; after every scan, focus returns to scan field automatically |