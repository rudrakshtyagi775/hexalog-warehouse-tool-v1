# Phase 1: Inward Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the complete inward flow — PO upload via CSV, box creation with atomic Box ID generation, EAN scan ingestion with FIFO PO-line allocation, box closure with physical count validation, and atomic submission that generates an Inscan Number and writes inventory ledger entries.

**Architecture:** Six sequential tasks, each producing independently testable, committable work. All business logic lives in `app/services/inward_service.py`; HTTP wiring in `app/routers/inward.py`; I/O contracts in `app/schemas/inward.py`. A new `Counter` ORM model handles atomic ID generation via raw `INSERT ... ON CONFLICT DO UPDATE RETURNING` — no `SELECT FOR UPDATE`. FIFO scan allocation uses a conditional UPDATE (`packed_qty + 1 WHERE packed_qty < ordered_qty`) with up to 5 retries to prevent concurrent over-allocation without pessimistic locking. Every mutation writes an `AuditLog` row in the same transaction.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, `text()` for counter UPSERT, Pydantic v2, Alembic, `csv`/`io` stdlib, pytest-asyncio, httpx.

## Global Constraints

- `organisation_id` always comes from the JWT (`current_user.organisation_id`) — never from the request body.
- `inward_scans` and `inventory_ledger_entries` are append-only: no `UPDATE` or hard `DELETE` ever touches them.
- `inward_boxes.scanned_qty` must be incremented/decremented in the **same transaction** as the `inward_scans` INSERT or soft-delete.
- `inward_po_lines.packed_qty` must be incremented/decremented in the **same transaction** as the scan INSERT or soft-delete.
- Box submission is atomic: Inscan Number generation + `inward_boxes.status = completed` + `inventory_ledger_entries` writes all succeed or all roll back.
- Every query on an org-scoped table includes `WHERE organisation_id = :org_id`.
- Audit log (`AuditModuleEnum.inward`) written in the same transaction as the triggering event. `write_audit_log` never commits — the service owns the commit.
- Counter UPSERT uses `INSERT ... ON CONFLICT DO UPDATE RETURNING last_value` — never `SELECT FOR UPDATE`.
- Scan allocation uses conditional UPDATE with max 5 retries before raising.
- PRD error strings are **verbatim** — copy them exactly from the table below.
- Ruff: `line-length=100`, `target-version=py312`.

### PRD-Mandated Verbatim Error Strings

| Condition | Exact string |
|-----------|-------------|
| Duplicate PO | `An inward already exists for this PO/Invoice. Continue adding boxes to it?` |
| Missing CSV column(s) | `Upload failed: missing required column(s): {names}` where `{names}` is a comma-separated sorted list |
| Physical qty ≠ scanned qty | `Scanned Quantity and Physical Quantity do not match. Please verify before submission.` |
| EAN not in any open PO line | `EAN not found in open POs` |
| All PO lines for this EAN are full | `Quantity complete for all open POs` |
| Packer already has an active box | `Mark the current box full before starting another box.` |
| Box is in completed status (read GET) | `This box is closed. Showing details in read-only mode.` |
| No prior inward ledger stock (note on scan) | `Note: no recorded inward stock for this item.` |

---

## File Map

| Action | Path |
|--------|------|
| Modify | `app/models/enums.py` — add `inward_box` to `CounterTypeEnum`; add `inward_scan_deletion` to `LedgerSourceTypeEnum` |
| Create | `app/models/inward.py` — `Counter`, `InwardPO`, `InwardPOLine`, `InwardBox`, `InwardScan`, `InventoryLedgerEntry` |
| Modify | `app/models/__init__.py` — import and re-export inward models |
| Create | `alembic/versions/<hash>_add_inward_module_tables.py` — migration (autogenerate then patch) |
| Create | `app/schemas/inward.py` — all request/response schemas |
| Create | `app/services/inward_service.py` — all business logic |
| Create | `app/routers/inward.py` — all HTTP routes |
| Modify | `app/main.py` — register inward router |
| Modify | `tests/conftest.py` — add `inward_operator_user` and `customer` fixtures |
| Create | `tests/unit/test_inward_schemas.py` |
| Create | `tests/integration/test_inward_po.py` |
| Create | `tests/integration/test_inward_boxes.py` |
| Create | `tests/integration/test_inward_scans.py` |
| Create | `tests/integration/test_inward_submit.py` |

---

## Task 1: Enum Extensions + ORM Models + Migration

**Files:**
- Modify: `app/models/enums.py`
- Create: `app/models/inward.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/<hash>_add_inward_module_tables.py`

**Interfaces:**
- Produces: `Counter`, `InwardPO`, `InwardPOLine`, `InwardBox`, `InwardScan`, `InventoryLedgerEntry` ORM models — consumed by all subsequent tasks.

- [ ] **Step 1: Extend enums in `app/models/enums.py`**

Add one value to each of the two enums shown:

```python
class CounterTypeEnum(str, enum.Enum):
    outward_box = "outward_box"
    inscan_number = "inscan_number"
    inward_box = "inward_box"          # ← add this


class LedgerSourceTypeEnum(str, enum.Enum):
    inward_submission = "inward_submission"
    outward_scan = "outward_scan"
    outward_scan_deletion = "outward_scan_deletion"
    inward_scan_deletion = "inward_scan_deletion"  # ← add this
```

- [ ] **Step 2: Create `app/models/inward.py`**

