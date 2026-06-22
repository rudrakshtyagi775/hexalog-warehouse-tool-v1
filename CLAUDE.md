# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Status

**Greenfield — no source code exists yet.** Only three approved design documents are committed. All implementation is ahead. The current branch is `auth-module` and the first milestone is the Auth module.

PRD: *Warehouse Tool v1.0* — Adesh Agarwal, 12 June 2026  
Tech Lead: Arpit  
Deadline: 30 June 2026

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async) |
| Database | PostgreSQL 16 |
| ORM | Async SQLAlchemy 2.0 + asyncpg |
| Migrations | Alembic (sync psycopg2 URL only — never asyncpg for Alembic) |
| Auth | 15-min JWT (PyJWT) + rotating refresh token in httpOnly cookie |
| Password | passlib[bcrypt], BCRYPT_ROUNDS=12 |
| Logging | structlog (structured JSON in prod, colored console in dev) |
| PDF | WeasyPrint (box labels, Phase 2) |
| Frontend | React 18 + Vite + TypeScript (separate milestone) |

**Redis is explicitly out of scope for V1.** No session store, no cache, no queue.

---

## Commands

These commands apply once `pyproject.toml` exists and the environment is set up:

```bash
# Install all dependencies (including dev)
pip install -e ".[dev]"

# Run all tests
pytest -v

# Run a single test file
pytest tests/integration/test_login.py -v

# Run a single test by name
pytest tests/integration/test_login.py::test_wrong_password -v

# Run tests with coverage
pytest --cov=app/services --cov-report=term-missing

# Start the dev server
uvicorn app.main:app --reload

# Run Alembic migrations
alembic upgrade head

# Generate a new migration (always review before applying)
alembic revision --autogenerate -m "describe_the_change"

# Alembic uses DATABASE_URL_SYNC (psycopg2), not DATABASE_URL (asyncpg)
```

Tests require a separate `TEST_DATABASE_URL` environment variable pointing to a test database.

---

## Planned Application Structure

```
app/
├── main.py              # FastAPI factory, lifespan, router mounts, exception handlers
├── config.py            # pydantic-settings Settings singleton — imported everywhere
├── database.py          # async engine, AsyncSessionLocal, get_db dependency
├── logging_config.py    # structlog setup — called once in main.py lifespan
├── models/              # SQLAlchemy ORM models only — no business logic
│   ├── base.py          # DeclarativeBase, TimestampMixin
│   ├── enums.py         # all 11 PostgreSQL enum mirrors (str, enum.Enum)
│   ├── user.py          # User, UserOrganisation, UserRole, Session, LoginAttempt
│   └── ...
├── schemas/             # Pydantic v2 I/O contracts — never import ORM models directly
├── services/            # All business logic and DB queries — owns commit/rollback
├── dependencies/        # FastAPI Depends() callables — call services, never query DB directly
├── routers/             # APIRouter per module — HTTP wiring only, no logic
└── utils/
    └── request.py       # get_client_ip(request) — centralised proxy-aware IP extraction
```

**Layer rule:** Routers call dependencies. Dependencies call services. Services own DB queries and transactions. Routers never commit. Services always call `write_audit_log` in the same transaction as the triggering event.

---

## Auth System (Approved Design — June 18 2026)

The auth redesign document (`C:\Users\rudra\OneDrive\Desktop\HEXALOG POLICIES\BRD AND PRDs\2026-06-18-auth-redesign-design.md`) supersedes the architecture overview's auth section. Implement this design, not the original `tokens_invalidated_at` approach.

**`users.tokens_invalidated_at` does not exist in the schema. Do not add it.**

### How it works

