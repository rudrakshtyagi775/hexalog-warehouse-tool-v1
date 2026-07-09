from httpx import AsyncClient


async def _login(client: AsyncClient, email: str, password: str, org_id: int) -> str:
    r = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


class TestStats:
    async def test_get_stats_success(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.get("/api/admin/stats", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert "inward_boxes_completed_this_month" in data
        assert "total_items_scanned" in data
        assert "total_pos_uploaded" in data
        assert "active_customers" in data

    async def test_get_stats_requires_admin(self, client, packer_user, org):
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
        r = await client.get("/api/admin/stats", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403


class TestAuditLogs:
    async def test_list_audit_logs_success(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.get("/api/admin/audit-logs", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data

    async def test_list_audit_logs_requires_admin(self, client, packer_user, org):
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
        r = await client.get("/api/admin/audit-logs", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_list_audit_logs_requires_auth(self, client):
        r = await client.get("/api/admin/audit-logs")
        assert r.status_code == 401


class TestRecentSubmissions:
    async def test_get_recent_submissions_success(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.get(
            "/api/admin/recent-submissions", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert isinstance(data["items"], list)

    async def test_get_recent_submissions_requires_admin(self, client, packer_user, org):
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
        r = await client.get(
            "/api/admin/recent-submissions", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403


class TestGetMyOrg:
    async def test_get_my_org_success(self, client, admin_user, org):
        token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
        r = await client.get(
            "/api/admin/organisations/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Test Org"
        assert data["is_active"] is True

    async def test_get_my_org_requires_admin(self, client, packer_user, org):
        token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
        r = await client.get(
            "/api/admin/organisations/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403