```python
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    CounterTypeEnum,
    InwardBoxStatusEnum,
    InwardCodeTypeEnum,
    InwardReferenceStatusEnum,
    LedgerSourceTypeEnum,
)


class Counter(Base):
    """Atomic sequence generator. One row per (type, org, customer_code, date_key).

    date_key is "" for non-date counters (e.g. inward_box) and "YYYYMMDD" for
    inscan_number counters. Updated exclusively via INSERT ... ON CONFLICT DO UPDATE.
    """

    __tablename__ = "counters"
    __table_args__ = (
        UniqueConstraint(
            "counter_type", "organisation_id", "customer_code", "date_key",
            name="uq_counters_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    counter_type: Mapped[CounterTypeEnum] = mapped_column(
        PGEnum(CounterTypeEnum, name="counter_type_enum", create_type=False),
        nullable=False,
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_code: Mapped[str] = mapped_column(Text, nullable=False)
    date_key: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class InwardPO(TimestampMixin, Base):
    """One row per PO/invoice number per organisation."""

    __tablename__ = "inward_pos"
    __table_args__ = (
        UniqueConstraint("organisation_id", "po_number", name="uq_inward_pos_org_po"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[InwardReferenceStatusEnum] = mapped_column(
        PGEnum(InwardReferenceStatusEnum, name="inward_reference_status_enum", create_type=False),
        default=InwardReferenceStatusEnum.open,
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lines: Mapped[list["InwardPOLine"]] = relationship(
        back_populates="inward_po", lazy="raise"
    )


class InwardPOLine(Base):
    """One row per EAN per PO. packed_qty tracks FIFO allocation across all boxes."""

    __tablename__ = "inward_po_lines"
    __table_args__ = (
        Index("idx_inward_po_lines_ean_org", "organisation_id", "ean"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inward_po_id: Mapped[int] = mapped_column(
        ForeignKey("inward_pos.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordered_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    packed_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    inward_po: Mapped["InwardPO"] = relationship(back_populates="lines")


class InwardBox(TimestampMixin, Base):
    """One packing box. Box ID (B-CUSTCODE-000001) generated via Counter.

    Status flow: scanning → pending_verification (after close) → completed (after submit).
    scanned_qty invariant: must equal COUNT(*) of non-deleted inward_scans for this box.
    """

    __tablename__ = "inward_boxes"
    __table_args__ = (
        Index("idx_inward_boxes_org_status", "organisation_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    box_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[InwardBoxStatusEnum] = mapped_column(
        PGEnum(InwardBoxStatusEnum, name="inward_box_status_enum", create_type=False),
        default=InwardBoxStatusEnum.scanning,
        nullable=False,
    )
    physical_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scanned_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inscan_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    scans: Mapped[list["InwardScan"]] = relationship(
        back_populates="box",
        foreign_keys="[InwardScan.inward_box_id]",
        lazy="raise",
    )


class InwardScan(Base):
    """Append-only. Rows are never hard-deleted; is_deleted=True marks soft-deletion."""

    __tablename__ = "inward_scans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    inward_box_id: Mapped[int] = mapped_column(
        ForeignKey("inward_boxes.id", ondelete="RESTRICT"), nullable=False
    )
    inward_po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("inward_po_lines.id", ondelete="SET NULL"), nullable=True
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    code_type: Mapped[InwardCodeTypeEnum] = mapped_column(
        PGEnum(InwardCodeTypeEnum, name="inward_code_type_enum", create_type=False),
        default=InwardCodeTypeEnum.ean,
        nullable=False,
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    box: Mapped["InwardBox"] = relationship(
        back_populates="scans",
        foreign_keys="[InwardScan.inward_box_id]",
    )


class InventoryLedgerEntry(Base):
    """Append-only. One row per scan (inward_submission = +1) or per deletion reversal."""

    __tablename__ = "inventory_ledger_entries"
    __table_args__ = (
        Index("idx_ledger_org_ean", "organisation_id", "ean"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[LedgerSourceTypeEnum] = mapped_column(
        PGEnum(LedgerSourceTypeEnum, name="ledger_source_type_enum", create_type=False),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 3: Update `app/models/__init__.py`**

Add these lines after the `Customer` import and in the `__all__` list:

```python
from app.models.inward import (
    Counter,
    InwardPO,
    InwardPOLine,
    InwardBox,
    InwardScan,
    InventoryLedgerEntry,
)
```

And append to `__all__`:

```python
    "Counter",
    "InwardPO",
    "InwardPOLine",
    "InwardBox",
    "InwardScan",
    "InventoryLedgerEntry",
```

- [ ] **Step 4: Generate the Alembic migration**

Run:

```bash
alembic revision --autogenerate -m "add_inward_module_tables"
```

This creates `alembic/versions/<hash>_add_inward_module_tables.py`. Open it and add the two `ALTER TYPE` statements **at the very top of `upgrade()`**, before any `op.create_table` calls. Autogenerate does not detect enum value additions.

```python
def upgrade() -> None:
    # Extend existing PostgreSQL enums BEFORE creating tables that use them.
    # IF NOT EXISTS makes this idempotent on re-run.
    op.execute("ALTER TYPE counter_type_enum ADD VALUE IF NOT EXISTS 'inward_box'")
    op.execute(
        "ALTER TYPE ledger_source_type_enum ADD VALUE IF NOT EXISTS 'inward_scan_deletion'"
    )

    # --- autogenerated CREATE TABLE statements follow here (do not remove them) ---
    ...


def downgrade() -> None:
    # Enum values cannot be removed in PostgreSQL; only drop the tables.
    op.drop_table("inventory_ledger_entries")
    op.drop_table("inward_scans")
    op.drop_table("inward_boxes")
    op.drop_table("inward_po_lines")
    op.drop_table("inward_pos")
    op.drop_table("counters")
```

- [ ] **Step 5: Apply the migration**

```bash
alembic upgrade head
```

Expected: migration applies cleanly with no errors.

- [ ] **Step 6: Verify with a smoke test**

Run (no DB required — just import check):

```bash
python -c "from app.models.inward import Counter, InwardPO, InwardPOLine, InwardBox, InwardScan, InventoryLedgerEntry; print('OK')"
```

Expected output: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/models/enums.py app/models/inward.py app/models/__init__.py alembic/versions/
git commit -m "feat(inward): add ORM models and migration for inward module tables"
```

---

## Task 2: Pydantic Schemas + Unit Tests

**Files:**
- Create: `app/schemas/inward.py`
- Create: `tests/unit/test_inward_schemas.py`

**Interfaces:**
- Produces: `POLineCreate`, `BoxCreate`, `BoxClose`, `ScanCreate`, `POResponse`, `POLineResponse`, `BoxResponse`, `ScanResponse`, `ScanCreateResponse` — consumed by Tasks 3–6.

- [ ] **Step 1: Write failing unit tests first**

