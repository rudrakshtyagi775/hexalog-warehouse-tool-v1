# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Status

**Auth module (implementation complete — ready for PR review).** All 8 auth milestone items are implemented. Current branch: `auth-module`.

**Completed (Items 1–8):** ORM models, initial migration (`9014f72e9f0c`), `auth_service` (login/refresh/logout/logout-all/switch-organisation/admin revoke), `jwt_service`, `password_service`, `audit_service`, `dependencies/auth.py`, all auth routers (`routers/auth.py`, `routers/admin.py`), unit tests (23 passing), integration tests (57 passing across 8 files).

**Deferred (not blockers):** `get_client_ip()` needs `X-Forwarded-For` support before production deploy behind reverse proxy. Session table has no cleanup job (expired rows accumulate). Confirm with Arpit whether `logout_all_devices` intentionally revokes across all orgs.

PRD: *Warehouse Tool v1.0* — Adesh Agarwal, 12 June 2026  
Tech Lead: Arpit  
Deadline: 30 June 2026

> **README.md is stale** on auth — it still says `tokens_invalidated_at`. The June 18 redesign (below) is authoritative. Do not introduce `tokens_invalidated_at`.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async) |
| Database | PostgreSQL 16 |
| ORM | Async SQLAlchemy 2.0 + asyncpg |
| Migrations | Alembic (sync psycopg2 URL only — never asyncpg for Alembic) |
| Auth | 15-min JWT (PyJWT) + rotating refresh token in httpOnly cookie |
| Password | `bcrypt>=4.0` (direct — no passlib) |
| Logging | structlog (structured JSON in prod, colored console in dev) |
| Linting | Ruff (`line-length=100`, `target-version=py312`) |
| PDF | WeasyPrint (box labels, Phase 2) |
| Frontend | React 18 + Vite + TypeScript (separate milestone) |

**Redis is explicitly out of scope for V1.** No session store, no cache, no queue.

---

## Commands

```bash
# Install all dependencies (including dev)
pip install -e ".[dev]"

# Run all unit tests (no database required)
pytest tests/unit/ -v

# Run all integration tests (requires TEST_DATABASE_URL)
pytest tests/integration/ -v

# Run a single test file
pytest tests/integration/test_login.py -v

# Run a single test by name
pytest tests/integration/test_login.py::test_login_wrong_password -v

# Run tests with coverage
pytest --cov=app/services --cov-report=term-missing

# Lint
ruff check .

# Start the dev server
uvicorn app.main:app --reload

# Run Alembic migrations
alembic upgrade head

# Generate a new migration (always review before applying)
alembic revision --autogenerate -m "describe_the_change"
```

Integration tests require `TEST_DATABASE_URL` pointing to a separate PostgreSQL test database. Unit tests (`tests/unit/`) run without any database.

Alembic uses `DATABASE_URL_SYNC` (psycopg2), not `DATABASE_URL` (asyncpg).

---

## Application Structure

```
app/
├── main.py              # FastAPI factory (create_app()), lifespan, CORSMiddleware, exception handler
├── config.py            # pydantic-settings Settings singleton — Settings() cached via @lru_cache
├── database.py          # async engine, AsyncSessionLocal, get_db dependency
├── logging_config.py    # structlog setup — called once in main.py lifespan
├── models/              # SQLAlchemy ORM models only — no business logic
│   ├── base.py          # DeclarativeBase, TimestampMixin (created_at, updated_at)
│   ├── enums.py         # all 11 PostgreSQL enum mirrors (str, enum.Enum)
│   ├── user.py          # User, UserOrganisation, UserRole, Session, LoginAttempt
│   └── ...
├── schemas/             # Pydantic v2 I/O contracts — never import ORM models directly
│   └── auth.py          # LoginRequest, LoginResponse, MeResponse, CurrentUser (dataclass)
├── services/            # All business logic and DB queries — owns commit/rollback
│   ├── auth_service.py  # login(), refresh_session(), logout(), logout_all_devices()
│   ├── jwt_service.py   # issue_access_token(), decode_access_token()
│   ├── password_service.py  # hash_password(), verify_password(), DUMMY_HASH
│   └── audit_service.py # write_audit_log() — strips password_hash, never commits
├── dependencies/        # FastAPI Depends() callables
│   └── auth.py          # get_current_user, require_roles, require_admin, require_inward_operator, require_packer
├── routers/             # APIRouter per module — HTTP wiring only, no logic
│   └── auth.py          # /api/auth/* endpoints
└── utils/
    └── request.py       # get_client_ip(request) — centralised proxy-aware IP extraction
```

