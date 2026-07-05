import csv
import io
from datetime import UTC, date, datetime

import structlog
from fastapi import HTTPException, status
from sqlalchemy import func as sql_func
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
    InventoryLedgerEntry,
    InwardBox,
    InwardPO,
    InwardPOLine,
    InwardReference,
    InwardScan,
)
from app.schemas.inward import (
    InwardBoxListResponse,
    InwardBoxSummary,
    InwardReferenceListResponse,
    InwardReferenceSummary,
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
            INSERT INTO counters
                (counter_type, organisation_id, customer_code, date_key, last_value)
            VALUES (:ct, :org, :code, :dk, 1)
            ON CONFLICT (counter_type, organisation_id, customer_code, date_key)
            DO UPDATE SET last_value = counters.last_value + 1
            RETURNING last_value
        """),
        {"ct": counter_type.value, "org": organisation_id, "code": customer_code, "dk": date_key},
    )
    return result.scalar_one()


# ── InwardReference: get-or-create ───────────────────────────────────────────

async def get_or_create_reference(
    db: AsyncSession,
    *,
    organisation_id: int,
    customer_id: int,
    po_number: str | None,
    invoice_number: str | None,
    created_by: int,
    ip_address: str | None,
) -> tuple[InwardReference, bool]:
    """Find an existing open reference for this customer + PO/invoice, or create one.

    Returns (reference, is_duplicate). A duplicate never blocks (IN-3) — the
    caller surfaces the PRD confirm-dialog string and lets the user append or
    cancel.
    """
    from app.models.customer import Customer

    cust_result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.organisation_id == organisation_id,
        )
    )
    if cust_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    dup_where = [
        InwardReference.organisation_id == organisation_id,
        InwardReference.customer_id == customer_id,
        InwardReference.status == InwardReferenceStatusEnum.open,
    ]
    match_conditions = []
    if po_number:
        match_conditions.append(InwardReference.po_number == po_number)
    if invoice_number:
        match_conditions.append(InwardReference.invoice_number == invoice_number)

    existing = None
    if match_conditions:
        from sqlalchemy import or_

        existing_result = await db.execute(
            select(InwardReference).where(*dup_where, or_(*match_conditions))
        )
        existing = existing_result.scalars().first()

    if existing is not None:
        return existing, True

    reference = InwardReference(
        organisation_id=organisation_id,
        customer_id=customer_id,
        po_number=po_number,
        invoice_number=invoice_number,
        status=InwardReferenceStatusEnum.open,
        created_by=created_by,
    )
    db.add(reference)
    await db.flush()

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="reference_created",
        resource_type="inward_reference",
        resource_id=reference.id,
        user_id=created_by,
        organisation_id=organisation_id,
        after_data={
            "customer_id": customer_id,
            "po_number": po_number,
            "invoice_number": invoice_number,
        },
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(reference)
    return reference, False


# ── InwardReference: list ─────────────────────────────────────────────────────

async def list_references(
    db: AsyncSession,
    *,
    org_id: int,
    customer_id: int | None = None,
    status_filter: InwardReferenceStatusEnum | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InwardReferenceListResponse:
    from app.models.customer import Customer

    base_where = [InwardReference.organisation_id == org_id]
    if customer_id is not None:
        base_where.append(InwardReference.customer_id == customer_id)
    if status_filter is not None:
        base_where.append(InwardReference.status == status_filter)

    total = await db.scalar(
        select(sql_func.count()).select_from(InwardReference).where(*base_where)
    ) or 0

    result = await db.execute(
        select(InwardReference, Customer.name.label("customer_name"))
        .join(Customer, InwardReference.customer_id == Customer.id)
        .where(*base_where)
        .order_by(InwardReference.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = result.all()

    return InwardReferenceListResponse(
        items=[
            InwardReferenceSummary(
                id=ref.id,
                customer_id=ref.customer_id,
                customer_name=customer_name,
                po_number=ref.po_number,
                invoice_number=ref.invoice_number,
                status=ref.status,
                created_at=ref.created_at,
            )
            for ref, customer_name in rows
        ],
        total=total,
    )


# ── InwardReference: finish delivery ─────────────────────────────────────────

async def finish_reference(
    db: AsyncSession,
    *,
    reference_id: int,
    organisation_id: int,
    finished_by: int,
    ip_address: str | None,
) -> InwardReference:
    """IN-11: mark a reference Completed. No new boxes accepted after this."""
    result = await db.execute(
        select(InwardReference).where(
            InwardReference.id == reference_id,
            InwardReference.organisation_id == organisation_id,
        )
    )
    reference = result.scalar_one_or_none()
    if reference is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reference not found")
    if reference.status == InwardReferenceStatusEnum.completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Reference is already completed."
        )

    reference.status = InwardReferenceStatusEnum.completed
    reference.completed_at = datetime.now(UTC)

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="reference_finished",
        resource_type="inward_reference",
        resource_id=reference.id,
        user_id=finished_by,
        organisation_id=organisation_id,
        before_data={"status": "open"},
        after_data={"status": "completed"},
        ip_address=ip_address,
    )

    await db.commit()
    await db.refresh(reference)
    return reference


# ── Box: create ───────────────────────────────────────────────────────────────

async def create_box(
    db: AsyncSession,
    *,
    customer_id: int,
    organisation_id: int,
    created_by: int,
    user_roles: list[UserRoleEnum],
    ip_address: str | None,
    inward_reference_id: int | None = None,
    box_number: str | None = None,
) -> InwardBox:
    # 1. Single-active-box guard (admin bypasses)
    is_non_admin = UserRoleEnum.admin not in user_roles
    if is_non_admin:
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

    # 2b. Validate reference (if given) and box_number uniqueness within it (IN-4)
    if inward_reference_id is not None:
        ref_result = await db.execute(
            select(InwardReference).where(
                InwardReference.id == inward_reference_id,
                InwardReference.organisation_id == organisation_id,
            )
        )
        reference = ref_result.scalar_one_or_none()
        if reference is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Inward reference not found"
            )
        if reference.status == InwardReferenceStatusEnum.completed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Delivery is already finished; no new boxes are accepted.",
            )
        if box_number:
            dup_result = await db.execute(
                select(InwardBox).where(
                    InwardBox.inward_reference_id == inward_reference_id,
                    InwardBox.box_number == box_number,
                    InwardBox.is_deleted == False,  # noqa: E712
                )
            )
            if dup_result.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Box number already used for this PO/Invoice. "
                    "Enter a different box number.",
                )

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
        inward_reference_id=inward_reference_id,
        box_number=box_number,
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
        after_data={
            "box_id": box_id,
            "customer_id": customer_id,
            "inward_reference_id": inward_reference_id,
            "box_number": box_number,
        },
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
            detail=(
                "Scanned Quantity and Physical Quantity do not match."
                " Please verify before submission."
            ),
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


# ── Box: submit ───────────────────────────────────────────────────────────────

async def submit_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    submitted_by: int,
    ip_address: str | None,
) -> InwardBox:
    """Transition pending_verification → completed. Generates inscan_number,
    writes one +1 InventoryLedgerEntry per active scan, and writes an audit row.
    """
    from zoneinfo import ZoneInfo

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
            detail=(
                f"Box must be in 'pending_verification' status to submit "
                f"(current: {box.status.value})"
            ),
        )

    # Load customer code for inscan_number format: INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>
    cust_result = await db.execute(
        select(Customer).where(Customer.id == box.customer_id)
    )
    customer = cust_result.scalar_one()

    tz = ZoneInfo(settings.APP_TIMEZONE)
    today = datetime.now(tz).strftime("%Y%m%d")
    n = await _next_counter(
        db,
        counter_type=CounterTypeEnum.inscan_number,
        organisation_id=organisation_id,
        customer_code=customer.code,
        date_key=today,
    )
    inscan_number = f"INS-{customer.code}-{today}-{n:04d}"

    # One +1 ledger entry per active scan
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

    box.status = InwardBoxStatusEnum.completed
    box.inscan_number = inscan_number
    box.submitted_at = datetime.now(UTC)
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
            "ledger_entries": len(active_scans),
        },
        ip_address=ip_address,
    )

    await db.commit()

    # Reload with scans eager-loaded (lazy="raise" blocks post-commit attribute access)
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
    is_manual_entry: bool = False,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="EAN not found in open POs"
        )

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
        is_manual_entry=is_manual_entry,
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
    scan.deleted_at = datetime.now(UTC)
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


# ── Box: list (history) ───────────────────────────────────────────────────────

async def list_boxes(
    db: AsyncSession,
    *,
    org_id: int,
    status_filter: InwardBoxStatusEnum | None = None,
    customer_id: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InwardBoxListResponse:
    from app.models.customer import Customer

    base_where = [
        InwardBox.organisation_id == org_id,
        InwardBox.is_deleted == False,  # noqa: E712
    ]
    if status_filter is not None:
        base_where.append(InwardBox.status == status_filter)
    if customer_id is not None:
        base_where.append(InwardBox.customer_id == customer_id)
    if from_date is not None:
        from_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=UTC)
        base_where.append(InwardBox.created_at >= from_dt)
    if to_date is not None:
        from datetime import timedelta
        to_dt = datetime(to_date.year, to_date.month, to_date.day, tzinfo=UTC)
        base_where.append(InwardBox.created_at < to_dt + timedelta(days=1))

    total = await db.scalar(
        select(sql_func.count()).select_from(InwardBox).where(*base_where)
    ) or 0

    result = await db.execute(
        select(InwardBox, Customer.name.label("customer_name"))
        .join(Customer, InwardBox.customer_id == Customer.id)
        .where(*base_where)
        .order_by(InwardBox.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = result.all()

    return InwardBoxListResponse(
        items=[
            InwardBoxSummary(
                box_id=box.box_id,
                customer_name=customer_name,
                status=box.status,
                scanned_qty=box.scanned_qty,
                inscan_number=box.inscan_number,
                created_at=box.created_at,
                submitted_at=box.submitted_at,
            )
            for box, customer_name in rows
        ],
        total=total,
    )


# ── Box: reopen ───────────────────────────────────────────────────────────────

async def reopen_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    reopened_by: int,
    ip_address: str | None,
) -> InwardBox:
    """Reset a pending_verification box back to scanning. Clears physical_qty."""
    result = await db.execute(
        select(InwardBox).where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    if box.status != InwardBoxStatusEnum.pending_verification:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Box can only be reopened from pending verification state.",
        )

    old_physical_qty = box.physical_qty
    box.status = InwardBoxStatusEnum.scanning
    box.physical_qty = None

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="box_reopened",
        resource_type="inward_box",
        resource_id=box.id,
        user_id=reopened_by,
        organisation_id=organisation_id,
        before_data={"status": "pending_verification", "physical_qty": old_physical_qty},
        after_data={"status": "scanning"},
        ip_address=ip_address,
    )

    await db.commit()

    # Reload with scans for router response
    reloaded = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(InwardBox.id == box.id)
    )
    return reloaded.scalar_one()