Create `tests/unit/test_inward_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from app.schemas.inward import BoxClose, BoxCreate, POLineCreate, ScanCreate
from app.models.enums import InwardCodeTypeEnum


# ── POLineCreate ──────────────────────────────────────────────────────────────

def test_po_line_valid():
    line = POLineCreate(ean="1234567890123", ordered_qty=10)
    assert line.ean == "1234567890123"
    assert line.description is None


def test_po_line_description_optional():
    line = POLineCreate(ean="ABC", ordered_qty=1, description="Widget")
    assert line.description == "Widget"


def test_po_line_zero_qty_rejected():
    with pytest.raises(ValidationError):
        POLineCreate(ean="1234567890123", ordered_qty=0)


def test_po_line_negative_qty_rejected():
    with pytest.raises(ValidationError):
        POLineCreate(ean="1234567890123", ordered_qty=-1)


def test_po_line_missing_ean_rejected():
    with pytest.raises(ValidationError):
        POLineCreate(ordered_qty=5)


# ── BoxCreate ─────────────────────────────────────────────────────────────────

def test_box_create_valid():
    b = BoxCreate(customer_id=42)
    assert b.customer_id == 42


def test_box_create_missing_customer_rejected():
    with pytest.raises(ValidationError):
        BoxCreate()


# ── BoxClose ──────────────────────────────────────────────────────────────────

def test_box_close_valid():
    bc = BoxClose(physical_qty=5)
    assert bc.physical_qty == 5


def test_box_close_zero_allowed():
    bc = BoxClose(physical_qty=0)
    assert bc.physical_qty == 0


def test_box_close_negative_rejected():
    with pytest.raises(ValidationError):
        BoxClose(physical_qty=-1)


# ── ScanCreate ────────────────────────────────────────────────────────────────

def test_scan_create_defaults_to_ean():
    s = ScanCreate(ean="1234567890123")
    assert s.code_type == InwardCodeTypeEnum.ean


def test_scan_create_style_code():
    s = ScanCreate(ean="STYLE-001", code_type=InwardCodeTypeEnum.style_code)
    assert s.code_type == InwardCodeTypeEnum.style_code


def test_scan_create_missing_ean_rejected():
    with pytest.raises(ValidationError):
        ScanCreate()
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
pytest tests/unit/test_inward_schemas.py -v
```

Expected: all tests fail with `ImportError` (module does not exist yet).

- [ ] **Step 3: Create `app/schemas/inward.py`**

```python
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import InwardBoxStatusEnum, InwardCodeTypeEnum, InwardReferenceStatusEnum


# ── Request schemas ───────────────────────────────────────────────────────────

class POLineCreate(BaseModel):
    ean: str
    ordered_qty: int = Field(gt=0)
    description: str | None = None


class BoxCreate(BaseModel):
    customer_id: int


class BoxClose(BaseModel):
    physical_qty: int = Field(ge=0)


class ScanCreate(BaseModel):
    ean: str
    code_type: InwardCodeTypeEnum = InwardCodeTypeEnum.ean


# ── Response schemas ──────────────────────────────────────────────────────────

class POLineResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    description: str | None
    ordered_qty: int
    packed_qty: int


class POResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    po_number: str
    customer_id: int
    status: InwardReferenceStatusEnum
    uploaded_at: datetime
    lines: list[POLineResponse]


class ScanResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    code_type: InwardCodeTypeEnum
    is_deleted: bool
    created_at: datetime


class BoxResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    box_id: str
    customer_id: int
    status: InwardBoxStatusEnum
    physical_qty: int | None
    scanned_qty: int
    inscan_number: str | None
    scans: list[ScanResponse]
    created_at: datetime
    is_read_only: bool  # True when status == completed


class ScanCreateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    code_type: InwardCodeTypeEnum
    is_deleted: bool
    created_at: datetime
    note: str | None = None
```

- [ ] **Step 4: Run unit tests to confirm they all pass**

```bash
pytest tests/unit/test_inward_schemas.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/inward.py tests/unit/test_inward_schemas.py
git commit -m "feat(inward): add inward module Pydantic schemas with unit tests"
```

---

## Task 3: PO Upload

**Files:**
- Create: `app/services/inward_service.py` (partial — `upload_po` only)
- Create: `app/routers/inward.py` (partial — `POST /api/inward/pos` only)
- Modify: `app/main.py` — register inward router
- Create: `tests/integration/test_inward_po.py`

**Interfaces:**
- Consumes: `InwardPO`, `InwardPOLine` (Task 1); `POResponse`, `POLineResponse` (Task 2).
- Produces: `upload_po(db, *, customer_id, organisation_id, uploaded_by, ip_address, po_number, lines) -> InwardPO` — consumed by Task 3 tests and referenced by later tasks.

- [ ] **Step 1: Write failing integration tests**

Create `tests/integration/test_inward_po.py`:

```python
import io
import csv as _csv

import pytest
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/integration/test_inward_po.py -v
```

Expected: all fail (router/service not yet implemented).

- [ ] **Step 3: Add `inward_operator_user` fixture to `tests/conftest.py`**

Add after the `inactive_user` fixture. Also add the needed imports at the top of conftest:

```python
# At top of tests/conftest.py, add to existing imports:
from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum
```

New fixture (add at the bottom of `tests/conftest.py`):

```python
@pytest_asyncio.fixture
async def inward_operator_user(db, org) -> User:
    u = User(
        email="inward@test.com",
        password_hash=hash_password("InwardPass1!"),
        full_name="Test Inward Operator",
        is_active=True,
    )
    db.add(u)
    await db.flush()
    db.add(UserOrganisation(user_id=u.id, organisation_id=org.id))
    db.add(UserRole(user_id=u.id, organisation_id=org.id, role=UserRoleEnum.inward_operator))
    await db.flush()
    return u
```

- [ ] **Step 4: Create `app/services/inward_service.py` with `upload_po`**