**Layer rule:** Routers call dependencies. Dependencies call services. Services own DB queries and transactions. Routers never commit. Services always call `write_audit_log` in the same transaction as the triggering event.

---

## Auth System (Approved Design — June 18 2026)

The auth redesign document (`C:\Users\rudra\OneDrive\Desktop\HEXALOG POLICIES\BRD AND PRDs\2026-06-18-auth-redesign-design.md`) supersedes the architecture overview's auth section.

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

### `get_current_user` — what it actually does

One DB SELECT per authenticated request (the `User` row only, for `is_active` and `/me` data). Roles are read from JWT claims — **no second DB query for roles**. Role changes propagate after the next access token refresh (~15 min).

```python
# 1. Require Bearer header (raise 401 if missing)
# 2. decode_access_token() → validate sig + exp
# 3. Extract user_id (int(payload["sub"])), organisation_id (int(payload["org"])),
#    session_id (payload["session_id"]), roles ([UserRoleEnum(r) for r in payload["roles"]])
# 4. SELECT User WHERE id = user_id
# 5. Raise 401 if user is None or not user.is_active
# 6. Return CurrentUser(user_id, organisation_id, session_id, roles, full_name, email, is_active)
```

`CurrentUser` is a dataclass (not a Pydantic model). It carries `session_id` — needed by `logout-all` to exclude the current session when revoking others.

### Auth endpoints

| Method | Path | Auth required | Status |
|--------|------|--------------|--------|
| POST | `/api/auth/login` | None | ✅ implemented |
| POST | `/api/auth/refresh` | Cookie | ✅ implemented |
| POST | `/api/auth/logout` | Cookie | ✅ implemented |
| POST | `/api/auth/logout-all` | Bearer + Cookie | ✅ implemented |
| GET | `/api/auth/me` | Bearer | ✅ implemented |
| POST | `/api/auth/switch-organisation` | Bearer | ✅ implemented |
| DELETE | `/api/admin/sessions/:id` | Bearer (admin) | ✅ implemented |
| DELETE | `/api/admin/users/:id/sessions` | Bearer (admin) | ✅ implemented |

### Session invalidation events (all set `sessions.revoked_at` in same transaction)

- User logs out
- Admin force-revokes
- Token theft detected (old rotated token replayed after grace window)
- User deactivated (`is_active=False`) — must also revoke all active sessions in same transaction
- Password changed — revokes all sessions except the one making the change
- **Inactivity timeout** — revokes session but **currently missing the audit log call** (deferred gap)

### Brute force protection

- **Per-IP:** `login_attempts` table, sliding window, max `LOGIN_RATE_LIMIT_PER_MINUTE=5`. → 429.
- **Per-account:** `users.failed_attempts` + `users.locked_until`. Lock after `LOGIN_MAX_FAILURES=10` consecutive failures for `LOGIN_LOCKOUT_MINUTES=15`. Lockout check runs before password verification.
- Both rate-limit and failed-attempt writes use `_side_effect_session_factory` — a separate `AsyncSession` that commits independently from the main transaction, so they persist even when the main transaction rolls back.
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
`inward_boxes.scanned_qty` must equal `COUNT(*) FROM inward_scans WHERE inward_box_id = ? AND is_deleted = false`. Always increment/decrement `scanned_qty` in the same transaction as the scan INSERT or soft-delete.

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
- **Multi-FK disambiguation:** When a child table has two FKs pointing back to the same parent table (e.g. `Session` has both `user_id` and `revoked_by`, both referencing `users.id`), the parent-side `relationship()` must declare `foreign_keys` explicitly — use string form since the child class may not yet be defined: `foreign_keys="[Session.user_id]"`. Omitting this raises `InvalidRequestError: Could not determine join condition` at mapper configuration time.

