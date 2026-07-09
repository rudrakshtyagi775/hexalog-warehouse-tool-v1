# Outward Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Outward Module backend — PO upload, box management, and FIFO-allocated scanning with over-pack prevention and ledger tracking.

**Architecture:** Four ORM models (`OutwardPO`, `OutwardPOLine`, `OutwardBox`, `OutwardScan`) backed by a single Alembic migration. A service layer (`outward_service.py`) owns all business logic including FIFO allocation via conditional-UPDATE retry loop (max 5 retries). A router (`routers/outward.py`) wires HTTP to services with role enforcement. All enums already exist in `app/models/enums.py`.

**Tech Stack:** FastAPI async, SQLAlchemy 2.0 async, asyncpg, Alembic (sync psycopg2 for migrations), pytest + httpx for integration tests.

## Global Constraints

- `asyncio_mode = "auto"` — NO `@pytest.mark.asyncio` decorators on any test
- Ruff `line-length=100`
- Services commit; routers never commit
- `write_audit_log` never commits (called before `await db.commit()`)
- `organisation_id` always from JWT (`current_user.organisation_id`), never from request body
- `lazy="raise"` on all ORM relationships — always use `selectinload` for eager loading
- All enums: `(str, enum.Enum)` subclass — already in `app/models/enums.py`
- Inward migration pattern: use `postgresql.ENUM(..., create_type=False)` in `op.create_table`, add DO blocks before tables in `upgrade()` for enum creation
- `expire_on_commit=False` on async_sessionmaker — already set in conftest
- Multi-tenancy: every query on org-scoped tables must include `WHERE organisation_id = :org_id`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `app/models/outward.py` | Create | 4 ORM models |
| `app/models/__init__.py` | Modify | Add outward model imports + `__all__` entries |
| `alembic/versions/<rev>_add_outward_module_tables.py` | Create (autogenerate + patch) | DB migration |
| `app/schemas/outward.py` | Create | Pydantic request/response schemas |
| `app/services/outward_service.py` | Create | Business logic for PO upload, box CRUD, scan FIFO |
| `app/routers/outward.py` | Create | HTTP wiring, role enforcement |
| `app/main.py` | Modify | Register `outward_router` |
| `tests/integration/test_outward_po.py` | Create | 5 PO upload tests |
| `tests/integration/test_outward_boxes.py` | Create | 5 box management tests |
| `tests/integration/test_outward_scans.py` | Create | 6 scan tests |

---

### Task 1: ORM Models (`app/models/outward.py`)

**Files:**
- Create: `app/models/outward.py`
- Modify: `app/models/__init__.py`

**Interfaces:**
- Produces: `OutwardPO`, `OutwardPOLine`, `OutwardBox`, `OutwardScan` — imported by service and test files

- [ ] **Step 1: Write `app/models/outward.py`**

```python
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
from app.models.enums import OutwardBoxStatusEnum, OutwardPoStatusEnum, OutwardScanResultEnum


class OutwardPO(TimestampMixin, Base):
    """One row per outward PO/invoice per organisation."""

    __tablename__ = "outward_pos"
    __table_args__ = (
        UniqueConstraint("organisation_id", "po_number", name="uq_outward_pos_org_po"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OutwardPoStatusEnum] = mapped_column(
        PGEnum(OutwardPoStatusEnum, name="outward_po_status_enum", create_type=False),
        default=OutwardPoStatusEnum.open,
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lines: Mapped[list["OutwardPOLine"]] = relationship(
        back_populates="outward_po", lazy="raise"
    )


class OutwardPOLine(Base):
    """One row per EAN per outward PO. packed_qty tracks FIFO allocation."""

    __tablename__ = "outward_po_lines"
    __table_args__ = (
        Index("idx_outward_po_lines_ean_org", "organisation_id", "ean"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outward_po_id: Mapped[int] = mapped_column(
        ForeignKey("outward_pos.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordered_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    packed_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    outward_po: Mapped["OutwardPO"] = relationship(back_populates="lines", lazy="raise")


class OutwardBox(TimestampMixin, Base):
    """One outward packing box. Box ID (OB-CUSTCODE-000001) generated via Counter."""

    __tablename__ = "outward_boxes"
    __table_args__ = (
        Index("idx_outward_boxes_org_status", "organisation_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    box_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[OutwardBoxStatusEnum] = mapped_column(
        PGEnum(OutwardBoxStatusEnum, name="outward_box_status_enum", create_type=False),
        default=OutwardBoxStatusEnum.open,
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    scans: Mapped[list["OutwardScan"]] = relationship(
        back_populates="box",
        foreign_keys="[OutwardScan.outward_box_id]",
        lazy="raise",
    )


class OutwardScan(Base):
    """Append-only. scan_result=deleted marks soft-deletion."""

    __tablename__ = "outward_scans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organisation_id: Mapped[int] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    outward_box_id: Mapped[int] = mapped_column(
        ForeignKey("outward_boxes.id", ondelete="RESTRICT"), nullable=False
    )
    outward_po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("outward_po_lines.id", ondelete="SET NULL"), nullable=True
    )
    ean: Mapped[str] = mapped_column(Text, nullable=False)
    scan_result: Mapped[OutwardScanResultEnum] = mapped_column(
        PGEnum(OutwardScanResultEnum, name="outward_scan_result_enum", create_type=False),
        nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    box: Mapped["OutwardBox"] = relationship(
        back_populates="scans",
        foreign_keys="[OutwardScan.outward_box_id]",
        lazy="raise",
    )
```