```python
import csv
import io
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AuditModuleEnum,
    CounterTypeEnum,
    InwardBoxStatusEnum,
    InwardCodeTypeEnum,
    InwardReferenceStatusEnum,
    LedgerSourceTypeEnum,
    UserRoleEnum,
)
from app.models.inward import (
    Counter,
    InventoryLedgerEntry,
    InwardBox,
    InwardPO,
    InwardPOLine,
    InwardScan,
)
from app.services.audit_service import write_audit_log

log = structlog.get_logger(__name__)

_PO_REQUIRED_COLUMNS = {"po_number", "ean", "ordered_qty"}


async def upload_po(
    db: AsyncSession,
    *,
    customer_id: int,
    organisation_id: int,
    uploaded_by: int,
    ip_address: str | None,
    po_number: str,
    csv_bytes: bytes,
) -> InwardPO:
    """Parse CSV, validate columns, detect duplicates, create InwardPO + lines atomically."""
    # 1. Parse CSV
    text_io = io.StringIO(csv_bytes.decode("utf-8-sig"))
    reader = csv.DictReader(text_io)
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV file is empty or has no header row")

    actual_columns = {c.strip().lower() for c in reader.fieldnames}
    missing = sorted(_PO_REQUIRED_COLUMNS - actual_columns)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Upload failed: missing required column(s): {', '.join(missing)}",
        )

    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file contains no data rows")

    # 2. Duplicate detection
    existing = await db.execute(
        select(InwardPO).where(
            InwardPO.organisation_id == organisation_id,
            InwardPO.po_number == po_number,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="An inward already exists for this PO/Invoice. Continue adding boxes to it?",
        )

    # 3. Build lines from CSV rows matching this po_number
    lines_data: list[dict] = []
    for row in rows:
        if row.get("po_number", "").strip() != po_number:
            continue
        try:
            ordered_qty = int(row["ordered_qty"])
        except (ValueError, KeyError):
            raise HTTPException(status_code=400, detail="ordered_qty must be a positive integer")
        if ordered_qty <= 0:
            raise HTTPException(status_code=400, detail="ordered_qty must be a positive integer")
        lines_data.append({
            "ean": row["ean"].strip(),
            "ordered_qty": ordered_qty,
            "description": (row.get("description") or "").strip() or None,
        })

    if not lines_data:
        raise HTTPException(
            status_code=400,
            detail=f"No rows found in CSV for po_number '{po_number}'",
        )

    # 4. Create InwardPO
    po = InwardPO(
        organisation_id=organisation_id,
        customer_id=customer_id,
        po_number=po_number,
        status=InwardReferenceStatusEnum.open,
        uploaded_by=uploaded_by,
    )
    db.add(po)
    await db.flush()

    # 5. Create InwardPOLine rows
    for ld in lines_data:
        db.add(InwardPOLine(
            inward_po_id=po.id,
            organisation_id=organisation_id,
            ean=ld["ean"],
            description=ld["description"],
            ordered_qty=ld["ordered_qty"],
            packed_qty=0,
        ))

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="po_uploaded",
        resource_type="inward_po",
        user_id=uploaded_by,
        organisation_id=organisation_id,
        after_data={"po_number": po_number, "customer_id": customer_id, "line_count": len(lines_data)},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(po)
    return po
```

- [ ] **Step 5: Create `app/routers/inward.py`** (initial — PO endpoint only)

```python
import structlog
from fastapi import APIRouter, Depends, Form, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import require_inward_operator, require_packer, get_current_user
from app.schemas.auth import CurrentUser
from app.schemas.inward import BoxClose, BoxCreate, POResponse, ScanCreate
from app.services import inward_service
from app.utils.request import get_client_ip

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/inward", tags=["inward"])


@router.post("/pos", response_model=POResponse, status_code=status.HTTP_201_CREATED)
async def upload_po_endpoint(
    request: Request,
    po_number: str = Form(...),
    customer_id: int = Form(...),
    file: UploadFile = ...,
    current_user: CurrentUser = Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> POResponse:
    csv_bytes = await file.read()
    po = await inward_service.upload_po(
        db,
        customer_id=customer_id,
        organisation_id=current_user.organisation_id,
        uploaded_by=current_user.user_id,
        ip_address=get_client_ip(request),
        po_number=po_number,
        csv_bytes=csv_bytes,
    )
    # Eagerly load lines for response serialisation
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    from app.models.inward import InwardPO
    result = await db.execute(
        select(InwardPO).options(selectinload(InwardPO.lines)).where(InwardPO.id == po.id)
    )
    po_with_lines = result.scalar_one()
    return po_with_lines
```

- [ ] **Step 6: Register the inward router in `app/main.py`**

Add import:
```python
from app.routers import inward as inward_router
```

Add `include_router` after the existing customer router line:
```python
app.include_router(inward_router.router)
```

- [ ] **Step 7: Run integration tests**

```bash
pytest tests/integration/test_inward_po.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 8: Run full unit test suite to check for regressions**

```bash
pytest tests/unit/ -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add app/services/inward_service.py app/routers/inward.py app/main.py \
        tests/conftest.py tests/integration/test_inward_po.py
git commit -m "feat(inward): add PO upload endpoint with duplicate detection and column validation"
```

---

## Task 4: Box Management — Create, Get, Close

**Files:**
- Modify: `app/services/inward_service.py` — add `_next_counter`, `create_box`, `get_box`, `close_box`
- Modify: `app/routers/inward.py` — add three box endpoints
- Create: `tests/integration/test_inward_boxes.py`

**Interfaces:**
- Consumes: `Counter`, `InwardBox` (Task 1); `BoxCreate`, `BoxClose`, `BoxResponse` (Task 2).
- Produces:
  - `_next_counter(db, *, counter_type, organisation_id, customer_code, date_key="") -> int`
  - `create_box(db, *, customer_id, organisation_id, created_by, user_roles, ip_address) -> InwardBox`
  - `get_box(db, *, box_id, organisation_id) -> InwardBox | None`
  - `close_box(db, *, box_id, organisation_id, physical_qty, closed_by, ip_address) -> InwardBox`

- [ ] **Step 1: Write failing integration tests**

Create `tests/integration/test_inward_boxes.py`:

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import CustomerStatusEnum, InwardBoxStatusEnum, InwardReferenceStatusEnum
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
async def open_box(db, org, customer) -> InwardBox:
    box = InwardBox(
        box_id="B-TST-000099",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.scanning,
        scanned_qty=0,
    )
    db.add(box)
    await db.flush()
    return box


@pytest_asyncio.fixture
async def closed_box(db, org, customer) -> InwardBox:
    box = InwardBox(
        box_id="B-TST-000098",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=3,
        physical_qty=3,
    )
    db.add(box)
    await db.flush()
    return box


# ── Create box ────────────────────────────────────────────────────────────────

async def test_create_box_success(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["box_id"].startswith("B-TST-")
    assert len(body["box_id"].split("-")[-1]) == 6  # 6-digit suffix
    assert body["status"] == "scanning"
    assert body["scanned_qty"] == 0
    assert body["is_read_only"] is False


async def test_create_box_generates_sequential_ids(client, packer_user, admin_user, org, customer):
    # Admin can create two boxes without the packer guard
    admin_token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    r1 = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r2 = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    n1 = int(r1.json()["box_id"].split("-")[-1])
    n2 = int(r2.json()["box_id"].split("-")[-1])
    assert n2 == n1 + 1


async def test_packer_cannot_create_second_active_box(client, packer_user, org, customer, open_box,
                                                       db):
    # Assign open_box to this packer so the guard fires
    open_box.created_by = packer_user.id
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/inward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Mark the current box full before starting another box."


async def test_create_box_unauthenticated(client, customer, org):
    resp = await client.post("/api/inward/boxes", json={"customer_id": customer.id})
    assert resp.status_code == 401


# ── Get box ───────────────────────────────────────────────────────────────────

async def test_get_box_success(client, packer_user, org, open_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        f"/api/inward/boxes/{open_box.box_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["box_id"] == open_box.box_id
    assert body["is_read_only"] is False
    assert body["scans"] == []


async def test_get_completed_box_is_read_only(client, packer_user, org, db, customer):
    box = InwardBox(
        box_id="B-TST-000001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.completed,
        scanned_qty=2,
        inscan_number="INS-TST-20260628-0001",
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        f"/api/inward/boxes/{box.box_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_read_only"] is True


async def test_get_box_not_found(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/inward/boxes/B-XXX-999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── Close box ─────────────────────────────────────────────────────────────────

async def test_close_box_success(client, packer_user, org, db, open_box):
    # Set scanned_qty to match physical_qty we'll provide
    open_box.scanned_qty = 5
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/close",
        json={"physical_qty": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_verification"


async def test_close_box_qty_mismatch(client, packer_user, org, open_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/close",
        json={"physical_qty": 99},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "Scanned Quantity and Physical Quantity do not match. Please verify before submission."
    )


async def test_close_box_wrong_status(client, packer_user, org, closed_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/close",
        json={"physical_qty": closed_box.physical_qty},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "scanning" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/integration/test_inward_boxes.py -v
```

