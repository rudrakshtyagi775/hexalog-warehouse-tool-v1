import csv
import io

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    AuditModuleEnum,
    CounterTypeEnum,
    InwardBoxStatusEnum,
    InwardReferenceStatusEnum,
    UserRoleEnum,
)
from app.models.inward import (
    InwardBox,
    InwardPO,
    InwardPOLine,
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

    # Reload with scans eager-loaded — refresh alone resets lazy="raise"
    reloaded = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(InwardBox.id == box.id)
    )
    return reloaded.scalar_one()