- [ ] **Step 2: Update `app/models/__init__.py`**

Add after the existing inward imports (line 28 in current file — after `InventoryLedgerEntry`):

```python
from app.models.outward import (
    OutwardPO,
    OutwardPOLine,
    OutwardBox,
    OutwardScan,
)
```

Add to `__all__` list (after `"InventoryLedgerEntry"`):
```python
    "OutwardPO",
    "OutwardPOLine",
    "OutwardBox",
    "OutwardScan",
```

- [ ] **Step 3: Verify models import cleanly**

```bash
cd C:\Users\rudra\OneDrive\Desktop\hexalog_packagingtool
python -c "from app.models import OutwardPO, OutwardPOLine, OutwardBox, OutwardScan; print('OK')"
```

Expected: `OK`

---

### Task 2: Alembic Migration

**Files:**
- Create: `alembic/versions/<autogenerated-rev>_add_outward_module_tables.py` (autogenerate then patch)

**Interfaces:**
- Consumes: `OutwardPO`, `OutwardPOLine`, `OutwardBox`, `OutwardScan` from Task 1
- Produces: 4 PostgreSQL tables + 3 enum types + 3 indexes; `alembic upgrade head` succeeds

- [ ] **Step 1: Generate migration**

```bash
cd C:\Users\rudra\OneDrive\Desktop\hexalog_packagingtool
alembic revision --autogenerate -m "add_outward_module_tables"
```

Note the generated filename (e.g., `alembic/versions/XXXX_add_outward_module_tables.py`).

- [ ] **Step 2: Patch the generated migration**

Open the generated file and replace the `upgrade()` function with the following structure:

```python
def upgrade() -> None:
    # Create the 3 outward enum types — use EXCEPTION WHEN pattern (same as inward migration)
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE outward_po_status_enum AS ENUM ('open', 'closed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE outward_box_status_enum AS ENUM ('open', 'in_use', 'closed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE outward_scan_result_enum AS ENUM ('accepted', 'rejected', 'deleted'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )

    op.create_table('outward_pos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('po_number', sa.Text(), nullable=False),
    sa.Column('status', postgresql.ENUM('open', 'closed', name='outward_po_status_enum', create_type=False), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organisation_id', 'po_number', name='uq_outward_pos_org_po')
    )
    op.create_index('ix_outward_pos_org', 'outward_pos', ['organisation_id'])

    op.create_table('outward_po_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('outward_po_id', sa.Integer(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('ean', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('ordered_qty', sa.Integer(), nullable=False),
    sa.Column('packed_qty', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['outward_po_id'], ['outward_pos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_outward_po_lines_ean', 'outward_po_lines', ['organisation_id', 'ean'])

    op.create_table('outward_boxes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('box_id', sa.Text(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('status', postgresql.ENUM('open', 'in_use', 'closed', name='outward_box_status_enum', create_type=False), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('closed_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['closed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('box_id')
    )
    op.create_index('ix_outward_boxes_org_status', 'outward_boxes', ['organisation_id', 'status'])

    op.create_table('outward_scans',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('organisation_id', sa.Integer(), nullable=False),
    sa.Column('outward_box_id', sa.Integer(), nullable=False),
    sa.Column('outward_po_line_id', sa.Integer(), nullable=True),
    sa.Column('ean', sa.Text(), nullable=False),
    sa.Column('scan_result', postgresql.ENUM('accepted', 'rejected', 'deleted', name='outward_scan_result_enum', create_type=False), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['outward_box_id'], ['outward_boxes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['outward_po_line_id'], ['outward_po_lines.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
```