Expected: all fail (service/router not yet implemented).

- [ ] **Step 3: Add `_next_counter`, `create_box`, `get_box`, `close_box` to `app/services/inward_service.py`**

Append to the existing service file:

```python
# ── Counter helper ────────────────────────────────────────────────────────────

async def _next_counter(
    db: AsyncSession,
    *,
    counter_type: CounterTypeEnum,
    organisation_id: int,
    customer_code: str,
    date_key: str = "",
) -> int:
    """Atomic increment. Returns the new last_value (starts at 1)."""
    result = await db.execute(
        text("""
            INSERT INTO counters (counter_type, organisation_id, customer_code, date_key, last_value)
            VALUES (:ct, :org, :code, :dk, 1)
            ON CONFLICT (counter_type, organisation_id, customer_code, date_key)
            DO UPDATE SET last_value = counters.last_value + 1
            RETURNING last_value
        """),
        {"ct": counter_type.value, "org": organisation_id, "code": customer_code, "dk": date_key},
    )
    return result.scalar_one()


# ── Box: create ───────────────────────────────────────────────────────────────

async def create_box(
    db: AsyncSession,
    *,
    customer_id: int,
    organisation_id: int,
    created_by: int,
    user_roles: list[UserRoleEnum],
    ip_address: str | None,
) -> InwardBox:
    from sqlalchemy.orm import selectinload

    # 1. Packer single-active-box guard (admin bypasses)
    is_packer_only = (
        UserRoleEnum.packer in user_roles and UserRoleEnum.admin not in user_roles
    )
    if is_packer_only:
        existing = await db.execute(
            select(InwardBox).where(
                InwardBox.organisation_id == organisation_id,
                InwardBox.created_by == created_by,
                InwardBox.status == InwardBoxStatusEnum.scanning,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mark the current box full before starting another box.",
            )

    # 2. Load customer to get the code for Box ID generation
    from app.models.customer import Customer
    cust_result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.organisation_id == organisation_id,
        )
    )
    customer = cust_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    # 3. Generate unique Box ID via atomic counter
    n = await _next_counter(
        db,
        counter_type=CounterTypeEnum.inward_box,
        organisation_id=organisation_id,
        customer_code=customer.code,
    )
    box_id = f"B-{customer.code}-{n:06d}"

    # 4. Persist
    box = InwardBox(
        box_id=box_id,
        organisation_id=organisation_id,
        customer_id=customer_id,
        status=InwardBoxStatusEnum.scanning,
        scanned_qty=0,
        created_by=created_by,
    )
    db.add(box)

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="box_created",
        resource_type="inward_box",
        user_id=created_by,
        organisation_id=organisation_id,
        after_data={"box_id": box_id, "customer_id": customer_id},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(box)

    # Reload with scans eager-loaded
    result = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(InwardBox.id == box.id)
    )
    return result.scalar_one()


# ── Box: get ──────────────────────────────────────────────────────────────────

async def get_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
) -> InwardBox | None:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


# ── Box: close ────────────────────────────────────────────────────────────────

async def close_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    physical_qty: int,
    closed_by: int,
    ip_address: str | None,
) -> InwardBox:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")

    if box.status != InwardBoxStatusEnum.scanning:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Box must be in 'scanning' status to close (current: {box.status.value})",
        )

    if box.scanned_qty != physical_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scanned Quantity and Physical Quantity do not match. Please verify before submission.",
        )

    box.status = InwardBoxStatusEnum.pending_verification
    box.physical_qty = physical_qty
    box.closed_by = closed_by

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="box_closed",
        resource_type="inward_box",
        resource_id=box.id,
        user_id=closed_by,
        organisation_id=organisation_id,
        before_data={"status": "scanning", "scanned_qty": box.scanned_qty},
        after_data={"status": "pending_verification", "physical_qty": physical_qty},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(box)
    return box
```

- [ ] **Step 4: Add three box endpoints to `app/routers/inward.py`**

Append to the router file:

```python
@router.post("/boxes", response_model=BoxResponse, status_code=status.HTTP_201_CREATED)
async def create_box_endpoint(
    body: BoxCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.create_box(
        db,
        customer_id=body.customer_id,
        organisation_id=current_user.organisation_id,
        created_by=current_user.user_id,
        user_roles=current_user.roles,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.get("/boxes/{box_id}", response_model=BoxResponse)
async def get_box_endpoint(
    box_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.get_box(
        db, box_id=box_id, organisation_id=current_user.organisation_id
    )
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    return _box_to_response(box)


@router.post("/boxes/{box_id}/close", response_model=BoxResponse)
async def close_box_endpoint(
    box_id: str,
    body: BoxClose,
    request: Request,
    current_user: CurrentUser = Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.close_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        physical_qty=body.physical_qty,
        closed_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)
```

Also add the `_box_to_response` helper and the `HTTPException` import **at the top of the router file** (inside the existing imports block):

```python
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status

# Helper at module level (after imports):
from app.schemas.inward import BoxResponse, ScanCreateResponse, ScanResponse


def _box_to_response(box: "InwardBox") -> BoxResponse:
    from app.models.enums import InwardBoxStatusEnum

    return BoxResponse(
        id=box.id,
        box_id=box.box_id,
        customer_id=box.customer_id,
        status=box.status,
        physical_qty=box.physical_qty,
        scanned_qty=box.scanned_qty,
        inscan_number=box.inscan_number,
        scans=[
            ScanResponse(
                id=s.id,
                ean=s.ean,
                code_type=s.code_type,
                is_deleted=s.is_deleted,
                created_at=s.created_at,
            )
            for s in box.scans
        ],
        created_at=box.created_at,
        is_read_only=box.status == InwardBoxStatusEnum.completed,
    )
```

