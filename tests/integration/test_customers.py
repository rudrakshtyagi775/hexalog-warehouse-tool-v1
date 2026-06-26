import pytest
import pytest_asyncio

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum
from app.models.organisation import Organisation


BASE = "/api/customers"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def admin_token(client, admin_user, org):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "AdminPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def packer_token(client, packer_user, org):
    resp = await client.post(
        "/api/auth/login",
        json={"email": "packer@test.com", "password": "PackerPass1!", "organisation_id": org.id},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def active_customer(db, org, admin_user):
    c = Customer(
        name="Kiran Enterprises",
        code="KIR",
        organisation_id=org.id,
        created_by=admin_user.id,
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


@pytest_asyncio.fixture
async def inactive_customer(db, org, admin_user):
    c = Customer(
        name="Inactive Corp",
        code="INC",
        organisation_id=org.id,
        created_by=admin_user.id,
        status=CustomerStatusEnum.inactive,
    )
    db.add(c)
    await db.flush()
    return c


@pytest_asyncio.fixture
async def other_org(db):
    o = Organisation(name="Other Org", is_active=True)
    db.add(o)
    await db.flush()
    return o


@pytest_asyncio.fixture
async def other_org_customer(db, other_org):
    c = Customer(
        name="Foreign Customer",
        code="FOR",
        organisation_id=other_org.id,
        created_by=None,
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


# ── List tests ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_customers_returns_own_org_only(
    client, admin_token, active_customer, other_org_customer
):
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert active_customer.id in ids
    assert other_org_customer.id not in ids


@pytest.mark.asyncio
async def test_list_customers_includes_inactive_by_default(
    client, admin_token, active_customer, inactive_customer
):
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert active_customer.id in ids
    assert inactive_customer.id in ids


@pytest.mark.asyncio
async def test_list_customers_filter_active_only(
    client, admin_token, active_customer, inactive_customer
):
    resp = await client.get(
        f"{BASE}?status=active", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert active_customer.id in ids
    assert inactive_customer.id not in ids


@pytest.mark.asyncio
async def test_list_customers_filter_inactive_only(
    client, admin_token, active_customer, inactive_customer
):
    resp = await client.get(
        f"{BASE}?status=inactive", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert inactive_customer.id in ids
    assert active_customer.id not in ids


@pytest.mark.asyncio
async def test_list_customers_name_search(client, admin_token, active_customer):
    resp = await client.get(
        f"{BASE}?q=kiran", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert any(c["id"] == active_customer.id for c in resp.json())


@pytest.mark.asyncio
async def test_list_customers_name_search_no_match(client, admin_token, active_customer):
    resp = await client.get(
        f"{BASE}?q=zzznomatch", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_customers_packer_allowed(client, packer_token, active_customer):
    """All authenticated roles can list customers (needed for dropdowns)."""
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {packer_token}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_customers_unauthenticated(client):
    resp = await client.get(BASE)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_customers_sorted_by_name(client, admin_token, db, org, admin_user):
    for code, name in [("BBB", "Zara Corp"), ("AAA", "Alpha Inc")]:
        db.add(Customer(
            name=name, code=code, organisation_id=org.id,
            created_by=admin_user.id, status=CustomerStatusEnum.active,
        ))
    await db.flush()
    resp = await client.get(BASE, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert names == sorted(names)


# ── Get by ID tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_customer_by_id(client, admin_token, active_customer):
    resp = await client.get(
        f"{BASE}/{active_customer.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == active_customer.id
    assert data["code"] == "KIR"
    assert data["name"] == "Kiran Enterprises"
    assert data["status"] == "active"
    assert "organisation_id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_get_customer_not_found(client, admin_token):
    resp = await client.get(
        f"{BASE}/999999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_customer_wrong_org_returns_404(client, admin_token, other_org_customer):
    """Cross-org lookup must 404 — never reveal another org's data."""
    resp = await client.get(
        f"{BASE}/{other_org_customer.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_customer_packer_allowed(client, packer_token, active_customer):
    resp = await client.get(
        f"{BASE}/{active_customer.id}",
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_customer_unauthenticated(client, active_customer):
    resp = await client.get(f"{BASE}/{active_customer.id}")
    assert resp.status_code == 401


# ── Create tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_customer_admin_success(client, admin_token, org):
    resp = await client.post(
        BASE,
        json={"name": "New Customer", "code": "NEW"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "New Customer"
    assert data["code"] == "NEW"
    assert data["status"] == "active"
    assert data["organisation_id"] == org.id
    assert data["id"] > 0
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_create_customer_packer_forbidden(client, packer_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "TST"},
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_customer_unauthenticated(client):
    resp = await client.post(BASE, json={"name": "Test", "code": "TST"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_customer_duplicate_code(client, admin_token, active_customer):
    resp = await client.post(
        BASE,
        json={"name": "Another Kiran", "code": "KIR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409
    assert "KIR" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_customer_invalid_code_lowercase(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "kir"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_code_too_short(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "K"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_code_too_long(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "Test", "code": "KIRN"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_empty_name(client, admin_token):
    resp = await client.post(
        BASE,
        json={"name": "", "code": "KIR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_customer_duplicate_code_other_org_ok(
    client, admin_token, other_org_customer
):
    """Same code in a different org is allowed — codes are unique per org."""
    resp = await client.post(
        BASE,
        json={"name": "Foreign Clone", "code": "FOR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_customer_audit_log_written(client, admin_token, db):
    from app.models.audit_log import AuditLog
    from sqlalchemy import select as sa_select
    resp = await client.post(
        BASE,
        json={"name": "Audit Test", "code": "AUD"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    customer_id = resp.json()["id"]
    result = await db.execute(
        sa_select(AuditLog).where(
            AuditLog.resource_type == "customer",
            AuditLog.resource_id == customer_id,
            AuditLog.action == "customer_created",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.after_data["code"] == "AUD"


# ── Update tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_customer_name(client, admin_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "Renamed Customer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Renamed Customer"
    assert data["code"] == "KIR"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_deactivate_customer(client, admin_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"status": "inactive"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_reactivate_customer(client, admin_token, inactive_customer):
    resp = await client.patch(
        f"{BASE}/{inactive_customer.id}",
        json={"status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_update_customer_both_fields(client, admin_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "New Name", "status": "inactive"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Name"
    assert data["status"] == "inactive"


@pytest.mark.asyncio
async def test_update_customer_code_rejected(client, admin_token, active_customer):
    """code is immutable — any attempt to patch it must return 422."""
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"code": "NEW"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_customer_packer_forbidden(client, packer_token, active_customer):
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "Hijack"},
        headers={"Authorization": f"Bearer {packer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_customer_unauthenticated(client, active_customer):
    resp = await client.patch(f"{BASE}/{active_customer.id}", json={"name": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_customer_not_found(client, admin_token):
    resp = await client.patch(
        f"{BASE}/999999",
        json={"name": "Ghost"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_customer_wrong_org_returns_404(
    client, admin_token, other_org_customer
):
    resp = await client.patch(
        f"{BASE}/{other_org_customer.id}",
        json={"name": "Steal"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_customer_no_op_returns_200(client, admin_token, active_customer):
    """Empty body (no fields to change) should return 200 unchanged."""
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "KIR"


@pytest.mark.asyncio
async def test_update_customer_audit_log_written(client, admin_token, active_customer, db):
    from app.models.audit_log import AuditLog
    from sqlalchemy import select as sa_select
    resp = await client.patch(
        f"{BASE}/{active_customer.id}",
        json={"name": "Audit Changed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    result = await db.execute(
        sa_select(AuditLog).where(
            AuditLog.resource_type == "customer",
            AuditLog.resource_id == active_customer.id,
            AuditLog.action == "customer_updated",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.before_data["name"] == "Kiran Enterprises"
    assert log.after_data["name"] == "Audit Changed"