Replace the `downgrade()` function:

```python
def downgrade() -> None:
    op.drop_index('ix_outward_boxes_org_status', table_name='outward_boxes')
    op.drop_index('ix_outward_po_lines_ean', table_name='outward_po_lines')
    op.drop_index('ix_outward_pos_org', table_name='outward_pos')
    op.drop_table('outward_scans')
    op.drop_table('outward_boxes')
    op.drop_table('outward_po_lines')
    op.drop_table('outward_pos')
    op.execute("DROP TYPE IF EXISTS outward_scan_result_enum")
    op.execute("DROP TYPE IF EXISTS outward_box_status_enum")
    op.execute("DROP TYPE IF EXISTS outward_po_status_enum")
```

- [ ] **Step 3: Run migration**

```bash
alembic upgrade head
```

Expected: exits 0 with no errors. If autogenerate also detected `sa.Enum(...)` columns instead of `postgresql.ENUM(...)`, the patch in step 2 already corrects them.

---

### Task 3: Pydantic Schemas (`app/schemas/outward.py`)

**Files:**
- Create: `app/schemas/outward.py`

**Interfaces:**
- Produces:
  - `OutwardBoxCreate(customer_id: int)`
  - `OutwardScanCreate(ean: str)`
  - `OutwardPOLineResponse`, `OutwardPOResponse`, `OutwardScanResponse`, `OutwardBoxResponse`, `OutwardScanCreateResponse`
  - All response models have `model_config = {"from_attributes": True}`

- [ ] **Step 1: Write `app/schemas/outward.py`**

```python
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import OutwardBoxStatusEnum, OutwardPoStatusEnum, OutwardScanResultEnum


# ── Request schemas ───────────────────────────────────────────────────────────

class OutwardBoxCreate(BaseModel):
    customer_id: int


class OutwardScanCreate(BaseModel):
    ean: str


# ── Response schemas ──────────────────────────────────────────────────────────

class OutwardPOLineResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    description: str | None
    ordered_qty: int
    packed_qty: int


class OutwardPOResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    po_number: str
    customer_id: int
    status: OutwardPoStatusEnum
    uploaded_at: datetime
    lines: list[OutwardPOLineResponse]


class OutwardScanResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    scan_result: OutwardScanResultEnum
    created_at: datetime


class OutwardBoxResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    box_id: str
    customer_id: int
    status: OutwardBoxStatusEnum
    scans: list[OutwardScanResponse]
    created_at: datetime
    is_read_only: bool  # True when status == closed


class OutwardScanCreateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    scan_result: OutwardScanResultEnum
    created_at: datetime
    note: str | None = None
```

- [ ] **Step 2: Verify schemas import cleanly**

```bash
python -c "from app.schemas.outward import OutwardBoxCreate, OutwardPOResponse, OutwardScanCreateResponse; print('OK')"
```

Expected: `OK`

---

### Task 4: Service Layer (`app/services/outward_service.py`)

**Files:**
- Create: `app/services/outward_service.py`

**Interfaces:**
- Consumes:
  - `_next_counter` from `app.services.inward_service` (reuse existing)
  - `InventoryLedgerEntry` from `app.models.inward`
  - `write_audit_log` from `app.services.audit_service`
  - `OutwardPO`, `OutwardPOLine`, `OutwardBox`, `OutwardScan` from Task 1
- Produces:
  - `upload_po(db, *, customer_id, organisation_id, uploaded_by, ip_address, po_number, csv_bytes) -> OutwardPO`
  - `create_box(db, *, customer_id, organisation_id, created_by, ip_address) -> OutwardBox`
  - `get_box(db, *, box_id, organisation_id) -> OutwardBox | None`
  - `close_box(db, *, box_id, organisation_id, closed_by, ip_address) -> OutwardBox`
  - `add_scan(db, *, box_id, organisation_id, ean, created_by, ip_address) -> tuple[OutwardScan, str | None]`
  - `delete_scan(db, *, scan_id, organisation_id, deleted_by, ip_address) -> OutwardScan`

- [ ] **Step 1: Write `app/services/outward_service.py`**

