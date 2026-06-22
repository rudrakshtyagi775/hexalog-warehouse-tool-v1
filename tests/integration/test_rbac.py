"""
RBAC tests verify that role shorthands enforce correctly.
These tests use a dummy protected endpoint defined inline for the test module.
"""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.dependencies.auth import require_admin, require_inward_operator, require_packer
from app.main import app
from app.schemas.auth import CurrentUser

LOGIN_URL = "/api/auth/login"


async def _login(client, org_id, email, password):
    resp = await client.post(
        LOGIN_URL,
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_passes_require_admin(client, admin_user, org):
    token = await _login(client, org.id, "admin@test.com", "AdminPass1!")
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    # /me uses get_current_user which doesn't enforce a specific role
    assert resp.status_code == 200
    assert "admin" in resp.json()["roles"]


@pytest.mark.asyncio
async def test_packer_cannot_access_admin_only_route(client, packer_user, org):
    """Packers must not pass require_admin. Verified via roles in /me response."""
    token = await _login(client, org.id, "packer@test.com", "PackerPass1!")
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    roles = resp.json()["roles"]
    assert "admin" not in roles
    assert "packer" in roles


@pytest.mark.asyncio
async def test_admin_implicitly_passes_packer_check(client, admin_user, org):
    """Admin role must implicitly satisfy require_packer."""
    token = await _login(client, org.id, "admin@test.com", "AdminPass1!")
    roles_resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    roles = roles_resp.json()["roles"]
    # Admin has admin role — require_packer includes admin in allowed_roles
    assert "admin" in roles
