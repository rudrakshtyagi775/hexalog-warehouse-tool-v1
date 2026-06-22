"""Integration tests for refresh_session() — all cases from the June 18 auth redesign.

Five-case flow under test:
  Case 1 — token unknown (not in current or previous hash) → 401
  Case 2 — previous (rotated) token, still within grace window → 409
  Theft  — previous token, grace window expired → 401, session revoked
  Case 3 — current token, session already revoked → 401
  Case 4 — current token, session hard-expired → 401
  Case 5 — current token, inactivity limit exceeded → 401, session revoked
  Case 5 (happy path) — valid session → 200, token rotated, new cookie set
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.user import Session as SessionModel
from app.services.auth_service import _hash_refresh_token

LOGIN_URL = "/api/auth/login"
REFRESH_URL = "/api/auth/refresh"
ME_URL = "/api/auth/me"

_COOKIE = "refresh_token"  # mirrors routers/auth.py _COOKIE_NAME


# ── Shared helper ─────────────────────────────────────────────────────────────

async def _login(client, admin_user, org) -> tuple[str, str]:
    """Log in and return (access_token, raw_refresh_token)."""
    resp = await client.post(
        LOGIN_URL,
        json={"email": admin_user.email, "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"], resp.cookies[_COOKIE]


async def _get_session_by_token(db, raw_token) -> SessionModel:
    """Load the Session row whose current refresh_token_hash matches raw_token."""
    token_hash = _hash_refresh_token(raw_token)
    result = await db.execute(
        select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
    )
    return result.scalar_one()


# ── Happy path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_success(client, admin_user, org):
    """Valid session returns 200 with a new access token and a rotated cookie."""
    _, t0 = await _login(client, admin_user, org)

    resp = await client.post(REFRESH_URL, cookies={_COOKIE: t0})

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_at" in data
    assert _COOKIE in resp.cookies
    assert resp.cookies[_COOKIE] != t0  # token was rotated


@pytest.mark.asyncio
async def test_refresh_new_access_token_is_valid(client, admin_user, org):
    """Access token issued by a successful refresh is accepted on authenticated endpoints."""
    _, t0 = await _login(client, admin_user, org)

    r = await client.post(REFRESH_URL, cookies={_COOKIE: t0})
    assert r.status_code == 200
    new_access_token = r.json()["access_token"]

    me = await client.get(ME_URL, headers={"Authorization": f"Bearer {new_access_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == admin_user.email


@pytest.mark.asyncio
async def test_refresh_rotation_stores_previous_hash(client, admin_user, org, db):
    """After rotation the session row carries the old hash in previous_refresh_token_hash."""
    _, t0 = await _login(client, admin_user, org)
    t0_hash = _hash_refresh_token(t0)

    r = await client.post(REFRESH_URL, cookies={_COOKIE: t0})
    assert r.status_code == 200
    t1 = r.cookies[_COOKIE]

    session = await _get_session_by_token(db, t1)
    assert session.previous_refresh_token_hash == t0_hash
    assert session.previous_token_valid_until is not None
    assert session.last_used_at is not None


# ── Grace window — Case 2 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_grace_window_replay_returns_409(client, admin_user, org):
    """Replay of a rotated token within REFRESH_REUSE_GRACE_SECONDS → 409, not 401.

    409 tells the client it lost a race: use the new cookie, not the old one.
    No session is revoked.
    """
    _, t0 = await _login(client, admin_user, org)

    r1 = await client.post(REFRESH_URL, cookies={_COOKIE: t0})
    assert r1.status_code == 200

    # Immediate replay of T0 — still within the grace window
    r2 = await client.post(REFRESH_URL, cookies={_COOKIE: t0})
    assert r2.status_code == 409
    assert "Refresh already rotated" in r2.json()["detail"]


# ── Theft detection ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_theft_detected_after_grace_window(client, admin_user, org, db):
    """Replay of a rotated token after the grace window expires → 401, session revoked."""
    _, t0 = await _login(client, admin_user, org)

    # First rotation: T0 → T1
    r1 = await client.post(REFRESH_URL, cookies={_COOKIE: t0})
    assert r1.status_code == 200
    t1 = r1.cookies[_COOKIE]

    # Expire the grace window by backdating previous_token_valid_until
    session = await _get_session_by_token(db, t1)
    session.previous_token_valid_until = datetime.now(tz=timezone.utc) - timedelta(seconds=60)
    await db.commit()

    # Replay T0 — grace window has passed → theft detected
    r2 = await client.post(REFRESH_URL, cookies={_COOKIE: t0})
    assert r2.status_code == 401
    assert "theft" in r2.json()["detail"].lower()

    # Session must be revoked
    await db.refresh(session)
    assert session.revoked_at is not None
    assert session.revoke_reason == "theft_detected"


# ── Already revoked — Case 3 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_revoked_session(client, admin_user, org, db):
    """Refresh token whose session is already revoked → 401."""
    _, raw_token = await _login(client, admin_user, org)

    session = await _get_session_by_token(db, raw_token)
    session.revoked_at = datetime.now(tz=timezone.utc)
    session.revoke_reason = "admin_revoke"
    await db.commit()

    resp = await client.post(REFRESH_URL, cookies={_COOKIE: raw_token})
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()


# ── Hard-expired — Case 4 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_expired_session(client, admin_user, org, db):
    """Refresh token whose session has passed its absolute expiry → 401."""
    _, raw_token = await _login(client, admin_user, org)

    session = await _get_session_by_token(db, raw_token)
    session.expires_at = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await db.commit()

    resp = await client.post(REFRESH_URL, cookies={_COOKIE: raw_token})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


# ── Inactivity timeout — Case 5 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_inactivity_timeout(client, admin_user, org, db):
    """Session idle beyond SESSION_INACTIVITY_MINUTES → 401, session revoked.

    The inactivity-timeout code path currently omits an audit log write
    (CLAUDE.md open item — fix before auth-module PR merge).
    """
    _, raw_token = await _login(client, admin_user, org)

    session = await _get_session_by_token(db, raw_token)
    session.last_used_at = datetime.now(tz=timezone.utc) - timedelta(
        minutes=settings.SESSION_INACTIVITY_MINUTES + 1
    )
    await db.commit()

    resp = await client.post(REFRESH_URL, cookies={_COOKIE: raw_token})
    assert resp.status_code == 401
    assert "inactivity" in resp.json()["detail"].lower()

    await db.refresh(session)
    assert session.revoked_at is not None
    assert session.revoke_reason == "inactivity_timeout"


# ── Missing / unknown token — Case 1 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_missing_cookie(client):
    """No refresh token cookie at all → 401."""
    resp = await client.post(REFRESH_URL)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing refresh token"


@pytest.mark.asyncio
async def test_refresh_unknown_token(client):
    """Token present but not found as current or previous hash → 401."""
    resp = await client.post(REFRESH_URL, cookies={_COOKIE: "unknown_token_not_in_db"})
    assert resp.status_code == 401
    assert "not found" in resp.json()["detail"].lower()