```python
import csv
import io

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    AuditModuleEnum,
    CounterTypeEnum,
    LedgerSourceTypeEnum,
    OutwardBoxStatusEnum,
    OutwardPoStatusEnum,
    OutwardScanResultEnum,
)
from app.models.inward import InventoryLedgerEntry
from app.models.outward import OutwardBox, OutwardPO, OutwardPOLine, OutwardScan
from app.services.audit_service import write_audit_log
from app.services.inward_service import _next_counter

log = structlog.get_logger(__name__)

_PO_REQUIRED_COLUMNS = {"po_number", "ean", "ordered_qty"}
_MAX_ALLOC_RETRIES = 5


# ── PO upload ─────────────────────────────────────────────────────────────────

async def upload_po(
    db: AsyncSession,
    *,
    customer_id: int,
    organisation_id: int,
    uploaded_by: int,
    ip_address: str | None,
    po_number: str,
    csv_bytes: bytes,
) -> OutwardPO:
    """Parse CSV, validate columns, detect duplicates, create OutwardPO + lines atomically."""
    from app.models.customer import Customer

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

    # 2. Validate customer belongs to this organisation
    cust_result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.organisation_id == organisation_id,
        )
    )
    if cust_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    # 3. Duplicate detection (pre-check; IntegrityError on commit handles the concurrent case)
    existing = await db.execute(
        select(OutwardPO).where(
            OutwardPO.organisation_id == organisation_id,
            OutwardPO.po_number == po_number,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="An outward PO already exists for this PO/Invoice number.",
        )

    # 4. Build lines — reject CSVs that contain rows for a different po_number
    lines_data: list[dict] = []
    for row in rows:
        row_po = row.get("po_number", "").strip()
        if row_po != po_number:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"CSV contains rows for a different PO number ('{row_po}'). "
                    f"All rows must match the requested PO '{po_number}'."
                ),
            )
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

    # 5. Create OutwardPO
    po = OutwardPO(
        organisation_id=organisation_id,
        customer_id=customer_id,
        po_number=po_number,
        status=OutwardPoStatusEnum.open,
        uploaded_by=uploaded_by,
    )
    db.add(po)
    await db.flush()  # populates po.id

    # 6. Create OutwardPOLine rows
    for ld in lines_data:
        db.add(OutwardPOLine(
            outward_po_id=po.id,
            organisation_id=organisation_id,
            ean=ld["ean"],
            description=ld["description"],
            ordered_qty=ld["ordered_qty"],
            packed_qty=0,
        ))

    await write_audit_log(
        db,
        module=AuditModuleEnum.outward,
        action="outward_po_uploaded",
        resource_type="outward_po",
        resource_id=po.id,
        user_id=uploaded_by,
        organisation_id=organisation_id,
        after_data={"po_number": po_number, "customer_id": customer_id, "line_count": len(lines_data)},
        ip_address=ip_address,
    )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An outward PO already exists for this PO/Invoice number.",
        )
    await db.refresh(po)
    return po


# ── Box: create ───────────────────────────────────────────────────────────────

async def create_box(
    db: AsyncSession,
    *,
    customer_id: int,
    organisation_id: int,
    created_by: int,
    ip_address: str | None,
) -> OutwardBox:
    from app.models.customer import Customer

    # 1. Load customer to get code for Box ID generation
    cust_result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.organisation_id == organisation_id,
        )
    )
    customer = cust_result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    # 2. Generate unique Box ID via atomic counter
    n = await _next_counter(
        db,
        counter_type=CounterTypeEnum.outward_box,
        organisation_id=organisation_id,
        customer_code=customer.code,
        date_key="",
    )
    box_id = f"OB-{customer.code}-{n:06d}"

    # 3. Persist
    box = OutwardBox(
        box_id=box_id,
        organisation_id=organisation_id,
        customer_id=customer_id,
        status=OutwardBoxStatusEnum.open,
        created_by=created_by,
    )
    db.add(box)
    await db.flush()  # populates box.id before writing the audit log

    await write_audit_log(
        db,
        module=AuditModuleEnum.outward,
        action="outward_box_created",
        resource_type="outward_box",
        resource_id=box.id,
        user_id=created_by,
        organisation_id=organisation_id,
        after_data={"box_id": box_id, "customer_id": customer_id},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(box)

    # Reload with scans eager-loaded
    result = await db.execute(
        select(OutwardBox)
        .options(selectinload(OutwardBox.scans))
        .where(OutwardBox.id == box.id)
    )
    return result.scalar_one()


# ── Box: get ──────────────────────────────────────────────────────────────────

async def get_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
) -> OutwardBox | None:
    result = await db.execute(
        select(OutwardBox)
        .options(selectinload(OutwardBox.scans))
        .where(
            OutwardBox.box_id == box_id,
            OutwardBox.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


# ── Box: close ────────────────────────────────────────────────────────────────

async def close_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    closed_by: int,
    ip_address: str | None,
) -> OutwardBox:
    result = await db.execute(
        select(OutwardBox)
        .options(selectinload(OutwardBox.scans))
        .where(
            OutwardBox.box_id == box_id,
            OutwardBox.organisation_id == organisation_id,
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")

    if box.status == OutwardBoxStatusEnum.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Box must be open or in_use to close (current: {box.status.value})",
        )

    before_status = box.status.value
    box.status = OutwardBoxStatusEnum.closed
    box.closed_by = closed_by

    await write_audit_log(
        db,
        module=AuditModuleEnum.outward,
        action="outward_box_closed",
        resource_type="outward_box",
        resource_id=box.id,
        user_id=closed_by,
        organisation_id=organisation_id,
        before_data={"status": before_status},
        after_data={"status": "closed"},
        ip_address=ip_address,
    )

    await db.commit()

    # Reload with scans eager-loaded
    reloaded = await db.execute(
        select(OutwardBox)
        .options(selectinload(OutwardBox.scans))
        .where(OutwardBox.id == box.id)
    )
    return reloaded.scalar_one()


# ── Scan: add ─────────────────────────────────────────────────────────────────

async def add_scan(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    ean: str,
    created_by: int,
    ip_address: str | None,
) -> tuple[OutwardScan, str | None]:
    """Allocate EAN to oldest open outward PO line (FIFO) and insert a scan row.

    Returns (scan, note). note is the PRD 'no recorded inward stock' string or None.
    """
    # 1. Load box
    box_result = await db.execute(
        select(OutwardBox).where(
            OutwardBox.box_id == box_id,
            OutwardBox.organisation_id == organisation_id,
        )
    )
    box = box_result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    if box.status == OutwardBoxStatusEnum.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add scans to a closed box",
        )

    # 2. Check if ANY open outward PO line has this EAN for this org/customer
    any_line_result = await db.execute(
        select(OutwardPOLine)
        .join(OutwardPO, OutwardPOLine.outward_po_id == OutwardPO.id)
        .where(
            OutwardPO.organisation_id == organisation_id,
            OutwardPO.customer_id == box.customer_id,
            OutwardPO.status == OutwardPoStatusEnum.open,
            OutwardPOLine.ean == ean,
        )
        .limit(1)
    )
    if any_line_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="EAN not found in open outward POs",
        )

    # 3. FIFO allocation with conditional UPDATE retry loop
    allocated_line_id: int | None = None
    for _ in range(_MAX_ALLOC_RETRIES):
        # Find oldest PO line with remaining capacity
        candidate_result = await db.execute(
            select(OutwardPOLine)
            .join(OutwardPO, OutwardPOLine.outward_po_id == OutwardPO.id)
            .where(
                OutwardPO.organisation_id == organisation_id,
                OutwardPO.customer_id == box.customer_id,
                OutwardPO.status == OutwardPoStatusEnum.open,
                OutwardPOLine.ean == ean,
                OutwardPOLine.packed_qty < OutwardPOLine.ordered_qty,
            )
            .order_by(OutwardPO.uploaded_at.asc(), OutwardPO.id.asc())
            .limit(1)
        )
        candidate = candidate_result.scalar_one_or_none()
        if candidate is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Quantity complete for all open outward POs",
            )

        # Conditional UPDATE — atomically increments only if still has capacity
        updated = await db.execute(
            text("""
                UPDATE outward_po_lines
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
            detail="Quantity complete for all open outward POs",
        )

    # 4. Insert scan row
    scan = OutwardScan(
        organisation_id=organisation_id,
        outward_box_id=box.id,
        outward_po_line_id=allocated_line_id,
        ean=ean,
        scan_result=OutwardScanResultEnum.accepted,
        created_by=created_by,
    )
    db.add(scan)

    # 5. If box.status == open, transition to in_use
    if box.status == OutwardBoxStatusEnum.open:
        await db.execute(
            text("UPDATE outward_boxes SET status = 'in_use' WHERE id = :id"),
            {"id": box.id},
        )

    # 6. Flush scan to get its id before the ledger entry
    await db.flush()

    # 7. Write -1 InventoryLedgerEntry (outgoing stock)
    db.add(InventoryLedgerEntry(
        organisation_id=organisation_id,
        customer_id=box.customer_id,
        ean=ean,
        quantity_change=-1,
        source_type=LedgerSourceTypeEnum.outward_scan,
        source_id=scan.id,
    ))

    # 8. Check ledger balance for "no inward stock" note
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
        module=AuditModuleEnum.outward,
        action="outward_scan_added",
        resource_type="outward_scan",
        user_id=created_by,
        organisation_id=organisation_id,
        after_data={"box_id": box_id, "ean": ean, "po_line_id": allocated_line_id},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(scan)
    return scan, note


# ── Scan: delete ──────────────────────────────────────────────────────────────

async def delete_scan(
    db: AsyncSession,
    *,
    scan_id: int,
    organisation_id: int,
    deleted_by: int,
    ip_address: str | None,
) -> OutwardScan:
    """Soft-delete an outward scan. Decrements packed_qty and writes +1 ledger reversal."""
    scan_result = await db.execute(
        select(OutwardScan)
        .join(OutwardBox, OutwardScan.outward_box_id == OutwardBox.id)
        .where(
            OutwardScan.id == scan_id,
            OutwardBox.organisation_id == organisation_id,
        )
    )
    scan = scan_result.scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    if scan.scan_result == OutwardScanResultEnum.deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Scan already deleted"
        )

    # Load box for customer_id (needed for ledger entry)
    box_result = await db.execute(
        select(OutwardBox).where(OutwardBox.id == scan.outward_box_id)
    )
    box = box_result.scalar_one()

    # Soft-delete
    scan.scan_result = OutwardScanResultEnum.deleted

    # Decrement po_line packed_qty (if allocated)
    if scan.outward_po_line_id is not None:
        await db.execute(
            text("UPDATE outward_po_lines SET packed_qty = packed_qty - 1 WHERE id = :id"),
            {"id": scan.outward_po_line_id},
        )

    # Write +1 ledger reversal
    db.add(InventoryLedgerEntry(
        organisation_id=organisation_id,
        customer_id=box.customer_id,
        ean=scan.ean,
        quantity_change=1,
        source_type=LedgerSourceTypeEnum.outward_scan_deletion,
        source_id=scan.id,
    ))

    await write_audit_log(
        db,
        module=AuditModuleEnum.outward,
        action="outward_scan_deleted",
        resource_type="outward_scan",
        resource_id=scan.id,
        user_id=deleted_by,
        organisation_id=organisation_id,
        before_data={"ean": scan.ean, "scan_result": "accepted"},
        after_data={"scan_result": "deleted"},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(scan)
    return scan
```