- [ ] **Step 5: Run integration tests**

```bash
pytest tests/integration/test_inward_boxes.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/unit/ tests/integration/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/inward_service.py app/routers/inward.py \
        tests/integration/test_inward_boxes.py
git commit -m "feat(inward): add box create/get/close endpoints with atomic Box ID generation"
```

---

## Task 5: Scan Ingestion + Deletion

**Files:**
- Modify: `app/services/inward_service.py` — add `add_scan`, `delete_scan`
- Modify: `app/routers/inward.py` — add `POST /api/inward/boxes/{box_id}/scans` and `DELETE /api/inward/scans/{scan_id}`
- Create: `tests/integration/test_inward_scans.py`

**Interfaces:**
- Consumes: `InwardBox`, `InwardScan`, `InwardPO`, `InwardPOLine`, `InventoryLedgerEntry` (Task 1); `ScanCreate`, `ScanCreateResponse` (Task 2); `get_box` (Task 4).
- Produces:
  - `add_scan(db, *, box_id, organisation_id, ean, code_type, created_by, ip_address) -> tuple[InwardScan, str | None]`
  - `delete_scan(db, *, scan_id, organisation_id, deleted_by, ip_address) -> InwardScan`

- [ ] **Step 1: Write failing integration tests**

Create `tests/integration/test_inward_scans.py`:

```python
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
    assert body.get("note") is None


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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/integration/test_inward_scans.py -v
```

Expected: all fail (service/router not yet implemented).

- [ ] **Step 3: Add `add_scan` and `delete_scan` to `app/services/inward_service.py`**

Append to the service file:

```python
# ── Scan: add ─────────────────────────────────────────────────────────────────

_MAX_ALLOC_RETRIES = 5


async def add_scan(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    ean: str,
    code_type: InwardCodeTypeEnum,
    created_by: int,
    ip_address: str | None,
) -> tuple[InwardScan, str | None]:
    """Allocate EAN to oldest open PO line (FIFO) and insert a scan row.

    Returns (scan, note). note is the PRD 'no recorded inward stock' string or None.
    Raises HTTPException 400 for EAN-not-found and all-lines-full.
    """
    from sqlalchemy.orm import selectinload

    # 1. Load box
    box_result = await db.execute(
        select(InwardBox).where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    box = box_result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    if box.status != InwardBoxStatusEnum.scanning:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add scans to a box that is not in 'scanning' status",
        )

    # 2. Check if ANY open PO line has this EAN for this org/customer
    any_line_result = await db.execute(
        select(InwardPOLine)
        .join(InwardPO, InwardPOLine.inward_po_id == InwardPO.id)
        .where(
            InwardPO.organisation_id == organisation_id,
            InwardPO.customer_id == box.customer_id,
            InwardPO.status == InwardReferenceStatusEnum.open,
            InwardPOLine.ean == ean,
        )
        .limit(1)
    )
    if any_line_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="EAN not found in open POs")

    # 3. FIFO allocation with conditional UPDATE retry loop
    allocated_line_id: int | None = None
    for _ in range(_MAX_ALLOC_RETRIES):
        # Find oldest PO line with remaining capacity
        candidate_result = await db.execute(
            select(InwardPOLine)
            .join(InwardPO, InwardPOLine.inward_po_id == InwardPO.id)
            .where(
                InwardPO.organisation_id == organisation_id,
                InwardPO.customer_id == box.customer_id,
                InwardPO.status == InwardReferenceStatusEnum.open,
                InwardPOLine.ean == ean,
                InwardPOLine.packed_qty < InwardPOLine.ordered_qty,
            )
            .order_by(InwardPO.uploaded_at.asc(), InwardPO.id.asc())
            .limit(1)
        )
        candidate = candidate_result.scalar_one_or_none()
        if candidate is None:
            # All lines now full (race: another scan filled the last slot)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Quantity complete for all open POs",
            )

        # Conditional UPDATE — atomically increments only if still has capacity
        updated = await db.execute(
            text("""
                UPDATE inward_po_lines
                SET packed_qty = packed_qty + 1
                WHERE id = :id AND packed_qty < ordered_qty
                RETURNING id
            """),
            {"id": candidate.id},
        )
        if updated.scalar_one_or_none() is not None:
            allocated_line_id = candidate.id
            break
        # 0 rows updated — another session raced us; retry

    if allocated_line_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity complete for all open POs",
        )

    # 4. Insert scan row
    scan = InwardScan(
        organisation_id=organisation_id,
        inward_box_id=box.id,
        inward_po_line_id=allocated_line_id,
        ean=ean,
        code_type=code_type,
        is_deleted=False,
        created_by=created_by,
    )
    db.add(scan)

    # 5. Increment box scanned_qty (same transaction)
    await db.execute(
        text("UPDATE inward_boxes SET scanned_qty = scanned_qty + 1 WHERE id = :id"),
        {"id": box.id},
    )

    # 6. Check for "no recorded inward stock" note
    ledger_result = await db.execute(
        text("""
            SELECT COALESCE(SUM(quantity_change), 0)
            FROM inventory_ledger_entries
            WHERE organisation_id = :org AND customer_id = :cust AND ean = :ean
        """),
        {"org": organisation_id, "cust": box.customer_id, "ean": ean},
    )
    note: str | None = None
    if (ledger_result.scalar() or 0) <= 0:
        note = "Note: no recorded inward stock for this item."

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="scan_added",
        resource_type="inward_scan",
        user_id=created_by,
        organisation_id=organisation_id,
        after_data={"box_id": box_id, "ean": ean, "po_line_id": allocated_line_id},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(scan)
    return scan, note


# ── Scan: delete (soft) ───────────────────────────────────────────────────────

async def delete_scan(
    db: AsyncSession,
    *,
    scan_id: int,
    organisation_id: int,
    deleted_by: int,
    ip_address: str | None,
) -> InwardScan:
    """Soft-delete a scan. Decrements scanned_qty and packed_qty in the same transaction.

    If the parent box is completed, also writes a -1 ledger reversal entry.
    """
    from app.models.customer import Customer

    scan_result = await db.execute(
        select(InwardScan)
        .join(InwardBox, InwardScan.inward_box_id == InwardBox.id)
        .where(
            InwardScan.id == scan_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    scan = scan_result.scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    if scan.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Scan already deleted"
        )

    # Soft-delete
    scan.is_deleted = True
    scan.deleted_at = datetime.now(timezone.utc)
    scan.deleted_by = deleted_by

    # Decrement box scanned_qty
    await db.execute(
        text("UPDATE inward_boxes SET scanned_qty = scanned_qty - 1 WHERE id = :id"),
        {"id": scan.inward_box_id},
    )

    # Decrement po_line packed_qty (if allocated)
    if scan.inward_po_line_id is not None:
        await db.execute(
            text("UPDATE inward_po_lines SET packed_qty = packed_qty - 1 WHERE id = :id"),
            {"id": scan.inward_po_line_id},
        )

    # If box is submitted (completed), write -1 ledger reversal
    box_result = await db.execute(
        select(InwardBox).where(InwardBox.id == scan.inward_box_id)
    )
    box = box_result.scalar_one()
    if box.status == InwardBoxStatusEnum.completed:
        db.add(InventoryLedgerEntry(
            organisation_id=organisation_id,
            customer_id=box.customer_id,
            ean=scan.ean,
            quantity_change=-1,
            source_type=LedgerSourceTypeEnum.inward_scan_deletion,
            source_id=scan.id,
        ))

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="scan_deleted",
        resource_type="inward_scan",
        resource_id=scan.id,
        user_id=deleted_by,
        organisation_id=organisation_id,
        before_data={"ean": scan.ean, "is_deleted": False},
        after_data={"is_deleted": True},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(scan)
    return scan
```

