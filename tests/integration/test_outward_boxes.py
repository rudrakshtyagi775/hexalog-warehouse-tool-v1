import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum, OutwardBoxStatusEnum
from app.models.outward import OutwardBox


async def _login(client: AsyncClient, email: str, password: str, org_id: int) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def customer(db, org) -> Customer:
    c = Customer(
        organisation_id=org.id, name="Test Customer", code="TST",
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


@pytest_asyncio.fixture
async def open_outward_box(db, org, customer) -> OutwardBox:
    box = OutwardBox(
        box_id="OB-TST-000099",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.open,
    )
    db.add(box)
    await db.flush()
    return box


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_create_outward_box_success(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["box_id"].startswith("OB-TST-")
    assert len(body["box_id"].split("-")[-1]) == 6  # 6-digit suffix
    assert body["status"] == "open"
    assert body["is_read_only"] is False
    assert body["scans"] == []


async def test_get_outward_box_success(client, packer_user, org, open_outward_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        f"/api/outward/boxes/{open_outward_box.box_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["box_id"] == open_outward_box.box_id
    assert body["scans"] == []
    assert body["is_read_only"] is False


async def test_get_outward_box_not_found(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/outward/boxes/OB-XXX-999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_close_outward_box_success(client, packer_user, org, open_outward_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_outward_box.box_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["is_read_only"] is True


async def test_close_outward_box_already_closed(client, packer_user, org, db, customer):
    box = OutwardBox(
        box_id="OB-TST-000098",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.closed,
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{box.box_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "closed" in resp.json()["detail"].lower()