- [ ] **Step 2: Verify service imports cleanly**

```bash
python -c "from app.services import outward_service; print('OK')"
```

Expected: `OK`

---

### Task 5: Router (`app/routers/outward.py`) + Register in `app/main.py`

**Files:**
- Create: `app/routers/outward.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: all service functions from Task 4, all schemas from Task 3
- Produces: 6 HTTP endpoints at `/api/outward/*`

- [ ] **Step 1: Write `app/routers/outward.py`**

```python
import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_user, require_inward_operator, require_packer
from app.models.enums import OutwardBoxStatusEnum
from app.models.outward import OutwardBox, OutwardPO
from app.schemas.outward import (
    OutwardBoxCreate,
    OutwardBoxResponse,
    OutwardPOResponse,
    OutwardScanCreate,
    OutwardScanCreateResponse,
    OutwardScanResponse,
)
from app.services import outward_service
from app.utils.request import get_client_ip

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/outward", tags=["outward"])


def _box_to_response(box: OutwardBox) -> OutwardBoxResponse:
    return OutwardBoxResponse(
        id=box.id,
        box_id=box.box_id,
        customer_id=box.customer_id,
        status=box.status,
        scans=[
            OutwardScanResponse(
                id=s.id,
                ean=s.ean,
                scan_result=s.scan_result,
                created_at=s.created_at,
            )
            for s in box.scans
        ],
        created_at=box.created_at,
        is_read_only=box.status == OutwardBoxStatusEnum.closed,
    )


@router.post("/pos", response_model=OutwardPOResponse, status_code=status.HTTP_201_CREATED)
async def upload_po_endpoint(
    request: Request,
    po_number: str = Form(...),
    customer_id: int = Form(...),
    file: UploadFile = ...,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> OutwardPOResponse:
    csv_bytes = await file.read()
    po = await outward_service.upload_po(
        db,
        customer_id=customer_id,
        organisation_id=current_user.organisation_id,
        uploaded_by=current_user.user_id,
        ip_address=get_client_ip(request),
        po_number=po_number,
        csv_bytes=csv_bytes,
    )
    # Eagerly load lines for response serialisation — lazy="raise" blocks post-commit access
    result = await db.execute(
        select(OutwardPO).options(selectinload(OutwardPO.lines)).where(OutwardPO.id == po.id)
    )
    po_with_lines = result.scalar_one()
    return po_with_lines


@router.post("/boxes", response_model=OutwardBoxResponse, status_code=status.HTTP_201_CREATED)
async def create_box_endpoint(
    body: OutwardBoxCreate,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> OutwardBoxResponse:
    box = await outward_service.create_box(
        db,
        customer_id=body.customer_id,
        organisation_id=current_user.organisation_id,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.get("/boxes/{box_id}", response_model=OutwardBoxResponse)
async def get_box_endpoint(
    box_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OutwardBoxResponse:
    box = await outward_service.get_box(
        db, box_id=box_id, organisation_id=current_user.organisation_id
    )
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    return _box_to_response(box)


@router.post("/boxes/{box_id}/close", response_model=OutwardBoxResponse)
async def close_box_endpoint(
    box_id: str,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> OutwardBoxResponse:
    box = await outward_service.close_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        closed_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.post(
    "/boxes/{box_id}/scans",
    response_model=OutwardScanCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_scan_endpoint(
    box_id: str,
    body: OutwardScanCreate,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> OutwardScanCreateResponse:
    scan, note = await outward_service.add_scan(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        ean=body.ean,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return OutwardScanCreateResponse(
        id=scan.id,
        ean=scan.ean,
        scan_result=scan.scan_result,
        created_at=scan.created_at,
        note=note,
    )


@router.delete("/scans/{scan_id}", response_model=OutwardScanResponse)
async def delete_scan_endpoint(
    scan_id: int,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> OutwardScanResponse:
    scan = await outward_service.delete_scan(
        db,
        scan_id=scan_id,
        organisation_id=current_user.organisation_id,
        deleted_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return OutwardScanResponse(
        id=scan.id,
        ean=scan.ean,
        scan_result=scan.scan_result,
        created_at=scan.created_at,
    )
```

- [ ] **Step 2: Register router in `app/main.py`**

Add import after the existing inward router import:
```python
from app.routers import outward as outward_router
```

Add include_router call after `app.include_router(inward_router.router)`:
```python
    app.include_router(outward_router.router)
```

- [ ] **Step 3: Verify app starts cleanly**

```bash
python -c "from app.main import app; print('routes:', [r.path for r in app.routes if hasattr(r, 'path') and '/outward' in r.path])"
```

Expected: list of outward route paths including `/api/outward/pos`, `/api/outward/boxes`, etc.

---

### Task 6: Integration Tests — PO Upload (`tests/integration/test_outward_po.py`)

**Files:**
- Create: `tests/integration/test_outward_po.py`

**Interfaces:**
- Consumes: `org`, `db`, `client`, `admin_user`, `packer_user`, `inward_operator_user` fixtures from `tests/conftest.py`
- Consumes: `OutwardPO` from `app.models.outward`

- [ ] **Step 1: Write `tests/integration/test_outward_po.py`**

```python
import io

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
    ).encode("utf-8")


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
```

- [ ] **Step 2: Run PO tests**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/hexalog_test pytest tests/integration/test_outward_po.py -v
```

Expected: 5 tests PASSED.

---

### Task 7: Integration Tests — Box Management (`tests/integration/test_outward_boxes.py`)

**Files:**
- Create: `tests/integration/test_outward_boxes.py`

**Interfaces:**
- Consumes: `org`, `db`, `client`, `packer_user` fixtures from `tests/conftest.py`
- Consumes: `OutwardBox`, `OutwardBoxStatusEnum` from `app.models.outward`

- [ ] **Step 1: Write `tests/integration/test_outward_boxes.py`**

```python
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


@pytest_asyncio.fixture
async def open_outward_box(db, org, customer) -> OutwardBox:
    box = OutwardBox(
        box_id="OB-TST-000099",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.open,
    )
    db.add(box)
    await db.flush()
    return box


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_create_outward_box_success(client, packer_user, org, customer):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/outward/boxes",
        json={"customer_id": customer.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["box_id"].startswith("OB-TST-")
    assert len(body["box_id"].split("-")[-1]) == 6  # 6-digit suffix
    assert body["status"] == "open"
    assert body["is_read_only"] is False
    assert body["scans"] == []


async def test_get_outward_box_success(client, packer_user, org, open_outward_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        f"/api/outward/boxes/{open_outward_box.box_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["box_id"] == open_outward_box.box_id
    assert body["scans"] == []
    assert body["is_read_only"] is False


async def test_get_outward_box_not_found(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.get(
        "/api/outward/boxes/OB-XXX-999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_close_outward_box_success(client, packer_user, org, open_outward_box):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{open_outward_box.box_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["is_read_only"] is True


async def test_close_outward_box_already_closed(client, packer_user, org, db, customer):
    box = OutwardBox(
        box_id="OB-TST-000098",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.closed,
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/outward/boxes/{box.box_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "closed" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run box tests**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/hexalog_test pytest tests/integration/test_outward_boxes.py -v
```

Expected: 5 tests PASSED.

---

### Task 8: Integration Tests — Scans (`tests/integration/test_outward_scans.py`)

**Files:**
- Create: `tests/integration/test_outward_scans.py`

**Interfaces:**
- Consumes: `org`, `db`, `client`, `packer_user` fixtures from `tests/conftest.py`
- Consumes: `OutwardPO`, `OutwardPOLine`, `OutwardBox`, `OutwardScan` from Task 1

- [ ] **Step 1: Write `tests/integration/test_outward_scans.py`**

```python
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
async def open_box(db, org, customer) -> OutwardBox:
    box = OutwardBox(
        box_id="OB-TST-099001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=OutwardBoxStatusEnum.open,
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
```

- [ ] **Step 2: Run scan tests**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/hexalog_test pytest tests/integration/test_outward_scans.py -v
```

Expected: 6 tests PASSED.

---

### Task 9: Lint + Full Regression + Commit

**Files:** No new files; fixes only.

- [ ] **Step 1: Run ruff on new files**

```bash
ruff check app/models/outward.py app/schemas/outward.py app/services/outward_service.py app/routers/outward.py
```

Expected: no errors. Fix any that appear (common: line > 100 chars, unused imports).

- [ ] **Step 2: Run full integration test suite**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/hexalog_test pytest tests/integration/ -v
```

Expected: all existing tests still pass + 16 new tests pass.

- [ ] **Step 3: Commit**

```bash
git add app/models/outward.py app/models/__init__.py app/schemas/outward.py app/services/outward_service.py app/routers/outward.py app/main.py alembic/versions/ tests/integration/test_outward_po.py tests/integration/test_outward_boxes.py tests/integration/test_outward_scans.py
git commit -m "feat(outward): implement outward module — PO upload, box management, FIFO scanning with over-pack prevention"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|-----------------|------|
| `app/models/outward.py` — 4 models | Task 1 |
| `app/models/__init__.py` update | Task 1 |
| Migration with DO blocks + `create_type=False` + 3 indexes | Task 2 |
| `app/schemas/outward.py` | Task 3 |
| `upload_po` service function | Task 4 |
| `create_box` service (OB- prefix, no packer guard) | Task 4 |
| `get_box` service | Task 4 |
| `close_box` service (open or in_use → closed) | Task 4 |
| `add_scan` service (FIFO, status open→in_use, -1 ledger, note) | Task 4 |
| `delete_scan` service (+1 ledger reversal, decrement packed_qty) | Task 4 |
| Router with 6 endpoints + role enforcement | Task 5 |
| Register router in `app/main.py` | Task 5 |
| `test_outward_po.py` — 5 tests | Task 6 |
| `test_outward_boxes.py` — 5 tests | Task 7 |
| `test_outward_scans.py` — 6 tests | Task 8 |
| Ruff lint + full regression | Task 9 |

### Type consistency check

- `OutwardBox.scans` relationship uses `foreign_keys="[OutwardScan.outward_box_id]"` — matches `OutwardScan.outward_box_id` FK defined in the same model file.
- `_next_counter` imported from `inward_service` — exact same function signature, reused directly.
- `InventoryLedgerEntry` imported from `app.models.inward` — correct, no outward ledger table.
- `add_scan` returns `tuple[OutwardScan, str | None]` — router destructures as `scan, note`.
- `_box_to_response` builds `OutwardScanResponse` objects from `box.scans` — all fields (`id`, `ean`, `scan_result`, `created_at`) exist on `OutwardScan`.
- `close_box` endpoint has no body — matches service signature (no `physical_qty`).

### No placeholder check

All steps contain actual code. No "TBD" or "implement later" strings present.
