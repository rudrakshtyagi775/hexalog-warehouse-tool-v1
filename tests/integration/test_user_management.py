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


# ── PATCH /api/admin/users/{id} ───────────────────────────────────────────────

async def test_update_user_full_name_success(client, admin_user, org, db):
    """Admin can rename another user."""
    target = await _create_user_in_org(db, org=org, email="rename@test.com", password="Rename1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"full_name": "Renamed User"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Renamed User"
    assert resp.json()["id"] == target.id


async def test_update_user_deactivate_success(client, admin_user, org, db):
    """Admin can deactivate another user."""
    target = await _create_user_in_org(db, org=org, email="deactivate@test.com", password="Deact1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_update_user_deactivate_revokes_sessions(client, admin_user, org, db):
    """Deactivating a user revokes all their active sessions in the same transaction."""
    from sqlalchemy import select as sa_select
    from app.models.user import Session as SessionModel

    target = await _create_user_in_org(
        db, org=org, email="revoke_sess@test.com", password="Revoke1!",
        roles=[UserRoleEnum.packer],
    )
    # Log in as target to create a session
    target_token_resp = await client.post(
        LOGIN_URL,
        json={"email": "revoke_sess@test.com", "password": "Revoke1!", "organisation_id": org.id},
    )
    assert target_token_resp.status_code == 200

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    # Verify all sessions for target user are revoked
    result = await db.execute(
        sa_select(SessionModel).where(
            SessionModel.user_id == target.id,
            SessionModel.revoked_at.is_(None),
        )
    )
    active_sessions = result.scalars().all()
    assert len(active_sessions) == 0


async def test_update_user_admin_cannot_deactivate_self_returns_400(client, admin_user, org, db):
    """Admin cannot deactivate their own account."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{admin_user.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "yourself" in detail or "own" in detail or "cannot" in detail


async def test_update_user_writes_audit_log(client, admin_user, org, db):
    """PATCH writes an audit_logs row with before/after data."""
    target = await _create_user_in_org(
        db, org=org, email="auditpatch@test.com", password="AuditPatch1!", full_name="Original Name"
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"full_name": "Updated Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user.update",
            AuditLog.resource_id == target.id,
            AuditLog.user_id == admin_user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.before_data["full_name"] == "Original Name"
    assert audit.after_data["full_name"] == "Updated Name"
    assert "is_active" in audit.before_data
    assert "is_active" in audit.after_data
    assert "password_hash" not in (audit.before_data or {})
    assert "password_hash" not in (audit.after_data or {})


async def test_update_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from a different org returns 404."""
    other_org = Organisation(name="Isolation Org", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(
        email="outsider2@test.com", full_name="Out", password_hash="x", is_active=True,
    )
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.flush()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.patch(
        f"{USERS_URL}/{outsider.id}",
        json={"full_name": "Hacked"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


async def test_update_user_empty_body_is_noop(client, admin_user, org, db):
    """PATCH with empty body returns 200 with unchanged data."""
    target = await _create_user_in_org(
        db, org=org, email="noop@test.com", password="Noop1!", full_name="Noop User"
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Noop User"


async def test_update_user_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(db, org=org, email="target403@test.com", password="Target1!")
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.patch(
        f"{USERS_URL}/{target.id}",
        json={"full_name": "Changed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_update_user_no_auth_returns_401(client):
    resp = await client.patch(f"{USERS_URL}/1", json={"full_name": "X"})
    assert resp.status_code == 401


# ── POST /api/admin/users/{id}/roles ─────────────────────────────────────────

async def test_assign_role_success(client, admin_user, org, db):
    """Admin can assign a new role to a user in their org."""
    target = await _create_user_in_org(db, org=org, email="assignrole@test.com", password="Assign1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    role_names = [r["role"] for r in resp.json()["roles"]]
    assert "packer" in role_names


async def test_assign_role_duplicate_returns_409(client, admin_user, org, db):
    """Assigning a role the user already has returns 409."""
    target = await _create_user_in_org(
        db, org=org, email="dup_role@test.com", password="DupRole1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 409


async def test_assign_role_writes_audit_log(client, admin_user, org, db):
    """POST /roles writes audit_logs with action admin.user_role.assign."""
    target = await _create_user_in_org(db, org=org, email="audit_role@test.com", password="AuditRole1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "inward_operator"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user_role.assign",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "user_roles"
    assert audit.after_data["user_id"] == target.id
    assert audit.after_data["role"] == "inward_operator"


async def test_assign_role_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from another org returns 404."""
    other_org = Organisation(name="Role Isolation", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(email="roleout@test.com", full_name="Out", password_hash="x", is_active=True)
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    await db.flush()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.post(
        f"{USERS_URL}/{outsider.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_assign_role_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(db, org=org, email="target_r@test.com", password="Target1!")
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.post(
        f"{USERS_URL}/{target.id}/roles",
        json={"role": "packer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_assign_role_no_auth_returns_401(client):
    resp = await client.post(f"{USERS_URL}/1/roles", json={"role": "packer"})
    assert resp.status_code == 401


# ── DELETE /api/admin/users/{id}/roles/{role} ─────────────────────────────────

async def test_revoke_role_success(client, admin_user, org, db):
    """Admin can revoke a role from a user; role no longer appears in response."""
    target = await _create_user_in_org(
        db, org=org, email="revoke_role@test.com", password="RevokeRole1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    role_names = [r["role"] for r in resp.json()["roles"]]
    assert "packer" not in role_names


async def test_revoke_role_not_assigned_returns_404(client, admin_user, org, db):
    """Revoking a role the user does not have returns 404."""
    target = await _create_user_in_org(db, org=org, email="no_role@test.com", password="NoRole1!")
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


async def test_revoke_role_admin_cannot_revoke_own_admin_returns_400(client, admin_user, org, db):
    """Admin cannot revoke their own admin role."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{admin_user.id}/roles/admin",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "own" in detail or "yourself" in detail or "cannot" in detail


async def test_revoke_role_writes_audit_log(client, admin_user, org, db):
    """DELETE /roles/{role} writes audit_logs with action admin.user_role.revoke."""
    target = await _create_user_in_org(
        db, org=org, email="audit_revoke@test.com", password="AuditRev1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.user_role.revoke",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "user_roles"
    assert audit.before_data["user_id"] == target.id
    assert audit.before_data["role"] == "packer"
    assert audit.after_data is None


async def test_revoke_role_user_not_in_org_returns_404(client, admin_user, org, db):
    """Targeting a user from another org returns 404."""
    other_org = Organisation(name="Revoke Isolation", is_active=True)
    db.add(other_org)
    await db.flush()
    outsider = User(email="revokeout@test.com", full_name="Out", password_hash="x", is_active=True)
    db.add(outsider)
    await db.flush()
    db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
    db.add(UserRole(user_id=outsider.id, organisation_id=other_org.id, role=UserRoleEnum.packer, assigned_by=outsider.id))
    await db.flush()

    token = await _login(client, admin_user, org, "AdminPass1!")
    resp = await client.delete(
        f"{USERS_URL}/{outsider.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_revoke_role_non_admin_returns_403(client, packer_user, org, db):
    target = await _create_user_in_org(
        db, org=org, email="target_rev@test.com", password="Target1!", roles=[UserRoleEnum.packer]
    )
    token = await _login(client, packer_user, org, "PackerPass1!")
    resp = await client.delete(
        f"{USERS_URL}/{target.id}/roles/packer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_revoke_role_no_auth_returns_401(client):
    resp = await client.delete(f"{USERS_URL}/1/roles/packer")
    assert resp.status_code == 401