- [ ] **Step 4: Add scan endpoints to `app/routers/inward.py`**

Append to the router file:

```python
@router.post(
    "/boxes/{box_id}/scans",
    response_model=ScanCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_scan_endpoint(
    box_id: str,
    body: ScanCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> ScanCreateResponse:
    scan, note = await inward_service.add_scan(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        ean=body.ean,
        code_type=body.code_type,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return ScanCreateResponse(
        id=scan.id,
        ean=scan.ean,
        code_type=scan.code_type,
        is_deleted=scan.is_deleted,
        created_at=scan.created_at,
        note=note,
    )


@router.delete("/scans/{scan_id}", response_model=ScanResponse)
async def delete_scan_endpoint(
    scan_id: int,
    request: Request,
    current_user: CurrentUser = Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> ScanResponse:
    scan = await inward_service.delete_scan(
        db,
        scan_id=scan_id,
        organisation_id=current_user.organisation_id,
        deleted_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return ScanResponse(
        id=scan.id,
        ean=scan.ean,
        code_type=scan.code_type,
        is_deleted=scan.is_deleted,
        created_at=scan.created_at,
    )
```

- [ ] **Step 5: Run integration tests**

```bash
pytest tests/integration/test_inward_scans.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/unit/ tests/integration/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/inward_service.py app/routers/inward.py \
        tests/integration/test_inward_scans.py
git commit -m "feat(inward): add scan ingestion with FIFO allocation and soft-delete"
```

---

## Task 6: Atomic Box Submission

**Files:**
- Modify: `app/services/inward_service.py` — add `submit_box`
- Modify: `app/routers/inward.py` — add `POST /api/inward/boxes/{box_id}/submit`
- Create: `tests/integration/test_inward_submit.py`

**Interfaces:**
- Consumes: `InwardBox`, `InwardScan`, `InventoryLedgerEntry`, `Counter` (Task 1); `_next_counter` (Task 4); `BoxResponse` (Task 2).
- Produces: `submit_box(db, *, box_id, organisation_id, submitted_by, ip_address) -> InwardBox`

The Inscan Number format is `INS-{customer_code}-{YYYYMMDD}-{N:04d}` where the date uses `APP_TIMEZONE` from settings and `N` is from the `inscan_number` counter scoped to `(org, customer_code, YYYYMMDD)`.

- [ ] **Step 1: Write failing integration tests**

Create `tests/integration/test_inward_submit.py`:

```python
import re

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.customer import Customer
from app.models.enums import (
    CustomerStatusEnum,
    InwardBoxStatusEnum,
    InwardReferenceStatusEnum,
)
from app.models.inward import InwardBox, InwardPO, InwardPOLine, InwardScan


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
async def ready_box(db, org, customer) -> InwardBox:
    """A box in pending_verification with 2 non-deleted scans."""
    po = InwardPO(
        organisation_id=org.id, customer_id=customer.id,
        po_number="PO-SUBMIT-001", status=InwardReferenceStatusEnum.open,
    )
    db.add(po)
    await db.flush()
    line = InwardPOLine(
        inward_po_id=po.id, organisation_id=org.id,
        ean="1234567890123", ordered_qty=10, packed_qty=2,
    )
    db.add(line)
    await db.flush()

    box = InwardBox(
        box_id="B-TST-088001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=2,
        physical_qty=2,
    )
    db.add(box)
    await db.flush()

    for ean in ["1234567890123", "1234567890123"]:
        db.add(InwardScan(
            organisation_id=org.id,
            inward_box_id=box.id,
            inward_po_line_id=line.id,
            ean=ean,
            is_deleted=False,
        ))
    await db.flush()
    return box


# ── Submit box ────────────────────────────────────────────────────────────────

async def test_submit_box_success(client, inward_operator_user, org, ready_box):
    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{ready_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["is_read_only"] is True
    inscan = body["inscan_number"]
    assert inscan is not None
    # Format: INS-TST-YYYYMMDD-NNNN
    assert re.match(r"^INS-TST-\d{8}-\d{4}$", inscan), f"Unexpected inscan format: {inscan}"


async def test_submit_box_creates_ledger_entries(
    client, inward_operator_user, org, ready_box, db
):
    from app.models.inward import InventoryLedgerEntry
    from sqlalchemy import select

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    await client.post(
        f"/api/inward/boxes/{ready_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )

    result = await db.execute(
        select(InventoryLedgerEntry).where(
            InventoryLedgerEntry.organisation_id == org.id,
        )
    )
    entries = result.scalars().all()
    # 2 non-deleted scans → 2 ledger entries of +1 each
    assert len(entries) == 2
    assert all(e.quantity_change == 1 for e in entries)
    assert all(e.source_type.value == "inward_submission" for e in entries)


async def test_submit_box_generates_sequential_inscan_numbers(
    client, inward_operator_user, org, db, customer
):
    """Two submissions on the same date produce consecutive 4-digit suffixes."""

    def _make_ready_box(box_id: str) -> InwardBox:
        return InwardBox(
            box_id=box_id,
            organisation_id=org.id,
            customer_id=customer.id,
            status=InwardBoxStatusEnum.pending_verification,
            scanned_qty=0,
            physical_qty=0,
        )

    b1 = _make_ready_box("B-TST-088010")
    b2 = _make_ready_box("B-TST-088011")
    db.add(b1)
    db.add(b2)
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    r1 = await client.post(
        f"/api/inward/boxes/{b1.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    r2 = await client.post(
        f"/api/inward/boxes/{b2.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )

    n1 = int(r1.json()["inscan_number"].split("-")[-1])
    n2 = int(r2.json()["inscan_number"].split("-")[-1])
    assert n2 == n1 + 1


async def test_submit_box_wrong_status(client, inward_operator_user, org, db, customer):
    box = InwardBox(
        box_id="B-TST-088020",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.scanning,  # not pending_verification
        scanned_qty=0,
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "inward@test.com", "InwardPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "pending_verification" in resp.json()["detail"].lower()


async def test_submit_box_packer_forbidden(client, packer_user, org, ready_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{ready_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/integration/test_inward_submit.py -v
```

