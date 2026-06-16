# Hexalog Warehouse Tool V1

**Version:** 1.0  
**Status:** Pre-implementation — schema approved, V1 build in progress  
**Tech Lead:** Arpit  
**Deadline:** 30 June 2026

---

## Overview

The Hexalog Warehouse Tool is a multi-tenant web application used inside the warehouse on desktops and laptops with USB barcode scanners. It replaces a previous ad-hoc system with two integrated modules on a shared platform:

- **Inward (Inscan):** Operators receive incoming cartons, scan items, verify counts, and submit. Each submission generates a unique Inscan Number for tracking.
- **Outward (Packing):** Admins upload customer purchase orders. Packers generate box labels, scan items into boxes against open POs, and mark boxes full. Every scan is validated and FIFO-allocated to the oldest outstanding PO.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 + Vite + TypeScript |
| Backend | FastAPI (async) |
| Database | PostgreSQL 16 |
| ORM | Async SQLAlchemy + asyncpg |
| Migrations | Alembic |
| Auth | JWT + `tokens_invalidated_at` (force-logout without a session store) |
| Application Logging | structlog (structured JSON) |
| PDF Generation | WeasyPrint (box labels — A6 / 4×6 inch thermal stock) |

> Redis is explicitly out of scope for V1. No session store, no cache, no queue.

---

## Documentation

| Document | Contents |
|----------|----------|
| [Feature List](docs/feature-list.md) | All 40 V1 features with PRD IDs; FIFO algorithm; verbatim error messages from PRD §11; out-of-scope items |
| [Architecture Overview](docs/architecture-overview.md) | System layers, module breakdown, authentication flow, FIFO concurrency design, inward transaction design, key architectural decisions |
| [Database Schema V1](docs/database-schema-v1.md) | 16-table schema (post-review, all 10 bugs fixed); 31 indexes; 11 enums; counter UPSERT strategy; inventory ledger strategy; ER diagram |

---

## Roles

| Role | Access |
|------|--------|
| `admin` | Full access — manage users, customers, POs; view all reports; force-close boxes |
| `inward_operator` | Inward scanning — receive, scan, verify, and submit boxes |
| `packer` | Outward packing — generate labels, scan items, mark boxes full |

One user can hold multiple roles. All role checks are enforced server-side on every API endpoint.

---

## Repository Structure

```
hexalog_packagingtool/
├── README.md
└── docs/
    ├── feature-list.md
    ├── architecture-overview.md
    └── database-schema-v1.md
```

---

## Open Items Before Build

These must be resolved with Ops before Phase 0 begins:

| Item | Needed Before | Owner |
|------|--------------|-------|
| Confirm `APP_TIMEZONE=Asia/Kolkata` (affects Inscan Number dates) | Phase 0 sign-off | Arpit / Ops |
| Confirm JWT 12h TTL = "12h working session" (not inactivity timeout) | Phase 0 sign-off | Adesh / Ops |
| Label printer model + exact label dimensions (A6 assumed) | Phase 2 | Ops |
| Style-code scanning on outward side (Q-3) | Phase 3 | Adesh / Ops |

---

## Source

PRD: *Warehouse Tool v1.0* — Adesh Agarwal, 12 June 2026
