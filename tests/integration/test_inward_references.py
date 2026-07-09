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


@pytest_asyncio.fixture
async def customer(db, org) -> Customer:
    c = Customer(
        organisation_id=org.id, name="Test Customer", code="TST",
        status=CustomerStatusEnum.active,
    )
    db.add(c)
    await db.flush()
    return c


# ── create / get-or-create ───────────────────────────────────────────────────

async def test_create_reference_requires_po_or_invoice(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_create_reference_success(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-PO-001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["po_number"] == "REF-PO-001"
    assert body["status"] == "open"
    assert body["is_duplicate"] is False
    assert body["duplicate_message"] is None


async def test_create_reference_duplicate_never_blocks(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    first = await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-DUP-001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    second = await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-DUP-001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 201  # never blocks (IN-3)
    body = second.json()
    assert body["id"] == first.json()["id"]
    assert body["is_duplicate"] is True
    assert body["duplicate_message"] == (
        "An inward already exists for this PO/Invoice. Continue adding boxes to it?"
    )


async def test_list_references(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "invoice_number": "INV-001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get(
        "/api/inward/references", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["invoice_number"] == "INV-001"
    assert body["items"][0]["customer_name"] == "Test Customer"


# ── box_number uniqueness within a reference (IN-4) ──────────────────────────

async def test_box_number_duplicate_within_reference_rejected(
    client, inward_operator_user, org, customer
):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    ref_resp = await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-BOX-001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    reference_id = ref_resp.json()["id"]

    first_box = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id, "inward_reference_id": reference_id, "box_number": "1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first_box.status_code == 201
    assert first_box.json()["box_number"] == "1"
    assert first_box.json()["inward_reference_id"] == reference_id

    # Free up the single-active-box slot so the duplicate-box_number check (not
    # the unrelated "one active box" guard) is what rejects the second request.
    await client.post(
        f"/api/inward/boxes/{first_box.json()['box_id']}/close",
        json={"physical_qty": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    dup_box = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id, "inward_reference_id": reference_id, "box_number": "1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dup_box.status_code == 400
    assert dup_box.json()["detail"] == (
        "Box number already used for this PO/Invoice. Enter a different box number."
    )


async def test_box_number_same_number_different_reference_allowed(
    client, inward_operator_user, org, customer
):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    ref1 = (await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-A"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]
    ref2 = (await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-B"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    box1 = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id, "inward_reference_id": ref1, "box_number": "1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert box1.status_code == 201
    await client.post(
        f"/api/inward/boxes/{box1.json()['box_id']}/close",
        json={"physical_qty": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    box2 = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id, "inward_reference_id": ref2, "box_number": "1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert box2.status_code == 201


# ── finish delivery (IN-11) ───────────────────────────────────────────────────

async def test_finish_reference_blocks_new_boxes(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    reference_id = (await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-FINISH-001"},
        headers={"Authorization": f"Bearer {token}"},
    )).json()["id"]

    finish_resp = await client.post(
        f"/api/inward/references/{reference_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert finish_resp.status_code == 200
    assert finish_resp.json()["status"] == "completed"

    # Second finish is rejected
    again = await client.post(
        f"/api/inward/references/{reference_id}/finish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert again.status_code == 400

    box_resp = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id, "inward_reference_id": reference_id, "box_number": "1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert box_resp.status_code == 400
    assert "already finished" in box_resp.json()["detail"].lower()


async def test_create_reference_requires_auth(client, org, customer):
    resp = await client.post(
        "/api/inward/references",
        json={"customer_id": customer.id, "po_number": "REF-AUTH-001"},
    )
    assert resp.status_code == 401
