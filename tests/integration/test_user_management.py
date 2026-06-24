"""Integration tests for User Management & RBAC endpoints.

Endpoints under test:
  GET    /api/admin/users                   — list org users (admin only)
  POST   /api/admin/users                   — create user (admin only)
  PATCH  /api/admin/users/{id}              — update user (admin only)
  POST   /api/admin/users/{id}/roles        — assign role (admin only)
  DELETE /api/admin/users/{id}/roles/{role} — revoke role (admin only)
  POST   /api/admin/users/{id}/password-reset — admin password reset (admin only)

Invariants verified:
  - All queries scoped to current_user.organisation_id
  - password_hash never appears in any response
  - Audit log written in the same transaction as every state change
  - Admin cannot deactivate themselves
  - Admin cannot revoke their own admin role
  - Deactivating a user revokes all their sessions in the same transaction
  - Email uniqueness is system-wide
"""

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole

LOGIN_URL = "/api/auth/login"
USERS_URL = "/api/admin/users"


async def _login(client, user, org, password: str) -> str:
    resp = await client.post(
        LOGIN_URL,
        json={"email": user.email, "password": password, "organisation_id": org.id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"]


async def _create_user_in_org(db, *, org, email: str, password: str, roles=None, full_name="Test User"):
    """Insert a user directly via DB — stays in test transaction (flush, not commit)."""
    from app.services.password_service import hash_password
    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(UserOrganisation(user_id=user.id, organisation_id=org.id, created_by=user.id))
    for role in (roles or []):
        db.add(UserRole(user_id=user.id, organisation_id=org.id, role=role, assigned_by=user.id))
    await db.flush()
    await db.refresh(user)
    return user


# ── GET /api/admin/users ──────────────────────────────────────────────────────

async def test_list_users_returns_org_users(client, admin_user, org, db):
    """GET /users returns all users belonging to the admin's organisation."""
    target = await _create_user_in_org(
        db, org=org, email="operator@test.com", password="Operator1!",
        roles=[UserRoleEnum.inward_operator],
    )

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()]
    assert admin_user.id in ids
    assert target.id in ids


async def test_list_users_excludes_other_org_users(client, admin_user, org, db):
    """Users in a different org must not appear in the response."""
    other_org = Organisation(name="Other Org", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(
        email="outsider@test.com",
        full_name="Outsider",
        password_hash="x",
        is_active=True,
    )
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.flush()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()]
    assert outsider.id not in ids


async def test_list_users_response_excludes_password_hash(client, admin_user, org, db):
    """No user object in the list may contain password_hash."""
    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    for user in resp.json():
        assert "password_hash" not in user


async def test_list_users_non_admin_returns_403(client, packer_user, org, db):
    token = await _login(client, packer_user, org, "PackerPass1!")
    resp = await client.get(USERS_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_list_users_no_auth_returns_401(client):
    resp = await client.get(USERS_URL)
    assert resp.status_code == 401


# ── POST /api/admin/users ─────────────────────────────────────────────────────

async def test_create_user_success(client, admin_user, org, db):
    """Admin can create a new user; response has correct fields."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "new@test.com", "full_name": "New User", "password": "NewPass1!", "roles": ["packer"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "new@test.com"
    assert data["full_name"] == "New User"
    assert data["is_active"] is True
    assert any(r["role"] == "packer" for r in data["roles"])
    assert "id" in data
    assert "created_at" in data


async def test_create_user_response_excludes_password_hash(client, admin_user, org, db):
    """Created user response must not expose password_hash."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "secure@test.com", "full_name": "Secure", "password": "SecurePass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    assert "password_hash" not in resp.json()


async def test_create_user_duplicate_email_returns_409(client, admin_user, org, db):
    """Creating a user with an already-registered email returns 409."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    await client.post(
        USERS_URL,
        json={"email": "dup@test.com", "full_name": "First", "password": "FirstPass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        USERS_URL,
        json={"email": "dup@test.com", "full_name": "Second", "password": "SecondPass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 409


async def test_create_user_writes_audit_log(client, admin_user, org, db):
    """POST /users writes an audit_logs row with action admin.user.create."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "audited@test.com", "full_name": "Audited", "password": "AuditPass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    new_user_id = resp.json()["id"]

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user.create",
            AuditLog.resource_id == new_user_id,
            AuditLog.user_id == admin_user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "users"
    assert audit.after_data["email"] == "audited@test.com"
    assert "password_hash" not in (audit.after_data or {})
    assert "password" not in (audit.after_data or {})


async def test_create_user_no_roles_succeeds(client, admin_user, org, db):
    """User can be created without any roles (empty roles list is valid)."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        USERS_URL,
        json={"email": "norole@test.com", "full_name": "No Role", "password": "NoRolePass1!"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    assert resp.json()["roles"] == []


async def test_create_user_non_admin_returns_403(client, packer_user, org, db):
    token = await _login(client, packer_user, org, "PackerPass1!")
    resp = await client.post(
        USERS_URL,
        json={"email": "x@test.com", "full_name": "X", "password": "XPassword1!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_create_user_no_auth_returns_401(client):
    resp = await client.post(USERS_URL, json={"email": "x@test.com", "full_name": "X", "password": "XPassword1!"})
    assert resp.status_code == 401
