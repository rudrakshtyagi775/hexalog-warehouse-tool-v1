import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum


async def _login(client: AsyncClient, email: str, password: str, org_id: int) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    return resp.json()["access_token"]


def _make_csv(po_number: str, ean: str = "1234567890123", qty: int = 10) -> bytes:
    return f"po_number,ean,ordered_qty,description\n{po_number},{ean},{qty},Widget\n".encode()


def _make_duplicate_ean_csv(po_number: str) -> bytes:
    return (
        "po_number,ean,ordered_qty,description\n"
        f"{po_number},1234567890123,5,Widget A\n"
        f"{po_number},1234567890123,7,Widget A\n"
        f"{po_number},9999999999999,3,Widget B\n"
    ).encode()


@pytest_asyncio.fixture
async def customer(db, org) -> Customer:
    c = Customer(
        organisation_id=org.id, name="Test Customer", code="TST",
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


# ── list_open_pos ────────────────────────────────────────────────────────────

async def test_list_open_pos_visible_to_packer(client, packer_user, admin_user, org, customer):
    admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-LIST-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-LIST-001"), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    packer_token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/outward/pos", headers={"Authorization": f"Bearer {packer_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["po_number"] == "OUT-LIST-001"
    assert item["customer_name"] == "Test Customer"
    assert item["total_ordered"] == 10
    assert item["progress_pct"] == 0.0


async def test_list_open_pos_requires_auth(client, org):
    resp = await client.get("/api/outward/pos")
    assert resp.status_code == 401


async def test_list_open_pos_inward_operator_denied(client, inward_operator_user, org):
    """RBAC: PRD §4.2 gives Inward Operator 'No' on viewing Outward POs."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.get("/api/outward/pos", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_packer_sees_only_open_pos_admin_sees_all(
    client, packer_user, admin_user, org, customer
):
    """PRD: packer is restricted to open outward POs; admin sees every status."""
    admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    open_create = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-STATUS-OPEN", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-STATUS-OPEN"), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    closed_create = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-STATUS-CLOSED", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-STATUS-CLOSED"), "text/csv")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    closed_po_id = closed_create.json()["id"]
    await client.patch(
        f"/api/outward/pos/{closed_po_id}/status",
        json={"status": "closed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert open_create.status_code == 201
    assert closed_create.status_code == 201

    packer_token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    packer_resp = await client.get(
        "/api/outward/pos", headers={"Authorization": f"Bearer {packer_token}"}
    )
    packer_numbers = {item["po_number"] for item in packer_resp.json()["items"]}
    assert packer_numbers == {"OUT-STATUS-OPEN"}

    admin_resp = await client.get(
        "/api/outward/pos", headers={"Authorization": f"Bearer {admin_token}"}
    )
    admin_numbers = {item["po_number"] for item in admin_resp.json()["items"]}
    assert admin_numbers == {"OUT-STATUS-OPEN", "OUT-STATUS-CLOSED"}


async def test_list_open_pos_date_filter_excludes_out_of_range(
    client, packer_user, org, customer, db
):
    """OUT-5: Open POs tab must support filtering by date."""
    from datetime import UTC, datetime, timedelta

    from app.models.enums import OutwardPoStatusEnum
    from app.models.outward import OutwardPO

    old_po = OutwardPO(
        organisation_id=org.id,
        customer_id=customer.id,
        po_number="OUT-OLD-001",
        status=OutwardPoStatusEnum.open,
        uploaded_at=datetime.now(UTC) - timedelta(days=30),
    )
    db.add(old_po)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    today = datetime.now(UTC).date()
    resp = await client.get(
        "/api/outward/pos",
        params={"from_date": str(today - timedelta(days=1)), "to_date": str(today)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    resp_all = await client.get(
        "/api/outward/pos",
        params={"from_date": str(today - timedelta(days=60)), "to_date": str(today)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_all.json()["total"] == 1


# ── toggle_po_status ─────────────────────────────────────────────────────────

async def test_toggle_po_status_close_then_reject_same_status(
    client, admin_user, org, customer
):
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    create_resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-TOGGLE-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-TOGGLE-001"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    po_id = create_resp.json()["id"]

    close_resp = await client.patch(
        f"/api/outward/pos/{po_id}/status",
        json={"status": "closed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert close_resp.status_code == 200
    assert close_resp.json()["status"] == "closed"

    # Closing an already-closed PO is rejected
    again_resp = await client.patch(
        f"/api/outward/pos/{po_id}/status",
        json={"status": "closed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert again_resp.status_code == 409


async def test_toggle_po_status_requires_admin_packer_denied(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.patch(
        "/api/outward/pos/1/status",
        json={"status": "closed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_toggle_po_status_requires_admin_inward_operator_denied(
    client, inward_operator_user, org
):
    """RBAC: PRD §4.2 gives Inward Operator 'No' on toggling Outward PO status."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.patch(
        "/api/outward/pos/1/status",
        json={"status": "closed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── preview_po ────────────────────────────────────────────────────────────────

async def test_preview_po_valid_csv(client, admin_user, org, customer):
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PREVIEW-001"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is True
    assert body["total_rows"] == 1
    assert body["total_quantity"] == 10
    assert body["problems"] == []
    assert body["first_10_rows"][0]["po_number"] == "OUT-PREVIEW-001"


async def test_preview_po_total_quantity_sums_all_rows(client, admin_user, org, customer):
    """PRD 7.2: preview must show total quantity across the whole file."""
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    csv_bytes = (
        b"po_number,ean,ordered_qty,description\n"
        b"OUT-TOTALQTY-001,1111111111111,4,Widget A\n"
        b"OUT-TOTALQTY-001,2222222222222,7,Widget B\n"
    )
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={"file": ("po.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 2
    assert body["total_quantity"] == 11


async def test_preview_po_duplicate_ean_reports_consolidation_notice(
    client, admin_user, org, customer
):
    """OUT-2/OUT-3: duplicate EANs must be flagged as a notice, not an error —
    the upload is still valid."""
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={"file": ("po.csv", _make_duplicate_ean_csv("OUT-DUPEAN-001"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is True
    assert body["problems"] == []
    assert body["consolidation_notice"] is not None
    assert body["total_rows"] == 3


async def test_preview_po_xlsx_valid(client, admin_user, org, customer):
    """OUT-1/OUT-2: preview also accepts XLSX uploads."""
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["po_number", "ean", "ordered_qty", "description"])
    ws.append(["OUT-XLSX-PREVIEW", "1234567890123", 10, "Widget"])
    buf = BytesIO()
    wb.save(buf)

    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={
            "file": (
                "po.xlsx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is True
    assert body["total_rows"] == 1
    assert body["first_10_rows"][0]["po_number"] == "OUT-XLSX-PREVIEW"
    assert body["first_10_rows"][0]["ordered_qty"] == 10


async def test_preview_po_missing_columns_reports_problem_not_error(
    client, admin_user, org, customer
):
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    bad_csv = b"po_number,description\nOUT-BAD-001,Widget\n"
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={"file": ("po.csv", bad_csv, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is False
    assert any("missing required column" in p.lower() for p in body["problems"])


async def test_preview_po_requires_admin_inward_operator_denied(
    client, inward_operator_user, org, customer
):
    """RBAC: PRD §4.2 gives Inward Operator 'No' on Outward PO management."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PREVIEW-DENY"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_preview_po_requires_admin_packer_denied(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos/preview",
        data={"customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PREVIEW-DENY2"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
