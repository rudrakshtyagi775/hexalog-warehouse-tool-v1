import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum, OutwardBoxStatusEnum, OutwardPoStatusEnum
from app.models.outward import OutwardBox, OutwardPO, OutwardPOLine


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
async def po_with_line(db, org, customer) -> tuple[OutwardPO, OutwardPOLine]:
    po = OutwardPO(
        organisation_id=org.id,
        customer_id=customer.id,
        po_number="OUT-SCAN-001",
        status=OutwardPoStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    line = OutwardPOLine(
        outward_po_id=po.id,
        organisation_id=org.id,
        ean="1234567890123",
        description="Widget",
        ordered_qty=5,
        packed_qty=0,
    )
    db.add(line)
    await db.flush()
    return po, line


@pytest_asyncio.fixture
async def open_box(db, org, customer) -> OutwardBox:
    box = OutwardBox(
        box_id="OB-TST-099001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.open,
    )
    db.add(box)
    await db.flush()
    return box


# ── Add scan: success ─────────────────────────────────────────────────────────

async def test_add_outward_scan_success(client, packer_user, org, open_box, po_with_line, db):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ean"] == "1234567890123"
    assert body["scan_result"] == "accepted"

    # Box should transition to in_use
    await db.refresh(open_box)
    assert open_box.status == OutwardBoxStatusEnum.in_use


async def test_add_outward_scan_ean_not_found(client, packer_user, org, open_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "0000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "EAN not found in open outward POs"


async def test_add_outward_scan_all_full(client, packer_user, org, open_box, db, customer):
    po = OutwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="OUT-FULL-001", status=OutwardPoStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    line = OutwardPOLine(
        outward_po_id=po.id, organisation_id=org.id,
        ean="8888888888888", ordered_qty=1, packed_qty=1,  # already full
    )
    db.add(line)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "8888888888888"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Quantity complete for all open outward POs"


async def test_delete_outward_scan_success(client, packer_user, org, open_box, po_with_line):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    # Create a scan first
    create_resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201
    scan_id = create_resp.json()["id"]

    # Delete it
    del_resp = await client.delete(
        f"/api/outward/scans/{scan_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["scan_result"] == "deleted"


async def test_delete_outward_scan_already_deleted(
    client, packer_user, org, open_box, po_with_line
):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    scan_id = resp.json()["id"]
    await client.delete(
        f"/api/outward/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"}
    )

    resp2 = await client.delete(
        f"/api/outward/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code == 400
    assert resp2.json()["detail"] == "Scan already deleted"


async def test_add_scan_to_closed_box(client, packer_user, org, db, customer):
    closed_box = OutwardBox(
        box_id="OB-TST-099002",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.closed,
    )
    db.add(closed_box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{closed_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot add scans to a closed box"