- **Access token:** JWT, 15-min TTL, `Authorization: Bearer` header. Zero DB hit per request — pure cryptographic verification.
- **Refresh token:** `secrets.token_hex(32)`, delivered as `httpOnly; Secure; SameSite=Strict; Path=/api/auth` cookie. Only the `HMAC-SHA256(REFRESH_TOKEN_HASH_KEY, raw_token)` is stored in the `sessions` table — the raw token is never persisted.
- **Rotation:** Every `/api/auth/refresh` call replaces `refresh_token_hash` with a new hash and moves the old hash to `previous_refresh_token_hash` with a short grace window (`REFRESH_REUSE_GRACE_SECONDS=30`). Reuse of an old rotated token after the grace window triggers full session revocation (`revoke_reason='theft_detected'`).

### JWT claims shape

```json
{ "sub": "42", "org": "7", "roles": ["packer"], "session_id": "<uuid>", "iat": 0, "exp": 0 }
```

Keys are `sub`, `org`, `session_id` — **not** `user_id` / `organisation_id`. Always decode with `algorithms=["HS256"]` explicitly. Reject `alg:none` unconditionally.

### Auth endpoints

| Method | Path | Auth required | Notes |
|--------|------|--------------|-------|
| POST | `/api/auth/login` | None | Returns access token in body; refresh token in cookie |
| POST | `/api/auth/refresh` | Cookie | `SELECT ... FOR UPDATE` on session row |
| POST | `/api/auth/logout` | Cookie | Revokes session; clears cookie |
| POST | `/api/auth/logout-all` | Bearer + Cookie | Revokes all other sessions for user |
| POST | `/api/auth/switch-organisation` | Bearer | New session for new org |
| GET | `/api/auth/me` | Bearer | No extra DB query — served from CurrentUser |
| DELETE | `/api/admin/sessions/:id` | Bearer (admin) | Force-revoke a session |
| DELETE | `/api/admin/users/:id/sessions` | Bearer (admin) | Revoke all sessions for a user |

### Session invalidation events (all set `sessions.revoked_at` in same transaction)

- User logs out
- Admin force-revokes
- Token theft detected (old rotated token replayed after grace window)
- User deactivated (`is_active=False`) — must also revoke all active sessions in same transaction
- Password changed — revokes all sessions except the one making the change

### Brute force protection

- **Per-IP:** `login_attempts` table, sliding window, max `LOGIN_RATE_LIMIT_PER_MINUTE=5`. → 429.
- **Per-account:** `users.failed_attempts` + `users.locked_until`. Lock after `LOGIN_MAX_FAILURES=10` consecutive failures for `LOGIN_LOCKOUT_MINUTES=15`. Lockout check runs before password verification.
- All failures return `401 "Invalid credentials"` — no message distinguishes locked/wrong-password/not-found.

---

## Critical Invariants (never violate these)

### Transactions
- Audit log writes happen **in the same DB transaction** as the event that caused them. No event without an audit row is possible by construction.
- Inward box submission is atomic: Inscan Number generation + box status + ledger entries all succeed or all roll back.
- Outward scan acceptance is atomic: `UPDATE outward_po_lines SET packed_qty = packed_qty + 1 WHERE id = ? AND packed_qty < ordered_qty`. If 0 rows affected (concurrent race), retry from the top (max 5 retries). Over-packing is impossible by construction.

### Append-only tables
`inward_scans`, `outward_scans`, `inventory_ledger_entries`, `audit_logs` — rows are inserted, never updated or deleted. Corrections happen via new rows (soft-delete flag, reversal entry).

### Soft deletes
Scan deletions set `is_deleted = true` and write a reversal `+1` ledger entry. The row is never hard-deleted.

### Counter generation
Box IDs (`B-<CUSTCODE>-<6-digit>`) and Inscan Numbers (`INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>`) use a single atomic UPSERT on the `counters` table:
```sql
INSERT INTO counters (..., last_value) VALUES (..., 1)
ON CONFLICT (...) DO UPDATE SET last_value = counters.last_value + 1
RETURNING last_value
```
Never use `SELECT FOR UPDATE` for counter generation.

### scanned_qty invariant
`inward_boxes.scanned_qty` must equal `COUNT(*) FROM inward_scans WHERE inward_box_id = ? AND is_deleted = false`. The application service layer maintains this; no DB trigger enforces it. Always increment/decrement `scanned_qty` in the same transaction as the scan INSERT or soft-delete.

