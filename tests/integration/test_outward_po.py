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

async def test_upload_outward_po_success(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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


async def test_upload_outward_po_duplicate(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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


async def test_upload_outward_po_missing_columns(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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


async def test_upload_outward_po_requires_inward_operator(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-PERM-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-PERM-001"), "text/csv")},
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
