from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.models.user import User, UserOrganisation, UserRole
from app.services.password_service import hash_password


async def _login(client: AsyncClient, email: str, password: str, org_id: int) -> str:
    r = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


async def _create_user_in_org(
    db, *, org, email: str, password: str, roles=None, full_name: str = "Test User"
) -> User:
    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    # User.user_organisations uses lazy="noload" — appending via the relationship
    # (rather than only setting the raw FK) keeps the in-memory collection correct
    # so a later selectinload() in the same session-shared test client isn't left
    # stuck at an empty noload default.
    user_org = UserOrganisation(user_id=user.id, organisation_id=org.id, created_by=user.id)
    db.add(user_org)
    user.user_organisations.append(user_org)
    for role in (roles or []):
        db.add(UserRole(user_id=user.id, organisation_id=org.id, role=role, assigned_by=user.id))
    await db.commit()
    return user


class TestListUsers:
    async def test_list_users_success(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert any(u["email"] == "admin@test.com" for u in data)

    async def test_list_users_requires_admin(self, client, packer_user, org):
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
        r = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_list_users_requires_auth(self, client):
        r = await client.get("/api/admin/users")
        assert r.status_code == 401


class TestCreateUser:
    async def test_create_user_success(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.post(
            "/api/admin/users",
            json={
                "email": "newuser@test.com",
                "full_name": "New User",
                "password": "NewPass1!",
                "roles": ["packer"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["email"] == "newuser@test.com"
        assert any(r["role"] == "packer" for r in data["roles"])

    async def test_create_user_duplicate_email(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        # admin@test.com already exists
        r = await client.post(
            "/api/admin/users",
            json={
                "email": "admin@test.com",
                "full_name": "Dup",
                "password": "DupPass1!",
                "roles": ["packer"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 409

    async def test_create_user_requires_admin(self, client, packer_user, org):
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
        r = await client.post(
            "/api/admin/users",
            json={
                "email": "x@x.com",
                "full_name": "X",
                "password": "XPass1!!",
                "roles": ["packer"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


class TestUpdateUser:
    async def test_update_full_name(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="rename@test.com", password="Rename1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{target.id}",
            json={"full_name": "Renamed"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert r.json()["full_name"] == "Renamed"

    async def test_deactivate_user(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="deact@test.com", password="Deact1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{target.id}",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert r.json()["is_active"] is False

    async def test_deactivate_blocks_subsequent_requests(self, client, admin_user, org, db):
        """Deactivating a user causes their subsequent requests to return 401."""
        target = await _create_user_in_org(
            db, org=org, email="block@test.com", password="Block1!", roles=[UserRoleEnum.packer]
        )
        target_token = await _login(client, "block@test.com", "Block1!", org.id)
        admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{target.id}",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200

        r2 = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {target_token}"}
        )
        assert r2.status_code == 401

    async def test_cannot_deactivate_self(self, client, admin_user, org, db):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{admin_user.id}",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 400

    async def test_update_writes_audit_log(self, client, admin_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="auditpatch@test.com", password="AP1!", full_name="Original"
        )
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{target.id}",
            json={"full_name": "Updated"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.user.update",
                AuditLog.resource_id == target.id,
            )
        )
        audit = result.scalar_one()
        assert audit.before_data["full_name"] == "Original"
        assert audit.after_data["full_name"] == "Updated"
        assert "password_hash" not in (audit.before_data or {})
        assert "password_hash" not in (audit.after_data or {})

    async def test_update_user_not_in_org_returns_404(self, client, admin_user, org, db):
        other_org = Organisation(name="Other", is_active=True)
        db.add(other_org)
        await db.flush()
        outsider = User(
            email="out@test.com", full_name="Out",
            password_hash=hash_password("Out1!"), is_active=True,
        )
        db.add(outsider)
        await db.flush()
        db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
        await db.commit()

        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.patch(
            f"/api/admin/users/{outsider.id}",
            json={"full_name": "Hacked"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    async def test_empty_body_is_noop(self, client, admin_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="noop@test.com", password="Noop1!", full_name="Noop"
        )
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{target.id}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert r.json()["full_name"] == "Noop"

    async def test_update_requires_admin(self, client, packer_user, org, db):
        target = await _create_user_in_org(db, org=org, email="target403@test.com", password="T1!")
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)

        r = await client.patch(
            f"/api/admin/users/{target.id}",
            json={"full_name": "X"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_update_requires_auth(self, client, db):
        r = await client.patch("/api/admin/users/1", json={"full_name": "X"})
        assert r.status_code == 401


class TestAssignRole:
    async def test_assign_role_success(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="assignrole@test.com", password="A1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/roles",
            json={"role": "packer"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        role_names = [ro["role"] for ro in r.json()["roles"]]
        assert "packer" in role_names

    async def test_assign_role_duplicate_returns_409(self, client, admin_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="dup_role@test.com", password="D1!", roles=[UserRoleEnum.packer]
        )
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/roles",
            json={"role": "packer"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 409

    async def test_assign_role_writes_audit_log(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="audit_r@test.com", password="AR1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/roles",
            json={"role": "inward_operator"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.user_role.assign",
                AuditLog.organisation_id == org.id,
            )
        )
        audit = result.scalar_one()
        assert audit.after_data["user_id"] == target.id
        assert audit.after_data["role"] == "inward_operator"

    async def test_assign_role_user_not_in_org_returns_404(self, client, admin_user, org, db):
        other_org = Organisation(name="Role Iso", is_active=True)
        db.add(other_org)
        await db.flush()
        outsider = User(
            email="roleout@test.com", full_name="Out",
            password_hash=hash_password("Out1!"), is_active=True,
        )
        db.add(outsider)
        await db.flush()
        db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
        await db.commit()

        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.post(
            f"/api/admin/users/{outsider.id}/roles",
            json={"role": "packer"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    async def test_assign_role_requires_admin(self, client, packer_user, org, db):
        target = await _create_user_in_org(db, org=org, email="target_r@test.com", password="T1!")
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/roles",
            json={"role": "packer"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_assign_role_requires_auth(self, client, db):
        r = await client.post("/api/admin/users/1/roles", json={"role": "packer"})
        assert r.status_code == 401


class TestRevokeRole:
    async def test_revoke_role_success(self, client, admin_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="revoke_r@test.com", password="R1!", roles=[UserRoleEnum.packer]
        )
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.delete(
            f"/api/admin/users/{target.id}/roles/packer",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        role_names = [ro["role"] for ro in r.json()["roles"]]
        assert "packer" not in role_names

    async def test_revoke_role_not_assigned_returns_404(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="no_role@test.com", password="N1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.delete(
            f"/api/admin/users/{target.id}/roles/packer",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 404

    async def test_cannot_revoke_own_admin_role(self, client, admin_user, org, db):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.delete(
            f"/api/admin/users/{admin_user.id}/roles/admin",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 400

    async def test_revoke_role_writes_audit_log(self, client, admin_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="audit_rev@test.com", password="AV1!", roles=[UserRoleEnum.packer]
        )
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.delete(
            f"/api/admin/users/{target.id}/roles/packer",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.user_role.revoke",
                AuditLog.organisation_id == org.id,
            )
        )
        audit = result.scalar_one()
        assert audit.before_data["user_id"] == target.id
        assert audit.before_data["role"] == "packer"
        assert audit.after_data is None

    async def test_revoke_role_user_not_in_org_returns_404(self, client, admin_user, org, db):
        other_org = Organisation(name="Rev Iso", is_active=True)
        db.add(other_org)
        await db.flush()
        outsider = User(
            email="revokeout@test.com", full_name="Out",
            password_hash=hash_password("Out1!"), is_active=True,
        )
        db.add(outsider)
        await db.flush()
        db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
        db.add(UserRole(user_id=outsider.id, organisation_id=other_org.id, role=UserRoleEnum.packer, assigned_by=outsider.id))
        await db.commit()

        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.delete(
            f"/api/admin/users/{outsider.id}/roles/packer",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    async def test_revoke_role_requires_admin(self, client, packer_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="target_rev@test.com", password="T1!", roles=[UserRoleEnum.packer]
        )
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)

        r = await client.delete(
            f"/api/admin/users/{target.id}/roles/packer",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_revoke_role_requires_auth(self, client):
        r = await client.delete("/api/admin/users/1/roles/packer")
        assert r.status_code == 401


class TestPasswordReset:
    async def test_password_reset_success(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="pwreset@test.com", password="Old1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/password-reset",
            json={"new_password": "NewPass2!"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert "password" in r.json()["message"].lower() or "reset" in r.json()["message"].lower()

    async def test_new_password_allows_login(self, client, admin_user, org, db):
        target = await _create_user_in_org(
            db, org=org, email="newpwlogin@test.com", password="Old1!", roles=[UserRoleEnum.packer]
        )
        admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        await client.post(
            f"/api/admin/users/{target.id}/password-reset",
            json={"new_password": "BrandNew2!"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        login_r = await client.post(
            "/api/auth/login",
            json={"email": "newpwlogin@test.com", "password": "BrandNew2!", "organisation_id": org.id},
        )
        assert login_r.status_code == 200

    async def test_password_reset_audit_log_excludes_hash(self, client, admin_user, org, db):
        target = await _create_user_in_org(db, org=org, email="audit_pw@test.com", password="AP1!")
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/password-reset",
            json={"new_password": "AuditNew2!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.action == "admin.user.password_reset",
                AuditLog.resource_id == target.id,
            )
        )
        audit = result.scalar_one()
        assert "password_hash" not in (audit.after_data or {})
        assert "password" not in (audit.after_data or {})
        assert "new_password" not in (audit.after_data or {})

    async def test_password_reset_user_not_in_org_returns_404(self, client, admin_user, org, db):
        other_org = Organisation(name="PwReset Iso", is_active=True)
        db.add(other_org)
        await db.flush()
        outsider = User(
            email="pwout@test.com", full_name="Out",
            password_hash=hash_password("Out1!"), is_active=True,
        )
        db.add(outsider)
        await db.flush()
        db.add(UserOrganisation(user_id=outsider.id, organisation_id=other_org.id, created_by=outsider.id))
        await db.commit()

        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.post(
            f"/api/admin/users/{outsider.id}/password-reset",
            json={"new_password": "Hacked1!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    async def test_password_reset_requires_admin(self, client, packer_user, org, db):
        target = await _create_user_in_org(db, org=org, email="target_pw@test.com", password="T1!")
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)

        r = await client.post(
            f"/api/admin/users/{target.id}/password-reset",
            json={"new_password": "NotAllowed1!"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    async def test_password_reset_requires_auth(self, client):
        r = await client.post("/api/admin/users/1/password-reset", json={"new_password": "NoAuth1!"})
        assert r.status_code == 401
