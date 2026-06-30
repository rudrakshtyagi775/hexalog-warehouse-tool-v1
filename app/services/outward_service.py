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
        after_data={
            "po_number": po_number,
            "customer_id": customer_id,
            "line_count": len(lines_data),
        },
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
    await db.flush()

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
        resource_id=scan.id,
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
        select(OutwardBox).where(
            OutwardBox.id == scan.outward_box_id,
            OutwardBox.organisation_id == organisation_id,
        )
    )
    box = box_result.scalar_one()

    if box.status == OutwardBoxStatusEnum.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete scans from a closed box",
        )

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