### Multi-tenancy
Every query on an org-scoped table must include `WHERE organisation_id = :org_id`. The `organisation_id` comes from the JWT (`org` claim), never from the request body.

### password_hash
Never appears in any API response, any `after_data` or `before_data` in `audit_logs`. `write_audit_log` strips it unconditionally. Never log it.

---

## SQLAlchemy 2.0 Patterns

- Use `Mapped[T]` with `mapped_column()` throughout — not the legacy `Column()` style.
- Set `expire_on_commit=False` on `async_sessionmaker` — accessing attributes after `await session.commit()` raises `MissingGreenlet` otherwise.
- All enums: `class XEnum(str, enum.Enum)` — compatible with Pydantic v2 and `Enum(native_enum=True)`.
- `selectinload` does not accept `.where()` in SQLAlchemy 2.0. Load a relationship first, then run a separate explicit `select()` filtered by org when you need org-scoped role loading.

---

## PRD-Mandated Verbatim Error Messages

These strings are fixed. UI and API must return them exactly — ops training material references them.

| Condition | Message |
|-----------|---------|
| Duplicate PO/invoice already inwarded | `An inward already exists for this PO/Invoice. Continue adding boxes to it?` |
| Duplicate box number within PO/invoice | `Box number already used for this PO/Invoice. Enter a different box number.` |
| Manual count ≠ scanned qty | `Scanned Quantity and Physical Quantity do not match. Please verify before submission.` |
| EAN on no open PO | `EAN not found in open POs` |
| EAN exists but all PO lines full | `Quantity complete for all open POs` |
| Packer scans second box with one active | `Mark the current box full before starting another box.` |
| Scanned/entered box is Closed | `This box is closed. Showing details in read-only mode.` |
| Manual Box ID doesn't exist | `Box ID not found` |
| Accepted scan, no inward stock recorded | `Note: no recorded inward stock for this item.` |
| Missing required column in PO upload | `Upload failed: missing required column(s): <names>` |

---

## RBAC

Three roles: `admin`, `inward_operator`, `packer`. One user can hold multiple roles per org.

```python
require_admin              # admin only
require_inward_operator    # inward_operator OR admin
require_packer             # packer OR admin
```

All role checks are server-side in the service/dependency layer. Admins implicitly pass every role check.

---

## Key Design Decisions (do not re-litigate)

| Decision | Choice |
|----------|--------|
| One tool or two? | One tool, two modules on a shared platform |
| Ledger blocks packing? | No — track, don't block (V1). Negative balances are expected. |
| FIFO vs manual PO selection | FIFO always. Oldest `uploaded_at`, tie-break by lower PO id. |
| Over-packing prevention | `packed_qty < ordered_qty` CHECK constraint + atomic conditional UPDATE — two layers |
| Refresh tokens | Yes — rotating refresh token in httpOnly cookie (June 18 redesign). Original `tokens_invalidated_at` design is superseded. |
| Redis | Explicitly rejected for V1. No session store, no cache, no queue. |
| Concurrent scan allocation | Retry loop (max 5), not pessimistic lock |
| Inactivity timeout | `SESSION_INACTIVITY_MINUTES=480` (8 hours) — confirmed by Arpit 2026-06-19. |

---

## Open Items Before Writing Code

| Item | Blocks |
|------|--------|
| Confirm `APP_TIMEZONE=Asia/Kolkata` for Inscan Number date generation | Phase 1 |
| Label printer model + exact label dimensions (A6 assumed) | Phase 2 |

---

## Branch and PR Strategy

- `main` — stable, PRD-aligned
- `auth-module` — current working branch; PR raised after Auth milestone is complete
- Schema review changes are stashed and belong on `schema-review-pr`, not `auth-module`

Milestone order per PRD §14:  
**Phase 0:** Auth → **Phase 1:** Inward module → **Phase 2:** Outward (non-scan) → **Phase 3:** Pack Items + FIFO → **Phase 4:** Reports → **Phase 5:** Hardening
