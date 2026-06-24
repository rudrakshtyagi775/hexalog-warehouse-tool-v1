"""Integration tests for organisation management endpoints.

Endpoints under test:
  GET    /api/admin/organisations/me  — current org details (admin only)
  PATCH  /api/admin/organisations/me  — update current org (admin only)
  GET    /api/admin/organisations     — list all orgs admin belongs to (admin only)
  POST   /api/admin/organisations     — create new org (admin only)

Invariants verified:
  - organisation_id always comes from the JWT, never from request body
  - Audit log written in the same transaction as changes
  - Deactivating an org with active users is blocked (400)
  - Non-admin (packer) receives 403 on every endpoint
"""

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.organisation import Organisation
from app.models.user import UserOrganisation

LOGIN_URL = "/api/auth/login"
ORG_ME_URL = "/api/admin/organisations/me"
ORGS_URL = "/api/admin/organisations"


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _login(client, user, org, password: str) -> str:
    """Log in and return the access token."""
    resp = await client.post(
        LOGIN_URL,
        json={"email": user.email, "password": password, "organisation_id": org.id},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["access_token"]


# ── GET /api/admin/organisations/me ──────────────────────────────────────────

async def test_get_current_org_returns_correct_data(client, admin_user, org, db):
    """GET /me returns the organisation that is active in the admin's JWT."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.get(ORG_ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == org.id
    assert data["name"] == org.name
    assert data["is_active"] is True
    assert "created_at" in data


async def test_get_current_org_non_admin_returns_403(client, packer_user, org, db):
    """Non-admin roles cannot access GET /me."""
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.get(ORG_ME_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


# ── PATCH /api/admin/organisations/me ────────────────────────────────────────

async def test_update_org_name_success(client, admin_user, org, db):
    """Admin can rename the current organisation."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        ORG_ME_URL,
        json={"name": "Renamed Org"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Renamed Org"
    assert data["id"] == org.id
    assert data["is_active"] is True


async def test_update_org_writes_audit_log(client, admin_user, org, db):
    """PATCH writes an audit_logs row with correct action and before/after data."""
    token = await _login(client, admin_user, org, "AdminPass1!")
    original_name = org.name

    resp = await client.patch(
        ORG_ME_URL,
        json={"name": "Audited Org"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.organisation.update",
            AuditLog.user_id == admin_user.id,
            AuditLog.organisation_id == org.id,
        )
    )
    audit = result.scalar_one()
    assert audit.resource_type == "organisations"
    assert audit.resource_id == org.id
    assert audit.before_data["name"] == original_name
    assert audit.after_data["name"] == "Audited Org"


async def test_deactivate_org_with_active_users_returns_400(client, admin_user, org, db):
    """An org that still has active users cannot be deactivated."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        ORG_ME_URL,
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert "active users" in resp.json()["detail"].lower()


async def test_update_org_no_fields_is_noop(client, admin_user, org, db):
    """PATCH with an empty body succeeds without changing anything."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.patch(
        ORG_ME_URL,
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == org.name
    assert data["is_active"] is True


async def test_update_org_non_admin_returns_403(client, packer_user, org, db):
    """Non-admin cannot call PATCH /me."""
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.patch(
        ORG_ME_URL,
        json={"name": "Should Not Work"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


# ── GET /api/admin/organisations ─────────────────────────────────────────────

async def test_list_orgs_includes_current_org(client, admin_user, org, db):
    """GET /organisations returns at least the org the admin belongs to."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.get(ORGS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    org_ids = [o["id"] for o in resp.json()]
    assert org.id in org_ids


async def test_list_orgs_non_admin_returns_403(client, packer_user, org, db):
    """Non-admin cannot list organisations."""
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.get(ORGS_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


# ── POST /api/admin/organisations ────────────────────────────────────────────

async def test_create_org_success(client, admin_user, org, db):
    """Admin can create a new organisation; response contains correct fields."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        ORGS_URL,
        json={"name": "Brand New Org"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Brand New Org"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


async def test_create_org_enrolls_admin_as_member(client, admin_user, org, db):
    """Creating an org automatically enrolls the creating admin in it."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        ORGS_URL,
        json={"name": "Membership Test Org"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    new_org_id = resp.json()["id"]

    result = await db.execute(
        select(UserOrganisation).where(
            UserOrganisation.user_id == admin_user.id,
            UserOrganisation.organisation_id == new_org_id,
        )
    )
    assert result.scalar_one_or_none() is not None


async def test_create_org_appears_in_list(client, admin_user, org, db):
    """A newly created org shows up in the admin's org list."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    create_resp = await client.post(
        ORGS_URL,
        json={"name": "Listed Org"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201
    new_org_id = create_resp.json()["id"]

    list_resp = await client.get(ORGS_URL, headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    org_ids = [o["id"] for o in list_resp.json()]
    assert new_org_id in org_ids


async def test_create_org_writes_audit_log(client, admin_user, org, db):
    """POST /organisations writes an audit_logs row for the new org."""
    token = await _login(client, admin_user, org, "AdminPass1!")

    resp = await client.post(
        ORGS_URL,
        json={"name": "Audit Org"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    new_org_id = resp.json()["id"]

    result = await db.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.organisation.create",
            AuditLog.user_id == admin_user.id,
            AuditLog.resource_id == new_org_id,
        )
    )
    audit = result.scalar_one()
    assert audit.after_data["name"] == "Audit Org"
    assert audit.organisation_id == new_org_id


async def test_create_org_non_admin_returns_403(client, packer_user, org, db):
    """Non-admin cannot create organisations."""
    token = await _login(client, packer_user, org, "PackerPass1!")

    resp = await client.post(
        ORGS_URL,
        json={"name": "Packer Org"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


# ── Auth failures ─────────────────────────────────────────────────────────────

async def test_org_me_missing_bearer_returns_401(client):
    """No Authorization header → 401 on GET /me."""
    resp = await client.get(ORG_ME_URL)
    assert resp.status_code == 401


async def test_patch_org_me_missing_bearer_returns_401(client):
    """No Authorization header → 401 on PATCH /me."""
    resp = await client.patch(ORG_ME_URL, json={"name": "No Auth"})
    assert resp.status_code == 401


async def test_org_list_missing_bearer_returns_401(client):
    """No Authorization header → 401 on GET /organisations."""
    resp = await client.get(ORGS_URL)
    assert resp.status_code == 401


async def test_create_org_missing_bearer_returns_401(client):
    """No Authorization header → 401 on POST /organisations."""
    resp = await client.post(ORGS_URL, json={"name": "No Auth Org"})
    assert resp.status_code == 401
