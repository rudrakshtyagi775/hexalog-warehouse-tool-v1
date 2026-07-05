import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum, OutwardBoxStatusEnum
from app.models.outward import OutwardBox


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


# ── generate_labels ──────────────────────────────────────────────────────────

async def test_generate_labels_creates_n_boxes(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/labels/generate",
        json={"customer_id": customer.id, "count": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["box_ids"]) == 3
    assert len(set(body["box_ids"])) == 3
    assert all(b.startswith("OB-TST-") for b in body["box_ids"])


async def test_generate_labels_requires_packer(client, inward_operator_user, org, customer):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        "/api/outward/labels/generate",
        json={"customer_id": customer.id, "count": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_generate_labels_count_out_of_range_rejected(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/labels/generate",
        json={"customer_id": customer.id, "count": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ── labels/pdf ────────────────────────────────────────────────────────────────

async def test_download_labels_pdf_unknown_box_404(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/outward/labels/pdf",
        params={"box_ids": ["OB-XXX-999999"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_download_labels_pdf_known_box_renders_or_reports_unavailable(
    client, packer_user, org, customer
):
    """WeasyPrint needs native Pango/GObject libs. On a host without them the
    service degrades to 503 rather than crashing; on a fully provisioned host
    it renders a real PDF. Both are acceptable outcomes for this test."""
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    gen_resp = await client.post(
        "/api/outward/labels/generate",
        json={"customer_id": customer.id, "count": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    box_id = gen_resp.json()["box_ids"][0]

    resp = await client.get(
        "/api/outward/labels/pdf",
        params={"box_ids": [box_id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert resp.headers["content-type"] == "application/pdf"
        assert len(resp.content) > 0


# ── labels/history ───────────────────────────────────────────────────────────

async def test_label_history_lists_own_generated_boxes(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    await client.post(
        "/api/outward/labels/generate",
        json={"customer_id": customer.id, "count": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get(
        "/api/outward/labels/history", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["print_count"] == 1 for item in body["items"])


# ── reprint ───────────────────────────────────────────────────────────────────

async def test_reprint_unknown_box_404(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/boxes/OB-XXX-999999/reprint",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_reprint_increments_print_count(client, packer_user, org, customer, db):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    gen_resp = await client.post(
        "/api/outward/labels/generate",
        json={"customer_id": customer.id, "count": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    box_id = gen_resp.json()["box_ids"][0]

    await client.post(
        f"/api/outward/boxes/{box_id}/reprint",
        headers={"Authorization": f"Bearer {token}"},
    )

    from sqlalchemy import select
    result = await db.execute(select(OutwardBox).where(OutwardBox.box_id == box_id))
    box = result.scalar_one()
    assert box.print_count == 2  # 1 from generate + 1 from reprint


# ── packing-history ──────────────────────────────────────────────────────────

async def test_packing_history_scoped_to_own_boxes(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    create_resp = await client.post(
        "/api/outward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    box_id = create_resp.json()["box_id"]

    resp = await client.get(
        "/api/outward/packing-history", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["box_id"] == box_id
    assert body["items"][0]["scan_count"] == 0


async def test_packing_history_excludes_other_packers_boxes(
    client, packer_user, admin_user, org, customer, db
):
    other_box = OutwardBox(
        box_id="OB-TST-000077",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.open,
        created_by=admin_user.id,
    )
    db.add(other_box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/outward/packing-history", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
