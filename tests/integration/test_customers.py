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
