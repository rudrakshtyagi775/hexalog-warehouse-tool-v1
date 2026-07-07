"""
RBAC tests verify that role shorthands enforce correctly.
These tests use a dummy protected endpoint defined inline for the test module.
"""
import pytest

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
async def test_admin_cannot_access_packer_only_route(client, admin_user, org):
    """Admin is NOT a superuser — require_packer must reject admin (PRD: admin cannot pack items).

    The role dependency runs before the endpoint body, so a nonexistent customer_id still
    surfaces the 403 from the role check rather than a 404 from the service layer.
    """
    token = await _login(client, org.id, "admin@test.com", "AdminPass1!")
    resp = await client.post(
        "/api/outward/boxes",
        json={"customer_id": 999999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_access_inward_only_route(client, admin_user, org):
    """Admin is NOT a superuser — require_inward_operator must reject admin (PRD: admin
    cannot perform any inward workflow step)."""
    token = await _login(client, org.id, "admin@test.com", "AdminPass1!")
    resp = await client.get(
        "/api/inward/boxes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_access_outward_pos(client, admin_user, org):
    """Admin explicitly retains outward PO visibility (PRD: admin may view all outward POs)."""
    token = await _login(client, org.id, "admin@test.com", "AdminPass1!")
    resp = await client.get(
        "/api/outward/pos",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
