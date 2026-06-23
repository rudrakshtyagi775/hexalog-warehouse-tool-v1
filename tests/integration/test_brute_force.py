"""Integration tests for brute-force protection and account lockout.

Coverage:
  1. Failed login increments failed_attempts in the users table
  2. Account locks after LOGIN_MAX_FAILURES consecutive failures
  3. Locked account rejects login (correct password too)
  4. Lockout expiry allows login again
  5. Successful login resets failed_attempts and locked_until
  6. IP rate limiting raises 429 when window count exceeds limit
  7. Rate-limit check skips silently when IP is None
  8. A new time window resets the per-IP counter

Design note — READ COMMITTED isolation:
  _record_failed_attempt and _check_ip_rate_limit use _side_effect_session_factory,
  a separate AsyncSession that commits independently of the main transaction.
  The test db fixture uses rollback at teardown, so fixtures flush but do not commit.
  Tests that need side-effect writes to see the user row must call await db.commit()
  explicitly before triggering login attempts. This is expected — committed rows
  accumulate in the test DB for the session lifetime (tables are dropped at the end).
  Unique emails (UUID-derived) prevent INSERT conflicts between tests.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

import app.services.auth_service as _auth_svc
from app.config import settings
from app.models.enums import UserRoleEnum
from app.models.user import User, UserOrganisation, UserRole
from app.services.auth_service import _check_ip_rate_limit
from app.services.password_service import hash_password

LOGIN_URL = "/api/auth/login"
_PASSWORD = "BFTestPass1!"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_committed_user(
    db,
    org,
    *,
    failed_attempts: int = 0,
    locked_until: datetime | None = None,
) -> User:
    """Create and COMMIT a user so side-effect sessions (READ COMMITTED) can see it.

    Each call uses a UUID-derived email to prevent unique-constraint conflicts
    between tests (committed rows are not rolled back between tests).
    """
    email = f"bf_{uuid.uuid4().hex[:12]}@test.com"
    u = User(
        email=email,
        password_hash=hash_password(_PASSWORD),
        full_name="BF Test User",
        is_active=True,
        failed_attempts=failed_attempts,
        locked_until=locked_until,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserRole(user_id=u.id, organisation_id=org.id, role=UserRoleEnum.admin))
    await db.commit()
    return u


def _payload(user: User, org, *, password: str = _PASSWORD) -> dict:
    return {"email": user.email, "password": password, "organisation_id": org.id}


def _unique_ip() -> str:
    """Return a unique private IP in the 10.x.x.x range, safe for INET columns."""
    n = uuid.uuid4().int
    return f"10.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"


# ── 1. Failed login increments counter ───────────────────────────────────────

async def test_failed_login_increments_failed_attempts(client, db, org):
    """A single wrong-password attempt increments failed_attempts by 1."""
    u = await _make_committed_user(db, org)

    resp = await client.post(LOGIN_URL, json=_payload(u, org, password="WrongPass1!"))

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"

    await db.refresh(u)
    assert u.failed_attempts == 1
    assert u.locked_until is None


# ── 2. Lockout triggers at threshold ─────────────────────────────────────────

async def test_account_locks_after_max_failures(client, db, org):
    """Reaching LOGIN_MAX_FAILURES consecutive failures sets locked_until."""
    # Pre-seed so the *next* failure is the threshold-crossing one.
    u = await _make_committed_user(db, org, failed_attempts=settings.LOGIN_MAX_FAILURES - 1)

    resp = await client.post(LOGIN_URL, json=_payload(u, org, password="WrongPass1!"))

    assert resp.status_code == 401

    await db.refresh(u)
    assert u.failed_attempts == settings.LOGIN_MAX_FAILURES
    assert u.locked_until is not None
    assert u.locked_until > datetime.now(tz=timezone.utc)


# ── 3. Locked account rejects every login ────────────────────────────────────

async def test_locked_account_rejects_login_with_correct_password(client, db, org):
    """An account under active lockout returns 401 even with the correct password."""
    future_lock = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.LOGIN_LOCKOUT_MINUTES
    )
    u = await _make_committed_user(db, org, locked_until=future_lock)

    resp = await client.post(LOGIN_URL, json=_payload(u, org))

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


async def test_locked_account_rejects_login_with_wrong_password(client, db, org):
    """An account under active lockout returns the same 401 with a wrong password."""
    future_lock = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.LOGIN_LOCKOUT_MINUTES
    )
    u = await _make_committed_user(db, org, locked_until=future_lock)

    resp = await client.post(LOGIN_URL, json=_payload(u, org, password="WrongPass1!"))

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


# ── 4. Lockout expiry ─────────────────────────────────────────────────────────

async def test_lockout_expiry_allows_login(client, db, org):
    """An account whose locked_until is in the past can log in normally."""
    past_lock = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    u = await _make_committed_user(db, org, locked_until=past_lock)

    resp = await client.post(LOGIN_URL, json=_payload(u, org))

    assert resp.status_code == 200
    assert "access_token" in resp.json()


# ── 5. Successful login resets the counter ────────────────────────────────────

async def test_successful_login_resets_failed_attempts(client, db, org):
    """A successful login zeroes failed_attempts and clears locked_until."""
    past_lock = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    u = await _make_committed_user(
        db, org, failed_attempts=5, locked_until=past_lock
    )

    resp = await client.post(LOGIN_URL, json=_payload(u, org))
    assert resp.status_code == 200

    await db.refresh(u)
    assert u.failed_attempts == 0
    assert u.locked_until is None


# ── 6. IP rate limiting raises 429 ───────────────────────────────────────────

async def test_ip_rate_limit_raises_429():
    """Seeding login_attempts at the limit causes the next call to raise 429."""
    test_ip = _unique_ip()
    window_start = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)

    async with _auth_svc._side_effect_session_factory() as rate_session:
        await rate_session.execute(
            text("""
                INSERT INTO login_attempts (ip_address, window_start, attempt_count, created_at)
                VALUES (:ip, :ws, :count, now())
                ON CONFLICT (ip_address, window_start)
                DO UPDATE SET attempt_count = :count
            """),
            {"ip": test_ip, "ws": window_start, "count": settings.LOGIN_RATE_LIMIT_PER_MINUTE},
        )
        await rate_session.commit()

    # Next call increments to LIMIT + 1 → 429
    with pytest.raises(HTTPException) as exc_info:
        await _check_ip_rate_limit(test_ip)

    assert exc_info.value.status_code == 429
    assert "Too many login attempts" in exc_info.value.detail


# ── 7. Rate limit skips when IP is unknown ───────────────────────────────────

async def test_ip_rate_limit_skips_when_ip_is_none():
    """_check_ip_rate_limit(None) returns silently — no write, no exception."""
    await _check_ip_rate_limit(None)  # must not raise


# ── 8. New time window resets the per-IP counter ─────────────────────────────

async def test_ip_rate_limit_new_window_resets_count():
    """A saturated counter from a prior minute does not affect the current minute."""
    test_ip = _unique_ip()
    now = datetime.now(tz=timezone.utc)
    old_window = (now - timedelta(minutes=2)).replace(second=0, microsecond=0)

    async with _auth_svc._side_effect_session_factory() as rate_session:
        await rate_session.execute(
            text("""
                INSERT INTO login_attempts (ip_address, window_start, attempt_count, created_at)
                VALUES (:ip, :ws, :count, now())
                ON CONFLICT (ip_address, window_start)
                DO UPDATE SET attempt_count = :count
            """),
            {"ip": test_ip, "ws": old_window, "count": 999},
        )
        await rate_session.commit()

    # Current-window call produces count=1 for this IP — well under the limit
    await _check_ip_rate_limit(test_ip)  # must not raise
