import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserOrganisation, UserRole
from app.models.enums import UserRoleEnum


async def _login(client: AsyncClient, email: str, password: str, org_id: int) -> str:
    r = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


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
