import csv
import io

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AuditModuleEnum,
    InwardReferenceStatusEnum,
)
from app.models.inward import (
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
