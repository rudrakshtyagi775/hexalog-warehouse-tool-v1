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

async def test_list_open_pos_visible_to_packer(client, packer_user, inward_operator_user, org, customer):
    inward_token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    await client.post(
        "/api/outward/pos",
        data={"po_number": "OUT-LIST-001", "customer_id": customer.id},
        files={"file": ("po.csv", _make_csv("OUT-LIST-001"), "text/csv")},
        headers={"Authorization": f"Bearer {inward_token}"},
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


async def test_list_open_pos_date_filter_excludes_out_of_range(
    client, inward_operator_user, org, customer, db
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

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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
    client, inward_operator_user, org, customer
):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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


async def test_toggle_po_status_requires_inward_operator(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.patch(
        "/api/outward/pos/1/status",
        json={"status": "closed"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── preview_po ────────────────────────────────────────────────────────────────

async def test_preview_po_valid_csv(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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
    assert body["problems"] == []
    assert body["first_10_rows"][0]["po_number"] == "OUT-PREVIEW-001"


async def test_preview_po_duplicate_ean_reports_consolidation_notice(
    client, inward_operator_user, org, customer
):
    """OUT-2/OUT-3: duplicate EANs must be flagged as a notice, not an error —
    the upload is still valid."""
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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


async def test_preview_po_missing_columns_reports_problem_not_error(
    client, inward_operator_user, org, customer
):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
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
