import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum, InwardBoxStatusEnum, InwardReferenceStatusEnum
from app.models.inward import InwardBox, InwardPO, InwardPOLine


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
async def open_box(db, org, customer) -> InwardBox:
    box = InwardBox(
        box_id="B-TST-000099",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.scanning,
        scanned_qty=0,
    )
    db.add(box)
    await db.flush()
    return box


@pytest_asyncio.fixture
async def closed_box(db, org, customer) -> InwardBox:
    box = InwardBox(
        box_id="B-TST-000098",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=3,
        physical_qty=3,
    )
    db.add(box)
    await db.flush()
    return box


# ── Create box ────────────────────────────────────────────────────────────────

async def test_create_box_success(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["box_id"].startswith("B-TST-")
    assert len(body["box_id"].split("-")[-1]) == 6  # 6-digit suffix
    assert body["status"] == "scanning"
    assert body["scanned_qty"] == 0
    assert body["is_read_only"] is False


async def test_create_box_generates_sequential_ids(client, packer_user, admin_user, org, customer):
    # Admin can create two boxes without the packer guard
    admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    r1 = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r2 = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    n1 = int(r1.json()["box_id"].split("-")[-1])
    n2 = int(r2.json()["box_id"].split("-")[-1])
    assert n2 == n1 + 1


async def test_packer_cannot_create_second_active_box(client, packer_user, org, customer, open_box,
                                                       db):
    # Assign open_box to this packer so the guard fires
    open_box.created_by = packer_user.id
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Mark the current box full before starting another box."


async def test_create_box_unauthenticated(client, customer, org):
    resp = await client.post("/api/inward/boxes", json={"customer_id": customer.id})
    assert resp.status_code == 401


# ── Get box ───────────────────────────────────────────────────────────────────

async def test_get_box_success(client, packer_user, org, open_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        f"/api/inward/boxes/{open_box.box_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["box_id"] == open_box.box_id
    assert body["is_read_only"] is False
    assert body["scans"] == []


async def test_get_completed_box_is_read_only(client, packer_user, org, db, customer):
    box = InwardBox(
        box_id="B-TST-000001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.completed,
        scanned_qty=2,
        inscan_number="INS-TST-20260628-0001",
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        f"/api/inward/boxes/{box.box_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_read_only"] is True


async def test_get_box_not_found(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/inward/boxes/B-XXX-999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── Close box ─────────────────────────────────────────────────────────────────

async def test_close_box_success(client, packer_user, org, db, open_box):
    # Set scanned_qty to match physical_qty we'll provide
    open_box.scanned_qty = 5
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/close",
        json={"physical_qty": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_verification"


async def test_close_box_qty_mismatch(client, packer_user, org, open_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/close",
        json={"physical_qty": 99},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "Scanned Quantity and Physical Quantity do not match. Please verify before submission."
    )


async def test_close_box_wrong_status(client, packer_user, org, closed_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/close",
        json={"physical_qty": closed_box.physical_qty},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "scanning" in resp.json()["detail"].lower()
