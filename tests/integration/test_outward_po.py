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
    return (
        f"po_number,ean,ordered_qty,description\n"
        f"{po_number},{ean},{qty},Widget\n"
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


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_upload_outward_po_success(client, admin_user, org, customer):
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-PO-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PO-001"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["po_number"] == "OUT-PO-001"
    assert body["status"] == "open"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["ordered_qty"] == 10
    assert body["lines"][0]["packed_qty"] == 0


def _make_xlsx(po_number: str, ean: str = "1234567890123", qty: int = 10) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["po_number", "ean", "ordered_qty", "description"])
    ws.append([po_number, ean, qty, "Widget"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_upload_outward_po_xlsx_success(client, admin_user, org, customer):
    """OUT-1: Outward PO upload accepts XLSX as well as CSV."""
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-XLSX-001", "customer_id": customer.id},
        files={
            "file": (
                "po.xlsx",
                _make_xlsx("OUT-XLSX-001"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["po_number"] == "OUT-XLSX-001"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["ordered_qty"] == 10


async def test_upload_outward_po_xlsx_missing_columns(client, admin_user, org, customer):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["po_number", "description"])
    ws.append(["OUT-XLSX-BAD", "Widget"])
    buf = BytesIO()
    wb.save(buf)

    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-XLSX-BAD", "customer_id": customer.id},
        files={
            "file": (
                "po.xlsx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "missing required column" in resp.json()["detail"].lower()


async def test_upload_outward_po_consolidates_duplicate_ean(
    client, admin_user, org, customer
):
    """OUT-3: duplicate EAN rows within one CSV are summed into a single line
    rather than violating the (outward_po_id, ean) unique constraint."""
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    csv_bytes = (
        b"po_number,ean,ordered_qty,description\n"
        b"OUT-DUP-EAN-001,1234567890123,5,Widget\n"
        b"OUT-DUP-EAN-001,1234567890123,7,Widget\n"
    )
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-DUP-EAN-001", "customer_id": customer.id},
        files={"file": ("po.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["lines"]) == 1
    assert body["lines"][0]["ordered_qty"] == 12


async def test_upload_outward_po_duplicate(client, admin_user, org, customer):
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    csv_bytes = _make_csv("OUT-DUP-001")
    # First upload succeeds
    await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-DUP-001", "customer_id": customer.id},
        files={"file": ("po.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Second upload is a duplicate
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-DUP-001", "customer_id": customer.id},
        files={"file": ("po.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "An outward PO already exists for this PO/Invoice number."


async def test_upload_outward_po_missing_columns(client, admin_user, org, customer):
    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    bad_csv = b"po_number,description\nOUT-BAD-001,Widget\n"
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-BAD-001", "customer_id": customer.id},
        files={"file": ("po.csv", bad_csv, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "missing required column" in resp.json()["detail"].lower()
    assert "ean" in resp.json()["detail"]
    assert "ordered_qty" in resp.json()["detail"]


async def test_upload_outward_po_requires_admin_packer_denied(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-PERM-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PERM-001"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_upload_outward_po_requires_admin_inward_operator_denied(
    client, inward_operator_user, org, customer
):
    """RBAC: PRD §4.2 gives Inward Operator 'No' on Outward PO management —
    only Admin may upload."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-PERM-002", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PERM-002"), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_upload_outward_po_requires_auth(client, customer):
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-AUTH-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-AUTH-001"), "text/csv")},
    )
    assert resp.status_code == 401
