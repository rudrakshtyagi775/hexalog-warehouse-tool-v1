"""Integration tests for POST /api/auth/logout-all.

Behaviour under test:
  - Bearer required → 401 if missing or invalid
  - Current session (identified by session_id in JWT) is NOT revoked
  - All other active sessions for the user are revoked (revoke_reason='all_sessions_logout')
  - Already-revoked sessions are excluded from the count and not double-revoked
  - Audit log written with action='auth.logout_all_devices' in the same transaction
  - Response message includes the revoked count
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.user import Session as SessionModel
from app.services.auth_service import _hash_refresh_token

LOGIN_URL = "/api/auth/login"
LOGOUT_ALL_URL = "/api/auth/logout-all"
_COOKIE = "refresh_token"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _login(client, admin_user, org) -> tuple[str, str]:
    """Log in and return (access_token, raw_refresh_token)."""
    resp = await client.post(
        LOGIN_URL,
        json={"email": admin_user.email, "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"], resp.cookies[_COOKIE]


async def _insert_session(db, user_id: int, org_id: int) -> tuple[SessionModel, str]:
    """Directly insert an active session row without going through the login flow.

    Called AFTER the user/org rows are committed (login commits them).
    """
    raw_token = secrets.token_hex(32)
    token_hash = _hash_refresh_token(raw_token)
    now = datetime.now(tz=timezone.utc)
    session = SessionModel(
        id=uuid.uuid4(),
        refresh_token_hash=token_hash,
        user_id=user_id,
        organisation_id=org_id,
        last_used_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db.add(session)
    await db.flush()
    return session, raw_token


async def _get_session_by_token(db, raw_token: str) -> SessionModel:
    """Load the session row whose current refresh_token_hash matches raw_token."""
    token_hash = _hash_refresh_token(raw_token)
    result = await db.execute(
        select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
    )
    return result.scalar_one()


# ── Happy path ─────────────────────────────────────────────────────────────────

async def test_logout_all_success(client, admin_user, org, db):
    """logout-all returns 200 with a message containing the revoked count."""
    access_token, _ = await _login(client, admin_user, org)

    await _insert_session(db, admin_user.id, org.id)
    await _insert_session(db, admin_user.id, org.id)
    await db.commit()

    resp = await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 200
    assert "2" in resp.json()["message"]


async def test_logout_all_current_session_not_revoked(client, admin_user, org, db):
    """The session whose session_id is embedded in the JWT must NOT be revoked."""
    access_token, raw_cookie = await _login(client, admin_user, org)

    await _insert_session(db, admin_user.id, org.id)
    await db.commit()

    await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    current_session = await _get_session_by_token(db, raw_cookie)
    assert current_session.revoked_at is None


async def test_logout_all_other_sessions_revoked(client, admin_user, org, db):
    """All sessions other than the current one must have revoked_at and correct reason set."""
    access_token, _ = await _login(client, admin_user, org)

    s1, _ = await _insert_session(db, admin_user.id, org.id)
    s2, _ = await _insert_session(db, admin_user.id, org.id)
    await db.commit()

    resp = await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200

    await db.refresh(s1)
    await db.refresh(s2)
    assert s1.revoked_at is not None
    assert s1.revoke_reason == "all_sessions_logout"
    assert s2.revoked_at is not None
    assert s2.revoke_reason == "all_sessions_logout"


async def test_logout_all_audit_log_written(client, admin_user, org, db):
    """Audit log row with action='auth.logout_all_devices' is written in the same transaction."""
    access_token, _ = await _login(client, admin_user, org)

    await _insert_session(db, admin_user.id, org.id)
    await db.commit()

    resp = await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "auth.logout_all_devices",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.after_data["revoked_session_count"] == 1


async def test_logout_all_no_other_sessions(client, admin_user, org, db):
    """logout-all with no other active sessions returns 200 with count 0."""
    access_token, _ = await _login(client, admin_user, org)

    resp = await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 200
    assert "0" in resp.json()["message"]


async def test_logout_all_skips_already_revoked_sessions(client, admin_user, org, db):
    """Already-revoked sessions are excluded from the count and not double-revoked."""
    access_token, _ = await _login(client, admin_user, org)

    pre_revoked, _ = await _insert_session(db, admin_user.id, org.id)
    pre_revoked.revoked_at = datetime.now(tz=timezone.utc)
    pre_revoked.revoke_reason = "logout"
    active, _ = await _insert_session(db, admin_user.id, org.id)
    await db.commit()

    resp = await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    assert "1" in resp.json()["message"]

    await db.refresh(pre_revoked)
    assert pre_revoked.revoke_reason == "logout"  # not overwritten by logout-all


# ── Auth failures ──────────────────────────────────────────────────────────────

async def test_logout_all_missing_bearer_returns_401(client):
    """No Authorization header → 401."""
    resp = await client.post(LOGOUT_ALL_URL)
    assert resp.status_code == 401


async def test_logout_all_invalid_bearer_returns_401(client):
    """Malformed JWT in Authorization header → 401."""
    resp = await client.post(
        LOGOUT_ALL_URL,
        headers={"Authorization": "Bearer not.a.real.token"},
    )
    assert resp.status_code == 401
