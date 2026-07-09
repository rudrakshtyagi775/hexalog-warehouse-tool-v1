"""Integration tests for admin session management endpoints.

Endpoints under test:
  DELETE /api/admin/sessions/{session_id}
    - Admin revokes any single active session in their org → 200
    - Already-revoked session → 404
    - Session in a different org → 404
    - Non-existent session_id → 404
    - Non-admin caller → 403

  DELETE /api/admin/users/{user_id}/sessions
    - Admin revokes all active sessions for a user in their org → 200, count returned
    - User has no active sessions → 200, count=0
    - Target user not in admin's org → 404
    - Non-admin caller → 403
    - Audit log written in the same transaction
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.organisation import Organisation
from app.models.user import Session as SessionModel
from app.models.user import User
from app.services.auth_service import _hash_refresh_token
from app.services.password_service import hash_password

LOGIN_URL = "/api/auth/login"
_COOKIE = "refresh_token"

REVOKE_SESSION_URL = "/api/admin/sessions/{session_id}"
REVOKE_USER_SESSIONS_URL = "/api/admin/users/{user_id}/sessions"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _login(client, email: str, password: str, org_id: int) -> str:
    """Log in and return the access token."""
    resp = await client.post(
        LOGIN_URL,
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"]


async def _insert_session(db, user_id: int, org_id: int) -> tuple[SessionModel, str]:
    """Directly insert a session row and return (session, raw_refresh_token).

    Used to create sessions without going through the login flow, so we can
    test revocation without committing the user fixture via login().
    Called AFTER the user/org rows are committed (login commits them).
    """
    raw_token = secrets.token_hex(32)
    token_hash = _hash_refresh_token(raw_token)
    now = datetime.now(tz=UTC)
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


# ── DELETE /api/admin/sessions/{session_id} ────────────────────────────────────

async def test_revoke_session_success(client, admin_user, packer_user, org, db):
    """Admin can revoke an active session in their org → 200, revoked_at set."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    # Create a second session for the packer user directly in the DB
    session, _ = await _insert_session(db, packer_user.id, org.id)
    await db.commit()

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=session.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["message"] == "Session revoked"

    await db.refresh(session)
    assert session.revoked_at is not None
    assert session.revoked_by == admin_user.id
    assert session.revoke_reason == "admin_force"


async def test_revoke_session_sets_revoked_by(client, admin_user, packer_user, org, db):
    """revoked_by is set to the admin's user_id, not the session owner's."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    session, _ = await _insert_session(db, packer_user.id, org.id)
    await db.commit()

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=session.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    await db.refresh(session)
    assert session.revoked_by == admin_user.id


async def test_revoke_session_already_revoked_returns_404(client, admin_user, packer_user, org, db):
    """Attempting to revoke an already-revoked session returns 404."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    session, _ = await _insert_session(db, packer_user.id, org.id)
    session.revoked_at = datetime.now(tz=UTC)
    session.revoke_reason = "logout"
    await db.commit()

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=session.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


async def test_revoke_session_nonexistent_returns_404(client, admin_user, org, db):
    """A UUID that doesn't exist as a session returns 404."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=uuid.uuid4()),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


async def test_revoke_session_wrong_org_returns_404(client, admin_user, packer_user, org, db):
    """Session belonging to a different org returns 404 (no cross-org revocation)."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    # Create a second org and a session scoped to it
    org2 = Organisation(name="Other Org", is_active=True)
    db.add(org2)
    await db.flush()
    session, _ = await _insert_session(db, packer_user.id, org2.id)
    await db.commit()

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=session.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404

    # Session must still be active
    await db.refresh(session)
    assert session.revoked_at is None


async def test_revoke_session_requires_admin(client, packer_user, org, db):
    """Packer (non-admin) cannot call the revoke-session endpoint → 403."""
    packer_token = await _login(client, packer_user.email, "PackerPass1!", org.id)

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=uuid.uuid4()),
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 403


async def test_revoke_session_requires_bearer(client):
    """No Authorization header → 401."""
    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=uuid.uuid4()),
    )
    assert resp.status_code == 401


