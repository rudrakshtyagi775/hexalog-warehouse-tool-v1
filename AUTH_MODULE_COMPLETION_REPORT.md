# Auth Module Completion Report

**Branch:** `auth-module` | **Date:** 2026-06-23 | **Reviewer:** Claude Code  
**Compared against:** June 18 Auth Redesign · CLAUDE.md · Arpit review

---

## Summary

All 8 auth milestone items are implemented. **One production blocker remains** before this branch can be merged: the inactivity timeout revocation path in `refresh_session()` commits without writing an audit log, violating the audit invariant.

---

## Endpoints — Status

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| POST | `/api/auth/login` | ✅ complete | Brute-force, timing parity, org membership check |
| POST | `/api/auth/refresh` | ✅ complete | 5-case flow, grace window, theft detection |
| POST | `/api/auth/logout` | ✅ complete | Cookie-only, idempotent |
| POST | `/api/auth/logout-all` | ✅ complete | Keeps current session alive |
| GET | `/api/auth/me` | ✅ complete | No extra DB query |
| POST | `/api/auth/switch-organisation` | ✅ complete | Old session intentionally orphaned |
| DELETE | `/api/admin/sessions/:id` | ✅ complete | FOR UPDATE, org-scoped |
| DELETE | `/api/admin/users/:id/sessions` | ✅ complete | Membership check, org-scoped |

---

## Test Coverage

| File | Tests | Scope |
|------|-------|-------|
| `tests/unit/test_password_service.py` | 6 | hash, verify, salting, dummy hash |
| `tests/unit/test_jwt_service.py` | 5 | issue, decode, expired, tampered, alg:none rejection |
| `tests/unit/test_audit_service.py` | 11 | row insert, password_hash strip, NULL org |
| `tests/unit/test_timing_parity.py` | 1 | N=5 samples, 150ms tolerance |
| `tests/integration/test_login.py` | 7 | happy path, all failure modes, timing parity |
| `tests/integration/test_logout.py` | 6 | cookie-only logout, double-logout idempotency |
| `tests/integration/test_me.py` | 4 | happy path, no password_hash in response |
| `tests/integration/test_rbac.py` | 3 | role content via /me |
| `tests/integration/test_brute_force.py` | 9 | failed_attempts, lockout, IP rate limit, DB state |
| `tests/integration/test_switch_organisation.py` | 13 | happy path, old-session orphan, roles, audit log |
| `tests/integration/test_admin_sessions.py` | 14 | revoke-single, revoke-all, 403/404, audit log |
| **Total** | **79** | |

---

## Security Invariants — Verification

| Invariant | Verified | Evidence |
|-----------|----------|---------|
| JWT decode uses `algorithms=["HS256"]` explicitly | ✅ | `jwt_service.py` — `_ALGORITHM = "HS256"` hardcoded; `decode_access_token` passes `algorithms=[_ALGORITHM]` |
| `alg:none` rejected unconditionally | ✅ | `test_jwt_service.py::test_decode_alg_none_rejected` |
| Raw refresh token never stored | ✅ | Only `_hash_refresh_token(raw_token)` stored; raw token returned to client and discarded |
| `password_hash` absent from all API responses | ✅ | No Pydantic schema exposes it; `write_audit_log` strips it unconditionally |
| `password_hash` absent from audit log data | ✅ | `audit_service.py` strips `password_hash` from `before_data` and `after_data` before insert |
| `organisation_id` from JWT, never request body | ✅ | All service functions receive `organisation_id` from `CurrentUser` (decoded from JWT `org` claim) |
| `tokens_invalidated_at` does not exist | ✅ | Absent from models, migration, and all code |
| Timing oracle prevention | ✅ | `verify_password(password, DUMMY_HASH)` always runs before raising 401 for unknown email or locked account |
| All auth failures return identical message | ✅ | `_INVALID_CREDENTIALS = "Invalid credentials"` — single constant used for all 401s |
| Audit log in same transaction as event | ✅ except Case 5 | See production blocker below |
| `is_active` checked on every request | ✅ | `get_current_user` SELECTs the User row and checks `is_active` per authenticated request |
| Roles from JWT, not DB (on access) | ✅ | `get_current_user` reads roles from JWT claims; no second DB query |
| Multi-tenancy on admin endpoints | ✅ | `revoke_session` and `revoke_user_sessions` filter by `organisation_id == admin_org_id` |
| HMAC key used for refresh token hash | ✅ | `HMAC-SHA256(REFRESH_TOKEN_HASH_KEY, raw_token)` in `_hash_refresh_token()` |

---

## June 18 Redesign Compliance