Expected: all fail (service/router not yet implemented).

- [ ] **Step 3: Add `submit_box` to `app/services/inward_service.py`**

Append to the service file:

```python
# ── Box: submit ───────────────────────────────────────────────────────────────

async def submit_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    submitted_by: int,
    ip_address: str | None,
) -> InwardBox:
    """Atomic submission: Inscan Number + status=completed + ledger entries.

    All three succeed or all roll back.
    One +1 InventoryLedgerEntry per non-deleted scan. Inscan Number format:
    INS-{customer_code}-{YYYYMMDD}-{N:04d} using APP_TIMEZONE for the date.
    """
    from zoneinfo import ZoneInfo
    from sqlalchemy.orm import selectinload
    from app.config import settings
    from app.models.customer import Customer

    result = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")

    if box.status != InwardBoxStatusEnum.pending_verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Box must be in 'pending_verification' status to submit (current: {box.status.value})",
        )

    # Load customer for code
    cust_result = await db.execute(
        select(Customer).where(Customer.id == box.customer_id)
    )
    customer = cust_result.scalar_one()

    # Generate Inscan Number via atomic counter
    tz = ZoneInfo(settings.APP_TIMEZONE)
    date_key = datetime.now(tz).strftime("%Y%m%d")
    n = await _next_counter(
        db,
        counter_type=CounterTypeEnum.inscan_number,
        organisation_id=organisation_id,
        customer_code=customer.code,
        date_key=date_key,
    )
    inscan_number = f"INS-{customer.code}-{date_key}-{n:04d}"

    # Write one +1 ledger entry per non-deleted scan
    active_scans = [s for s in box.scans if not s.is_deleted]
    for scan in active_scans:
        db.add(InventoryLedgerEntry(
            organisation_id=organisation_id,
            customer_id=box.customer_id,
            ean=scan.ean,
            quantity_change=1,
            source_type=LedgerSourceTypeEnum.inward_submission,
            source_id=scan.id,
        ))

    # Update box status atomically (same transaction as ledger writes and counter)
    now_utc = datetime.now(timezone.utc)
    box.status = InwardBoxStatusEnum.completed
    box.inscan_number = inscan_number
    box.submitted_at = now_utc
    box.submitted_by = submitted_by

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="box_submitted",
        resource_type="inward_box",
        resource_id=box.id,
        user_id=submitted_by,
        organisation_id=organisation_id,
        before_data={"status": "pending_verification"},
        after_data={
            "status": "completed",
            "inscan_number": inscan_number,
            "ledger_entries_written": len(active_scans),
        },
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(box)

    # Reload with scans for response serialisation
    reloaded = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(InwardBox.id == box.id)
    )
    return reloaded.scalar_one()
```

- [ ] **Step 4: Add submit endpoint to `app/routers/inward.py`**

Append to the router file:

```python
@router.post("/boxes/{box_id}/submit", response_model=BoxResponse)
async def submit_box_endpoint(
    box_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.submit_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        submitted_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)
```

- [ ] **Step 5: Run integration tests**

```bash
pytest tests/integration/test_inward_submit.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 6: Run the complete test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass (unit + integration).

- [ ] **Step 7: Run linter**

```bash
ruff check .
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app/services/inward_service.py app/routers/inward.py \
        tests/integration/test_inward_submit.py
git commit -m "feat(inward): add atomic box submission with Inscan Number and ledger writes"
```

---

## Self-Review: Spec Coverage Checklist

| Requirement | Covered in Task |
|-------------|----------------|
| `POST /api/inward/pos` — CSV upload | Task 3 |
| Duplicate PO detection → verbatim 409 message | Task 3 |
| Missing CSV column → verbatim 400 message | Task 3 |
| `POST /api/inward/boxes` — create with Box ID `B-<CUSTCODE>-<6-digit>` | Task 4 |
| Atomic counter UPSERT (no SELECT FOR UPDATE) | Task 4 (`_next_counter`) |
| `GET /api/inward/boxes/{box_id}` — read-only mode if completed | Task 4 |
| `POST /api/inward/boxes/{box_id}/close` — validate physical == scanned qty | Task 4 |
| Packer one-active-box guard | Task 4 (`create_box`) |
| `POST /api/inward/boxes/{box_id}/scans` — EAN lookup FIFO | Task 5 |
| EAN not found → verbatim 400 message | Task 5 |
| All lines full → verbatim 400 message | Task 5 |
| No inward stock note on successful scan | Task 5 |
| FIFO: oldest `uploaded_at`, tie-break lower PO id | Task 5 |
| Conditional UPDATE with 5-retry race safety | Task 5 |
| `DELETE /api/inward/scans/{scan_id}` — soft delete | Task 5 |
| `scanned_qty` + `packed_qty` decremented in same tx as soft-delete | Task 5 |
| Reversal ledger entry on delete of submitted scan | Task 5 |
| `POST /api/inward/boxes/{box_id}/submit` — Inscan Number `INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>` | Task 6 |
| Submission atomic (Inscan Number + status + ledger entries) | Task 6 |
| One `+1` ledger entry per non-deleted scan at submission | Task 6 |
| `organisation_id` from JWT on every query | All tasks |
| Audit log in same transaction as every mutation | All tasks |
| `inward_scans` + `inventory_ledger_entries` append-only | Tasks 5–6 |
| `CounterTypeEnum.inward_box` enum extension | Task 1 |
| `LedgerSourceTypeEnum.inward_scan_deletion` enum extension | Task 1 |
| `require_inward_operator` on PO upload and box submit | Tasks 3, 6 |
| `require_packer` on box create, close, scan add/delete | Tasks 4, 5 |