---

## Testing Infrastructure

### Test separation

| Directory | DB required | When to run |
|-----------|-------------|-------------|
| `tests/unit/` | No | Always — fast, run before every commit |
| `tests/integration/` | Yes (`TEST_DATABASE_URL`) | When PostgreSQL is available |

### pytest configuration (`pyproject.toml`)
- `asyncio_mode = "auto"` — all `async def test_*` functions are auto-collected as coroutines. **Do not add `@pytest.mark.asyncio`** (redundant, but harmless).
- `asyncio_default_fixture_loop_scope = "session"` — session-scoped async fixtures share one event loop.

### Integration test fixtures (`tests/conftest.py`)
All fixtures are function-scoped unless noted. The `db` session rolls back after each test.

| Fixture | Type | Notes |
|---------|------|-------|
| `async_engine` | session | Creates all tables once; drops all on teardown |
| `db` | function | `AsyncSession`; rolls back after each test |
| `client` | function | httpx `AsyncClient` via ASGI transport; overrides `get_db` with test `db` |
| `org` | function | `Organisation(name="Test Org", is_active=True)` |
| `admin_user` | function | email `admin@test.com`, password `AdminPass1!`, role `admin` in `org` |
| `packer_user` | function | email `packer@test.com`, password `PackerPass1!`, role `packer` in `org` |
| `inactive_user` | function | email `inactive@test.com`, password `InactivePass1!`, `is_active=False` |

### `_side_effect_session_factory` pattern
`_check_ip_rate_limit` and `_record_failed_attempt` in `auth_service.py` open their own sessions that commit independently. The integration conftest (`tests/integration/conftest.py`) has a session-scoped `autouse=True` fixture that patches `auth_service._side_effect_session_factory` to point at the test engine, so these writes land in the test database rather than the main one.

In tests, the `admin_user` row is in an uncommitted transaction (the `db` fixture uses rollback, not commit). Side-effect sessions use `READ COMMITTED` isolation and cannot see the uncommitted row — so `_record_failed_attempt` UPDATEs affect 0 rows during tests (silent no-op, which is correct for unit-level isolation).

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

All role checks are server-side in `app/dependencies/auth.py`. Admins implicitly pass every role check.

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
| Roles in JWT | Roles are embedded in the JWT at login/refresh time. `get_current_user` reads them from the token — no DB query for roles. Changes propagate after the next token refresh (~15 min). |

---

## Open Items

| Item | Blocks |
|------|--------|
| Confirm `logout_all_devices` cross-org revocation is intentional (no `organisation_id` filter in bulk UPDATE) | Auth PR merge |
| Confirm `APP_TIMEZONE=Asia/Kolkata` for Inscan Number date generation | Phase 1 |
| Label printer model + exact label dimensions (A6 assumed) | Phase 2 |
| `get_client_ip()` — add `X-Forwarded-For` support before production deploy behind reverse proxy | Production deploy |
| Session table cleanup — add opportunistic cleanup in `login()` or a scheduled daily job | Production deploy |

---

## Branch and PR Strategy

- `main` — stable, PRD-aligned
- `auth-module` — current working branch; PR raised after Auth milestone is complete
- Schema review changes are stashed and belong on `schema-review-pr`, not `auth-module`

Milestone order per PRD §14:  
**Phase 0:** Auth → **Phase 1:** Inward module → **Phase 2:** Outward (non-scan) → **Phase 3:** Pack Items + FIFO → **Phase 4:** Reports → **Phase 5:** Hardening
