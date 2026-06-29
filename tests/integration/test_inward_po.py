import io
import csv as _csv

import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import CustomerStatusEnum
from app.models.customer import Customer


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_csv(**rows_kwargs) -> bytes:
    """Build a minimal valid CSV from a list of row dicts."""
    rows = rows_kwargs.get("rows", [
        {"po_number": "PO-001", "ean": "1234567890123", "description": "Widget A", "ordered_qty": "10"},
    ])
    buf = io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


async def _login(client: AsyncClient, email: str, password: str, org_id: int) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organisation_id": org_id},
    )
    return resp.json()["access_token"]


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def customer(db, org) -> Customer:
    c = Customer(
        organisation_id=org.id,
        name="Test Customer",
        code="TST",
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_upload_po_success(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    rows = [
        {"po_number": "PO-001", "ean": "1234567890123", "description": "Widget A", "ordered_qty": "10"},
        {"po_number": "PO-001", "ean": "9876543210987", "description": "Widget B", "ordered_qty": "5"},
    ]
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-001"},
        files={"file": ("po.csv", _make_csv(rows=rows), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["po_number"] == "PO-001"
    assert len(body["lines"]) == 2
    assert body["status"] == "open"


async def test_upload_po_missing_column(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    # CSV missing ordered_qty column
    rows = [{"po_number": "PO-002", "ean": "1234567890123"}]
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-002"},
        files={"file": ("po.csv", _make_csv(rows=rows), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Upload failed: missing required column(s): ordered_qty"


async def test_upload_po_duplicate_returns_409(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    rows = [{"po_number": "PO-DUP", "ean": "1111111111111", "ordered_qty": "3"}]
    csv_bytes = _make_csv(rows=rows)

    # First upload succeeds
    await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-DUP"},
        files={"file": ("po.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Second upload of same PO number → 409
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-DUP"},
        files={"file": ("po.csv", csv_bytes, "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "An inward already exists for this PO/Invoice. Continue adding boxes to it?"
    )


async def test_upload_po_packer_forbidden(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-PACKER"},
        files={"file": ("po.csv", _make_csv(), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_upload_po_unauthenticated(client, org, customer):
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-UNAUTH"},
        files={"file": ("po.csv", _make_csv(), "text/csv")},
    )
    assert resp.status_code == 401


async def test_upload_po_cross_tenant_customer_returns_404(
    client, inward_operator_user, org, db
):
    """customer_id from a different org must be rejected (IDOR guard)."""
    from app.models.organisation import Organisation

    other_org = Organisation(name="Other Org", is_active=True)
    db.add(other_org)
    await db.flush()
    other_customer = Customer(
        organisation_id=other_org.id,
        name="Other Customer",
        code="OTH",
        status=CustomerStatusEnum.active,
    )
    db.add(other_customer)
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(other_customer.id), "po_number": "PO-IDOR"},
        files={"file": ("po.csv", _make_csv(), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_upload_po_mixed_po_numbers_returns_400(
    client, inward_operator_user, org, customer
):
    """CSV containing rows for a different PO number must be rejected."""
    rows = [
        {"po_number": "PO-A", "ean": "1111111111111", "ordered_qty": "2"},
        {"po_number": "PO-B", "ean": "2222222222222", "ordered_qty": "3"},
    ]
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/inward/pos",
        data={"customer_id": str(customer.id), "po_number": "PO-A"},
        files={"file": ("po.csv", _make_csv(rows=rows), "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "PO-B" in resp.json()["detail"]
