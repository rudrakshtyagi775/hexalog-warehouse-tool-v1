import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.customer import Customer
from app.models.enums import (
    CustomerStatusEnum,
    InwardBoxStatusEnum,
    InwardCodeTypeEnum,
    LedgerSourceTypeEnum,
)
from app.models.inward import InventoryLedgerEntry, InwardBox, InwardScan


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

async def test_create_box_success(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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
    # Admin can create two boxes without the single-active-box guard
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


async def test_non_admin_cannot_create_second_active_box(
    client, inward_operator_user, org, customer, open_box, db
):
    # Assign open_box to this operator so the guard fires
    open_box.created_by = inward_operator_user.id
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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

async def test_close_box_success(client, inward_operator_user, org, db, open_box):
    # Set scanned_qty to match physical_qty we'll provide
    open_box.scanned_qty = 5
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/close",
        json={"physical_qty": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_verification"


async def test_close_box_qty_mismatch(client, inward_operator_user, org, open_box):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/close",
        json={"physical_qty": 99},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "Scanned Quantity and Physical Quantity do not match. Please verify before submission."
    )


async def test_close_box_wrong_status(client, inward_operator_user, org, closed_box):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/close",
        json={"physical_qty": closed_box.physical_qty},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "scanning" in resp.json()["detail"].lower()


# ── Submit box ────────────────────────────────────────────────────────────────

async def test_submit_box_success(client, inward_operator_user, org, closed_box):
    """pending_verification → completed; inscan_number is set; is_read_only becomes True."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["inscan_number"] is not None
    assert body["inscan_number"].startswith("INS-TST-")
    assert body["is_read_only"] is True


async def test_submit_box_inscan_number_format(client, inward_operator_user, org, closed_box):
    """Inscan number matches INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>."""
    import re
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    inscan = resp.json()["inscan_number"]
    assert re.match(r"^INS-[A-Z]+-\d{8}-\d{4}$", inscan), f"Bad format: {inscan}"


async def test_submit_box_wrong_status_scanning(client, inward_operator_user, org, open_box):
    """Box in 'scanning' status cannot be submitted — must close first."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "pending_verification" in resp.json()["detail"].lower()


async def test_submit_box_wrong_status_completed(client, inward_operator_user, org, db, customer):
    """Already-completed box cannot be submitted again."""
    box = InwardBox(
        box_id="B-TST-000001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.completed,
        scanned_qty=2,
        inscan_number="INS-TST-20260630-0001",
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "pending_verification" in resp.json()["detail"].lower()


async def test_submit_box_not_found(client, inward_operator_user, org):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/inward/boxes/B-XXX-999999/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_submit_box_requires_auth(client, closed_box):
    resp = await client.post(f"/api/inward/boxes/{closed_box.box_id}/submit")
    assert resp.status_code == 401


async def test_submit_box_writes_ledger_entries(client, inward_operator_user, org, db, customer):
    """One +1 InventoryLedgerEntry per active scan; deleted scans are skipped."""
    box = InwardBox(
        box_id="B-TST-000097",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=2,
        physical_qty=2,
    )
    db.add(box)
    await db.flush()

    scan_a = InwardScan(
        organisation_id=org.id,
        inward_box_id=box.id,
        ean="1111111111111",
        code_type=InwardCodeTypeEnum.ean,
        is_deleted=False,
    )
    scan_b = InwardScan(
        organisation_id=org.id,
        inward_box_id=box.id,
        ean="2222222222222",
        code_type=InwardCodeTypeEnum.ean,
        is_deleted=False,
    )
    scan_del = InwardScan(
        organisation_id=org.id,
        inward_box_id=box.id,
        ean="3333333333333",
        code_type=InwardCodeTypeEnum.ean,
        is_deleted=True,
    )
    db.add_all([scan_a, scan_b, scan_del])
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    entries = (
        await db.execute(
            select(InventoryLedgerEntry).where(
                InventoryLedgerEntry.organisation_id == org.id,
                InventoryLedgerEntry.source_type == LedgerSourceTypeEnum.inward_submission,
            )
        )
    ).scalars().all()

    assert len(entries) == 2
    assert {e.ean for e in entries} == {"1111111111111", "2222222222222"}
    assert all(e.quantity_change == 1 for e in entries)


async def test_submit_box_sequential_inscan_numbers(client, admin_user, org, db, customer):
    """Counter increments per customer per day; second submission gets next number."""
    box1 = InwardBox(
        box_id="B-TST-000095",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=0,
        physical_qty=0,
    )
    box2 = InwardBox(
        box_id="B-TST-000096",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=0,
        physical_qty=0,
    )
    db.add_all([box1, box2])
    await db.flush()

    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    r1 = await client.post(
        f"/api/inward/boxes/{box1.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    r2 = await client.post(
        f"/api/inward/boxes/{box2.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    n1 = int(r1.json()["inscan_number"].split("-")[-1])
    n2 = int(r2.json()["inscan_number"].split("-")[-1])
    assert n2 == n1 + 1