| Design requirement | Status | Notes |
|-------------------|--------|-------|
| Rotating refresh token with grace window | ✅ | `REFRESH_REUSE_GRACE_SECONDS` configurable; previous hash stored with `previous_token_valid_until` |
| 409 on in-grace-window replay | ✅ | Case 2 in `refresh_session()` |
| `theft_detected` on expired-grace replay with audit log | ✅ | Case 2 — `revoke_reason="theft_detected"`, audit logged |
| Inactivity timeout revocation | ✅ | Case 5 — `revoke_reason="inactivity_timeout"` |
| **Inactivity timeout audit log** | ❌ BLOCKER | Case 5 commits with no `write_audit_log` call |
| Old session NOT revoked on org switch | ✅ | Explicitly confirmed in `switch_organisation()` comment |
| Cookie attributes: httpOnly, Secure, SameSite=Strict, Path=/api/auth | ✅ | `_set_refresh_cookie()` in `routers/auth.py` |
| Brute-force: per-IP sliding window | ✅ | `login_attempts` table, `_check_ip_rate_limit()` |
| Brute-force: per-account lockout | ✅ | `users.failed_attempts` + `users.locked_until`, `_record_failed_attempt()` |
| Side-effect sessions commit independently | ✅ | `_side_effect_session_factory` pattern |
| `session_id` in JWT claims | ✅ | `issue_access_token` includes `session_id`; `get_current_user` extracts it |
| `CurrentUser` carries `session_id` | ✅ | Used by `logout_all_devices` to exclude current session |
| FOR UPDATE on session SELECT (admin revoke) | ✅ | `revoke_session()` uses `.with_for_update()` |

---

## Production Blocker

### `refresh_session()` Case 5 — inactivity timeout has no audit log

**File:** `app/services/auth_service.py:333–340`

```python
# Case 5: Valid — check inactivity then rotate
inactivity_limit = timedelta(minutes=settings.SESSION_INACTIVITY_MINUTES)
if (now - session.last_used_at) > inactivity_limit:
    session.revoked_at = now
    session.revoke_reason = "inactivity_timeout"
    await db.commit()          # ← commits with no write_audit_log call
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session expired due to inactivity",
    )
```

**Invariant violated:** "Audit log writes happen in the SAME DB transaction as the event that caused them. No event without an audit row is possible by construction."

**Fix:** Add `write_audit_log(...)` before `await db.commit()` with `action="session.inactivity_timeout"`, mirroring the pattern used by the theft-detected case.

**Severity:** Merge blocker. Every other revocation event (logout, logout-all, admin force, theft detected, org switch) is audited. This is the only missing one.

---

## Deferred Items (not merge blockers)

### 1. `get_client_ip()` — no proxy header support

**File:** `app/utils/request.py`

Uses `request.client.host if request.client else None`. When deployed behind nginx/caddy, `request.client.host` will always be the proxy's IP. Brute-force per-IP limiting becomes ineffective.

**Fix before production deploy:** Parse `X-Forwarded-For` or `X-Real-IP` (with trusted proxy validation). Not needed for local dev or the auth PR itself.

### 2. Session table — no cleanup of expired rows

Expired and revoked sessions accumulate indefinitely. No background job or cleanup-on-login logic purges stale rows.

**Fix before production:** Add opportunistic cleanup in `login()` (delete sessions older than `SESSION_ABSOLUTE_EXPIRE_HOURS` for the user on successful login) or a scheduled daily job. Not scoped to V1 auth milestone.

### 3. `logout_all_devices` — revokes sessions across all orgs

**File:** `app/services/auth_service.py:537–545`

The bulk UPDATE has no `organisation_id` filter — it revokes all sessions for `user_id` regardless of which org the session belongs to. This is likely intentional (a user logging out all devices should mean everywhere), but it differs from `revoke_user_sessions` (admin-scoped to one org) and could surprise users who expect per-org isolation.

**Action:** Confirm with Arpit whether this is the intended behaviour. Add a comment if intentional.

### 4. `test_rbac.py` — role enforcement not tested end-to-end

Tests verify that the `/me` response contains the expected roles but do not make requests to role-protected endpoints (e.g. a packer calling an admin endpoint and expecting 403). `test_admin_sessions.py` covers the 403 path for admin endpoints specifically, so the gap is narrow — but a dedicated RBAC smoke test for each dependency (`require_inward_operator`, `require_packer`) is missing.

### 5. `APP_TIMEZONE` not confirmed

`APP_TIMEZONE=Asia/Kolkata` for Inscan Number date generation is flagged as an open item in CLAUDE.md. This is a Phase 1 concern, not auth.

---

## Migration — Verified

Alembic migration `9014f72e9f0c` (initial schema) covers:

- `organisations` — id, name, is_active, timestamps
- `users` — id, email, password_hash, full_name, is_active, **failed_attempts**, **locked_until**, NO `tokens_invalidated_at`
- `user_organisations` — composite PK (user_id, org_id)
- `user_roles` — user_id, org_id, role enum
- `sessions` — id (UUID PK), refresh_token_hash, previous_refresh_token_hash, previous_token_valid_until, user_id, org_id, last_used_at, expires_at, revoked_at, revoked_by, revoke_reason, ip_address (INET), user_agent
- `login_attempts` — ip_address (INET), window_start, attempt_count, unique(ip, window)
- `audit_logs` — BIGSERIAL PK, action, module, resource_type, resource_id, user_id, org_id, before_data (JSONB), after_data (JSONB), ip_address (INET), created_at

All columns align with the June 18 redesign. No schema drift detected.

---

## Before PR Merge — Checklist

- [ ] **Fix Case 5 audit log** in `refresh_session()` — the only blocker
- [ ] Run full test suite: `pytest tests/ -v` — confirm 0 failures  
- [ ] Confirm `logout_all_devices` cross-org revocation behaviour with Arpit
- [ ] Add comment in `logout_all_devices` documenting the intentional (or corrected) org-scope decision