async def test_revoke_session_audit_log_written(client, admin_user, packer_user, org, db):
    """Audit log row with action='session.force_revoked' is written in the same transaction."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    session, _ = await _insert_session(db, packer_user.id, org.id)
    await db.commit()

    resp = await client.delete(
        REVOKE_SESSION_URL.format(session_id=session.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "session.force_revoked",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.after_data["target_user_id"] == packer_user.id
    assert audit.after_data["target_session_id"] == str(session.id)


# ── DELETE /api/admin/users/{user_id}/sessions ────────────────────────────────

async def test_revoke_user_sessions_success(client, admin_user, packer_user, org, db):
    """Admin revokes all active sessions for a user → 200, correct count."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    # Create two additional sessions for packer_user in this org
    s1, _ = await _insert_session(db, packer_user.id, org.id)
    s2, _ = await _insert_session(db, packer_user.id, org.id)
    await db.commit()

    resp = await client.delete(
        REVOKE_USER_SESSIONS_URL.format(user_id=packer_user.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    assert "2" in resp.json()["message"]

    await db.refresh(s1)
    await db.refresh(s2)
    assert s1.revoked_at is not None
    assert s2.revoked_at is not None
    assert s1.revoke_reason == "admin_force"
    assert s2.revoke_reason == "admin_force"


async def test_revoke_user_sessions_no_active_sessions(client, admin_user, packer_user, org, db):
    """User with no active sessions returns 200 with count=0."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    # Pre-revoke any sessions packer_user might have
    session, _ = await _insert_session(db, packer_user.id, org.id)
    session.revoked_at = datetime.now(tz=UTC)
    session.revoke_reason = "logout"
    await db.commit()

    resp = await client.delete(
        REVOKE_USER_SESSIONS_URL.format(user_id=packer_user.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200
    assert "0" in resp.json()["message"]


async def test_revoke_user_sessions_wrong_org_returns_404(client, admin_user, org, db):
    """Target user not in admin's org → 404."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    # Create a user that is NOT in the admin's org
    other_user = User(
        email=f"other_{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("OtherPass1!"),
        full_name="Other User",
        is_active=True,
    )
    db.add(other_user)
    await db.flush()
    # Intentionally not adding to org — this user is in no org
    await db.commit()

    resp = await client.delete(
        REVOKE_USER_SESSIONS_URL.format(user_id=other_user.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert "organisation" in resp.json()["detail"].lower()


async def test_revoke_user_sessions_cross_org_sessions_untouched(
    client, admin_user, packer_user, org, db
):
    """Sessions in a different org are NOT revoked even if user_id matches."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    org2 = Organisation(name="Cross-Org", is_active=True)
    db.add(org2)
    await db.flush()
    # packer_user has a session in org2 (created e.g. after switch-organisation)
    cross_session, _ = await _insert_session(db, packer_user.id, org2.id)
    await db.commit()

    await client.delete(
        REVOKE_USER_SESSIONS_URL.format(user_id=packer_user.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # The org2 session must still be alive
    await db.refresh(cross_session)
    assert cross_session.revoked_at is None


async def test_revoke_user_sessions_requires_admin(client, packer_user, org, db):
    """Packer (non-admin) cannot call the revoke-user-sessions endpoint → 403."""
    packer_token = await _login(client, packer_user.email, "PackerPass1!", org.id)

    resp = await client.delete(
        REVOKE_USER_SESSIONS_URL.format(user_id=packer_user.id),
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 403


async def test_revoke_user_sessions_audit_log_written(client, admin_user, packer_user, org, db):
    """Audit log row with action='session.all_revoked' is written in the same transaction."""
    admin_token = await _login(client, admin_user.email, "AdminPass1!", org.id)

    s, _ = await _insert_session(db, packer_user.id, org.id)
    await db.commit()

    resp = await client.delete(
        REVOKE_USER_SESSIONS_URL.format(user_id=packer_user.id),
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "session.all_revoked",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.after_data["target_user_id"] == packer_user.id
    assert audit.after_data["revoked_count"] >= 1
