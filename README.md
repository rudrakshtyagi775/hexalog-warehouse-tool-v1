# Hexalog Warehouse Tool — V1

Internal warehouse management tool for Hexalog operations. Manages everything coming into the warehouse (Inward / Inscan) and everything going out of it (Outward / Packing) on a single shared platform.

> **V1 Deadline: 30 June 2026**  
> **Schema Status: Ready for Arpit Review**  
> **PRD Version: 1.0 — Adesh Agarwal, 12 June 2026**

---

## What It Does

| Module | Who Uses It | What It Does |
|--------|-------------|--------------|
| **Inward (Inscan)** | Inward Operators | Scan incoming cartons, verify item counts, submit with a unique Inscan Number |
| **Outward (Packing)** | Packers | Generate box labels, scan items against open POs, FIFO-allocate to the correct PO, mark boxes full |
| **Shared Platform** | Admins | User management, customer master, PO upload, reports, audit trail, inventory ledger |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 + Vite + TypeScript |
| Backend | FastAPI (async) |
| Database | PostgreSQL 16 |
| ORM | Async SQLAlchemy + asyncpg |
| Migrations | Alembic |
| Authentication | JWT — `tokens_invalidated_at` invalidation strategy |
| Logging | structlog (application) + `audit_logs` table (business audit) |
| PDF Generation | WeasyPrint |

> **Redis is explicitly out of scope for V1.**

---

## Roles

| Role | Access |
|------|--------|
| Admin | PO upload and management, reports, user management, customer master |
| Inward Operator | Inward scanning workflow end-to-end |
| Packer | Box label creation, Pack Items workflow, packing history |

One user can hold multiple roles. Role assignment is per organisation.

---

## Project Structure
.
├── alembic/ # Alembic migration environment
│ ├── env.py # Must use ALEMBIC_DATABASE_URL (psycopg2, not asyncpg)
│ └── versions/ # Migration scripts
├── app/
│ ├── api/ # FastAPI routers (per module)
│ ├── models/ # SQLAlchemy ORM models
│ ├── schemas/ # Pydantic request/response schemas
│ ├── services/ # Business logic layer
│ ├── repositories/ # Data access layer
│ └── core/ # Config, auth, logging, dependencies
├── frontend/ # React + Vite + TypeScript
│ └── src/
├── docs/ # Engineering documentation
│ ├── feature-list.md
│ ├── architecture-overview.md
│ └── database-schema-v1.md
├── alembic.ini
└── README.md

---
## Environment Variables
Two database URLs are required — one for the async application runtime and one for synchronous Alembic migrations.
| Variable | Driver | Used By |
|----------|--------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://` | FastAPI application |
| `ALEMBIC_DATABASE_URL` | `postgresql+psycopg2://` | Alembic migrations only |
| `SECRET_KEY` | — | JWT signing |
| `APP_TIMEZONE` | e.g. `Asia/Kolkata` | Inscan Number date generation |
| `ACCESS_TOKEN_EXPIRE_HOURS` | e.g. `12` | JWT TTL |
---
## Build Order
Phases are independently testable. Complete each phase before starting the next.
| Phase | Deliverable | Why This Order |
|-------|-------------|----------------|
| 0 | Schema · Alembic · Auth · RBAC · Organisations · Customer Master · Audit-log wiring | Foundation everything else depends on |
| 1 | Inward module end-to-end (steps 1–6, Inscan Numbers, Inward History) | Smaller, self-contained; warehouse can start using it while Phase 2–3 is built |
| 2 | Outward: PO upload + preview + manage · Open POs tab · Label generation + PDF | Non-scanning outward features; Admins can start loading real POs |
| 3 | Pack Items: scan validation · FIFO allocation · concurrency · delete-scan · mark-full | Hardest part; build on a stable base and unit-test the allocation path |
| 4 | All four reports + stock-flag UI notice | Reports need real data from Phases 1–3 |
| 5 | Admin tooling · background exports · 300 ms performance pass | Hardening after core is proven |
---
## Documentation
| Document | Purpose |
|----------|---------|
| [Feature List](docs/feature-list.md) | Complete V1 feature inventory with PRD requirement IDs |
| [Architecture Overview](docs/architecture-overview.md) | System design, tech decisions, data flow |
| [Database Schema V1](docs/database-schema-v1.md) | Authoritative schema reference — all 16 tables, indexes, constraints |
---
## Team
| Role | Person |
|------|--------|
| Product | Adesh Agarwal |
| Tech Lead | Arpit |
| Engineering | TBD |