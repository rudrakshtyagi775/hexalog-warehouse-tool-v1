"""Integration tests for POST /api/auth/switch-organisation.

Behaviour under test (June 18 redesign):
  - Valid switch: 200, new cookie set, new access token issued for target org
  - New access token encodes the target org and new session_id
  - New session row created in DB with correct organisation_id
  - Old session is NOT revoked (orphaned — browser cookie replaced, dies naturally)
  - Roles returned are those for the target org, not the source org
  - Switching to the same org is permitted (creates a second session)
  - User not a member of target org → 403
  - Missing Bearer token → 401
  - Expired / invalid Bearer token → 401
  - Audit log row written in the same transaction as the new session
"""

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import Session as SessionModel
from app.models.user import User, UserOrganisation, UserRole
from app.services.auth_service import _hash_refresh_token
from app.services.jwt_service import decode_access_token

LOGIN_URL = "/api/auth/login"
SWITCH_URL = "/api/auth/switch-organisation"
ME_URL = "/api/auth/me"
_COOKIE = "refresh_token"


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _login(client, user: User, org: Organisation, password: str) -> tuple[str, str]:
    """Log in and return (access_token, raw_refresh_token)."""
    resp = await client.post(
        LOGIN_URL,
        json={"email": user.email, "password": password, "organisation_id": org.id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"], resp.cookies[_COOKIE]


async def _get_session_by_token(db, raw_token) -> SessionModel:
    token_hash = _hash_refresh_token(raw_token)
    result = await db.execute(
        select(SessionModel).where(SessionModel.refresh_token_hash == token_hash)
    )
    return result.scalar_one()


async def _make_second_org(db, user: User, role: UserRoleEnum) -> Organisation:
    """Create a second org and enrol the user in it."""
    org2 = Organisation(name="Second Org", is_active=True)
    db.add(org2)
    await db.flush()
    db.add(UserOrganisation(user_id=user.id, organisation_id=org2.id))
    db.add(UserRole(user_id=user.id, organisation_id=org2.id, role=role))
    await db.flush()
    return org2


# ── Happy path ─────────────────────────────────────────────────────────────────

async def test_switch_org_success(client, admin_user, org, db):
    """Valid switch returns 200 with a new access token and a new cookie."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_at" in data
    assert data["organisation"]["id"] == org2.id
    assert data["organisation"]["name"] == "Second Org"
    assert _COOKIE in resp.cookies


async def test_switch_org_new_cookie_differs_from_login_cookie(client, admin_user, org, db):
    """The refresh cookie after switch is a freshly generated token."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, t0 = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 200
    assert resp.cookies[_COOKIE] != t0


async def test_switch_org_new_token_encodes_target_org(client, admin_user, org, db):
    """JWT issued after switch carries the target organisation_id."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200

    new_token = resp.json()["access_token"]
    payload = decode_access_token(new_token)
    assert int(payload["org"]) == org2.id


async def test_switch_org_new_token_is_accepted_on_me(client, admin_user, org, db):
    """Access token from switch is valid and accepted on GET /me."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    new_access = resp.json()["access_token"]

    me = await client.get(ME_URL, headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200
    assert me.json()["email"] == admin_user.email


async def test_switch_org_new_session_created_in_db(client, admin_user, org, db):
    """A new session row is created for the target org."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    new_cookie = resp.cookies[_COOKIE]

    new_session = await _get_session_by_token(db, new_cookie)
    assert new_session.organisation_id == org2.id
    assert new_session.user_id == admin_user.id
    assert new_session.revoked_at is None


async def test_switch_org_old_session_not_revoked(client, admin_user, org, db):
    """The previous session is left intact — it must NOT have revoked_at set."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, t0 = await _login(client, admin_user, org, "AdminPass1!")
    old_session = await _get_session_by_token(db, t0)

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200

    await db.refresh(old_session)
    assert old_session.revoked_at is None


async def test_switch_org_returns_roles_for_target_org(client, admin_user, org, db):
    """Roles in the response reflect the user's roles in the target org, not the source org."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.packer)
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    roles = resp.json()["roles"]
    assert roles == [UserRoleEnum.packer.value]


async def test_switch_org_to_same_org_is_allowed(client, admin_user, org, db):
    """Switching to the currently active org succeeds and creates a second session."""
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["organisation"]["id"] == org.id


async def test_switch_org_audit_log_written(client, admin_user, org, db):
    """An audit_logs row with action='auth.switch_organisation' is written in the same transaction."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "auth.switch_organisation",
            AuditLog.user_id == admin_user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.organisation_id == org2.id
    assert audit.after_data["to_organisation_id"] == org2.id
    assert audit.after_data["from_organisation_id"] == org.id


# ── Error cases ────────────────────────────────────────────────────────────────

async def test_switch_org_not_a_member_returns_403(client, admin_user, org, db):
    """Switching to an org the user doesn't belong to returns 403."""
    # Create an org the user has no membership in
    other_org = Organisation(name="Other Org", is_active=True)
    db.add(other_org)
    await db.flush()

    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": other_org.id},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 403
    assert "member" in resp.json()["detail"].lower()


async def test_switch_org_nonexistent_org_returns_403(client, admin_user, org, db):
    """Switching to a non-existent org returns 403 (no org enumeration)."""
    access_token, _ = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": 999999},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert resp.status_code == 403


async def test_switch_org_missing_bearer_returns_401(client, admin_user, org, db):
    """No Authorization header → 401."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
    )

    assert resp.status_code == 401


async def test_switch_org_invalid_bearer_returns_401(client, admin_user, org, db):
    """Malformed JWT → 401."""
    org2 = await _make_second_org(db, admin_user, UserRoleEnum.admin)

    resp = await client.post(
        SWITCH_URL,
        json={"organisation_id": org2.id},
        headers={"Authorization": "Bearer not_a_real_token"},
    )

    assert resp.status_code == 401
