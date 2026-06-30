import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import (
    CustomerStatusEnum,
    InwardBoxStatusEnum,
    InwardReferenceStatusEnum,
)
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
async def po_with_line(db, org, customer) -> tuple[InwardPO, InwardPOLine]:
    po = InwardPO(
        organisation_id=org.id,
        customer_id=customer.id,
        po_number="PO-SCAN-001",
        status=InwardReferenceStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    line = InwardPOLine(
        inward_po_id=po.id,
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
async def scanning_box(db, org, customer) -> InwardBox:
    box = InwardBox(
        box_id="B-TST-099001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.scanning,
        scanned_qty=0,
    )
    db.add(box)
    await db.flush()
    return box


# ── Add scan: success ─────────────────────────────────────────────────────────

async def test_add_scan_success(client, packer_user, org, scanning_box, po_with_line):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ean"] == "1234567890123"
    assert body["is_deleted"] is False


async def test_add_scan_increments_scanned_qty(client, packer_user, org, scanning_box,
                                                po_with_line, db):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await db.refresh(scanning_box)
    assert scanning_box.scanned_qty == 1


async def test_add_scan_increments_packed_qty_on_po_line(
    client, packer_user, org, scanning_box, po_with_line, db
):
    _, line = po_with_line
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await db.refresh(line)
    assert line.packed_qty == 1


async def test_add_scan_fifo_allocates_oldest_po_first(
    client, packer_user, org, scanning_box, db, customer
):
    # Two POs with the same EAN; older one should be allocated first
    import asyncio
    po_old = InwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="PO-OLD", status=InwardReferenceStatusEnum.open,
    )
    db.add(po_old)
    await db.flush()
    line_old = InwardPOLine(
        inward_po_id=po_old.id, organisation_id=org.id,
        ean="9999999999999", ordered_qty=1, packed_qty=0,
    )
    db.add(line_old)
    await db.flush()

    po_new = InwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="PO-NEW", status=InwardReferenceStatusEnum.open,
    )
    db.add(po_new)
    await db.flush()
    line_new = InwardPOLine(
        inward_po_id=po_new.id, organisation_id=org.id,
        ean="9999999999999", ordered_qty=1, packed_qty=0,
    )
    db.add(line_new)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "9999999999999"},
        headers={"Authorization": f"Bearer {token}"},
    )

    await db.refresh(line_old)
    await db.refresh(line_new)
    assert line_old.packed_qty == 1  # oldest allocated first
    assert line_new.packed_qty == 0


# ── Add scan: errors ──────────────────────────────────────────────────────────

async def test_add_scan_ean_not_found(client, packer_user, org, scanning_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "0000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "EAN not found in open POs"


async def test_add_scan_all_lines_full(client, packer_user, org, scanning_box, db, customer):
    po = InwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="PO-FULL", status=InwardReferenceStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    line = InwardPOLine(
        inward_po_id=po.id, organisation_id=org.id,
        ean="8888888888888", ordered_qty=1, packed_qty=1,  # already full
    )
    db.add(line)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "8888888888888"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Quantity complete for all open POs"


async def test_add_scan_no_inward_stock_note(client, packer_user, org, scanning_box, db, customer):
    # EAN exists in PO but no prior inward_submission ledger entries
    po = InwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="PO-NOTE", status=InwardReferenceStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    db.add(InwardPOLine(
        inward_po_id=po.id, organisation_id=org.id,
        ean="7777777777777", ordered_qty=5, packed_qty=0,
    ))
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "7777777777777"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["note"] == "Note: no recorded inward stock for this item."


# ── Delete scan ───────────────────────────────────────────────────────────────

async def test_delete_scan_success(client, packer_user, org, scanning_box, po_with_line, db):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    # Create a scan first
    create_resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    scan_id = create_resp.json()["id"]

    # Delete it
    del_resp = await client.delete(
        f"/api/inward/scans/{scan_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["is_deleted"] is True

    # scanned_qty should be back to 0
    await db.refresh(scanning_box)
    assert scanning_box.scanned_qty == 0


async def test_delete_scan_decrements_packed_qty(
    client, packer_user, org, scanning_box, po_with_line, db
):
    _, line = po_with_line
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    scan_id = resp.json()["id"]

    await client.delete(
        f"/api/inward/scans/{scan_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    await db.refresh(line)
    assert line.packed_qty == 0


async def test_delete_already_deleted_scan(client, packer_user, org, scanning_box,
                                            po_with_line, db):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{scanning_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    scan_id = resp.json()["id"]
    await client.delete(f"/api/inward/scans/{scan_id}",
                        headers={"Authorization": f"Bearer {token}"})

    resp2 = await client.delete(f"/api/inward/scans/{scan_id}",
                                headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 400
