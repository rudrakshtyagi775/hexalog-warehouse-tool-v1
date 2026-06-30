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


# ── FIFO ordering ─────────────────────────────────────────────────────────────

async def test_add_outward_scan_fifo_allocates_oldest_po_first(
    client, packer_user, org, open_box, db, customer
):
    # Two POs with the same EAN; older one should be allocated first
    po_old = OutwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="OUT-OLD-001", status=OutwardPoStatusEnum.open,
    )
    db.add(po_old)
    await db.flush()
    line_old = OutwardPOLine(
        outward_po_id=po_old.id, organisation_id=org.id,
        ean="9999999999999", ordered_qty=1, packed_qty=0,
    )
    db.add(line_old)
    await db.flush()

    po_new = OutwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="OUT-NEW-001", status=OutwardPoStatusEnum.open,
    )
    db.add(po_new)
    await db.flush()
    line_new = OutwardPOLine(
        outward_po_id=po_new.id, organisation_id=org.id,
        ean="9999999999999", ordered_qty=1, packed_qty=0,
    )
    db.add(line_new)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "9999999999999"},
        headers={"Authorization": f"Bearer {token}"},
    )

    await db.refresh(line_old)
    await db.refresh(line_new)
    assert line_old.packed_qty == 1  # oldest allocated first
    assert line_new.packed_qty == 0


# ── Ledger reversal on delete ─────────────────────────────────────────────────

async def test_delete_outward_scan_restores_ledger(
    client, packer_user, org, open_box, po_with_line, db
):
    from sqlalchemy import text as sa_text
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)

    # Add a scan (writes -1 ledger entry)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    scan_id = resp.json()["id"]

    # Delete the scan (writes +1 reversal)
    del_resp = await client.delete(
        f"/api/outward/scans/{scan_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200

    # Ledger balance should be 0 (net -1 + +1)
    result = await db.execute(
        sa_text(
            "SELECT COALESCE(SUM(quantity_change), 0) FROM inventory_ledger_entries "
            "WHERE organisation_id = :org AND ean = :ean"
        ),
        {"org": org.id, "ean": "1234567890123"},
    )
    assert result.scalar() == 0


# ── "No inward stock" note path ───────────────────────────────────────────────

async def test_add_outward_scan_no_inward_stock_note(
    client, packer_user, org, open_box, db, customer
):
    # EAN exists in outward PO but no prior inward ledger entries
    po = OutwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="OUT-NOTE-001", status=OutwardPoStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    db.add(OutwardPOLine(
        outward_po_id=po.id, organisation_id=org.id,
        ean="7777777777777", ordered_qty=5, packed_qty=0,
    ))
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "7777777777777"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["note"] == "Note: no recorded inward stock for this item."


# ── Cannot delete scan from closed box ───────────────────────────────────────

async def test_delete_scan_from_closed_box_fails(
    client, packer_user, org, po_with_line, db, customer
):
    from app.models.outward import OutwardScan
    from app.models.enums import OutwardScanResultEnum
    # Create a closed box with an accepted scan directly in DB
    closed_box = OutwardBox(
        box_id="OB-TST-099003",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.closed,
    )
    db.add(closed_box)
    await db.flush()

    _, line = po_with_line
    scan = OutwardScan(
        organisation_id=org.id,
        outward_box_id=closed_box.id,
        outward_po_line_id=line.id,
        ean="1234567890123",
        scan_result=OutwardScanResultEnum.accepted,
    )
    db.add(scan)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.delete(
        f"/api/outward/scans/{scan.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "closed" in resp.json()["detail"].lower()
