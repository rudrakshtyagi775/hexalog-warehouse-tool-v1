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
async def open_box(db, org, customer, packer_user) -> OutwardBox:
    box = OutwardBox(
        box_id="OB-TST-099001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.open,
        created_by=packer_user.id,
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
    from app.models.enums import OutwardScanResultEnum
    from app.models.outward import OutwardScan
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


# ── OUT-10: one active box per packer ────────────────────────────────────────

async def test_scan_into_second_box_blocked_while_first_in_use(
    client, packer_user, org, customer, po_with_line, db
):
    """Once a packer's box transitions to in_use via a scan, scanning into a
    different box for the same packer must be rejected with the PRD message —
    not the raw IntegrityError from the DB's one-in_use-box-per-packer index."""
    _, line = po_with_line
    line2 = OutwardPOLine(
        outward_po_id=line.outward_po_id,
        organisation_id=org.id,
        ean="9999999999999",
        ordered_qty=5,
        packed_qty=0,
    )
    db.add(line2)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    box1 = (await client.post(
        "/api/outward/boxes", json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )).json()
    box2 = (await client.post(
        "/api/outward/boxes", json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    first_scan = await client.post(
        f"/api/outward/boxes/{box1['box_id']}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first_scan.status_code == 201

    second_scan = await client.post(
        f"/api/outward/boxes/{box2['box_id']}/scans",
        json={"ean": "9999999999999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second_scan.status_code == 400
    assert second_scan.json()["detail"] == (
        "Mark the current box full before starting another box."
    )


async def test_continuing_scans_into_own_in_use_box_allowed(
    client, packer_user, org, customer, po_with_line
):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    box = (await client.post(
        "/api/outward/boxes", json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )).json()

    first = await client.post(
        f"/api/outward/boxes/{box['box_id']}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/outward/boxes/{box['box_id']}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 201


# ── R-3 / Audit Trail: rejected scans must be persisted and audited ─────────

async def test_rejected_scan_ean_not_found_is_persisted_and_audited(
    client, packer_user, org, open_box, db
):
    from sqlalchemy import select

    from app.models.audit_log import AuditLog
    from app.models.enums import OutwardScanResultEnum
    from app.models.outward import OutwardScan

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "0000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "EAN not found in open outward POs"

    scan_result = await db.execute(
        select(OutwardScan).where(
            OutwardScan.outward_box_id == open_box.id, OutwardScan.ean == "0000000000000"
        )
    )
    scan = scan_result.scalar_one()
    assert scan.scan_result == OutwardScanResultEnum.rejected
    assert scan.reject_reason == "EAN not found in open outward POs"
    assert scan.outward_po_line_id is None

    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.action == "outward_scan_rejected")
    )
    audit_row = audit_result.scalar_one()
    assert audit_row.resource_id == scan.id
    assert audit_row.after_data["reject_reason"] == "EAN not found in open outward POs"


async def test_rejected_scan_quantity_complete_is_persisted_and_audited(
    client, packer_user, org, open_box, db, customer
):
    from sqlalchemy import select

    from app.models.enums import OutwardScanResultEnum
    from app.models.outward import OutwardScan

    po = OutwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="OUT-FULL-002", status=OutwardPoStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    line = OutwardPOLine(
        outward_po_id=po.id, organisation_id=org.id,
        ean="7777777777777", ordered_qty=1, packed_qty=1,  # already full
    )
    db.add(line)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "7777777777777"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Quantity complete for all open outward POs"

    scan_result = await db.execute(
        select(OutwardScan).where(
            OutwardScan.outward_box_id == open_box.id, OutwardScan.ean == "7777777777777"
        )
    )
    scan = scan_result.scalar_one()
    assert scan.scan_result == OutwardScanResultEnum.rejected
    assert scan.reject_reason == "Quantity complete for all open outward POs"


async def test_rejected_scan_appears_in_item_packing_report(
    client, admin_user, packer_user, org, open_box
):
    from datetime import date, timedelta

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "0000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )

    admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    today = date.today()
    resp = await client.get(
        "/api/admin/reports/item-packing",
        params={"from_date": str(today - timedelta(days=1)), "to_date": str(today)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    rejected_rows = [r for r in body["items"] if r["scan_result"] == "rejected"]
    assert len(rejected_rows) == 1
    assert rejected_rows[0]["ean"] == "0000000000000"
    assert rejected_rows[0]["allocated_po"] is None


# ── OUT-14: rejected scans cannot be deleted ──────────────────────────────────
# Regression test: rejected scans were not persisted before this session, so this
# path was unreachable. Persisting them (for R-3/audit) made delete_scan's missing
# "already rejected" guard live — DELETE would silently soft-delete a rejected scan
# and write a bogus +1 ledger reversal for stock that was never removed.

async def test_cannot_delete_rejected_scan(client, packer_user, org, open_box, db):
    from sqlalchemy import select

    from app.models.enums import OutwardScanResultEnum
    from app.models.inward import InventoryLedgerEntry
    from app.models.outward import OutwardScan

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    scan_resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "0000000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert scan_resp.status_code == 400  # rejected: EAN not found in open outward POs

    rejected_scan_result = await db.execute(
        select(OutwardScan).where(
            OutwardScan.outward_box_id == open_box.id, OutwardScan.ean == "0000000000000"
        )
    )
    rejected_scan = rejected_scan_result.scalar_one()

    del_resp = await client.delete(
        f"/api/outward/scans/{rejected_scan.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 400
    assert del_resp.json()["detail"] == "Cannot delete a rejected scan"

    # Confirm nothing changed: still rejected, no ledger reversal was written
    await db.refresh(rejected_scan)
    assert rejected_scan.scan_result == OutwardScanResultEnum.rejected
    ledger_result = await db.execute(
        select(InventoryLedgerEntry).where(InventoryLedgerEntry.source_id == rejected_scan.id)
    )
    assert ledger_result.scalar_one_or_none() is None


async def test_delete_still_works_for_accepted_and_already_deleted_scans(
    client, packer_user, org, open_box, po_with_line
):
    """Confirm the OUT-14 fix didn't touch the pre-existing accepted/deleted paths."""
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    scan_resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    scan_id = scan_resp.json()["id"]

    first_delete = await client.delete(
        f"/api/outward/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert first_delete.status_code == 200
    assert first_delete.json()["scan_result"] == "deleted"

    second_delete = await client.delete(
        f"/api/outward/scans/{scan_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert second_delete.status_code == 400
    assert second_delete.json()["detail"] == "Scan already deleted"


# ── RBAC: delete_scan is restricted to the box's own creator ─────────────────

async def test_other_packer_cannot_delete_scan_from_someone_elses_box(
    client, packer_user, org, open_box, po_with_line, db
):
    """PRD: packer may delete accepted scans from their own active box only."""
    from app.models.enums import UserRoleEnum
    from app.models.user import User, UserOrganisation, UserRole
    from app.services.password_service import hash_password

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    scan_resp = await client.post(
        f"/api/outward/boxes/{open_box.box_id}/scans",
        json={"ean": "1234567890123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    scan_id = scan_resp.json()["id"]

    other_packer = User(
        email="packer2@test.com",
        password_hash=hash_password("Packer2Pass1!"),
        full_name="Other Packer",
        is_active=True,
    )
    db.add(other_packer)
    await db.flush()
    db.add(UserOrganisation(user_id=other_packer.id, organisation_id=org.id))
    db.add(UserRole(user_id=other_packer.id, organisation_id=org.id, role=UserRoleEnum.packer))
    await db.flush()

    other_token = await _login(client, "packer2@test.com", "Packer2Pass1!", org.id)
    resp = await client.delete(
        f"/api/outward/scans/{scan_id}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "You can only delete scans from your own active box."
