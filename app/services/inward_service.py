import csv
import io
from datetime import datetime, timezone

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    InwardBox,
    InventoryLedgerEntry,
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

    # 2. Validate customer belongs to this organisation (prevents cross-tenant IDOR)
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

    # 5. Create InwardPO
    po = InwardPO(
        organisation_id=organisation_id,
        customer_id=customer_id,
        po_number=po_number,
        status=InwardReferenceStatusEnum.open,
        uploaded_by=uploaded_by,
    )
    db.add(po)
    await db.flush()  # populates po.id

    # 6. Create InwardPOLine rows
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
            detail="An inward already exists for this PO/Invoice. Continue adding boxes to it?",
        )
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
        # Advisory lock serialises concurrent create_box calls for the same packer,
        # eliminating the SELECT-then-INSERT race where two requests both see no
        # active box and both proceed to create one.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:uid)"), {"uid": created_by}
        )
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
    await db.flush()  # populates box.id before writing the audit log

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="box_created",
        resource_type="inward_box",
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

    # 6. Check for "no recorded inward stock" note.
    # Show the note when no positive inward_submission ledger entries exist
    # for this EAN/customer (i.e., the item has never been received via a
    # completed inward box submission).
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
