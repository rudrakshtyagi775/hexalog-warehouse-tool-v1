import csv
import io
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func as sql_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.enums import AuditModuleEnum, InwardBoxStatusEnum, OutwardScanResultEnum
from app.models.inward import InventoryLedgerEntry, InwardBox, InwardReference, InwardScan
from app.models.outward import OutwardBox, OutwardPO, OutwardPOLine, OutwardScan
from app.models.user import User
from app.schemas.admin import (
    InscanReportResponse,
    InscanReportRow,
    ItemPackingReportResponse,
    ItemPackingReportRow,
    OutwardPOReportResponse,
    OutwardPOReportRow,
    VarianceReportResponse,
    VarianceReportRow,
)
from app.services.audit_service import write_audit_log


def _date_range_utc(from_date: date, to_date: date):
    """Return (start_dt, end_dt) in UTC for inclusive date range."""
    start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=UTC)
    end = datetime(to_date.year, to_date.month, to_date.day, tzinfo=UTC) + timedelta(days=1)
    return start, end


def _rows_to_csv(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


async def get_outward_po_report(
    db: AsyncSession,
    *,
    org_id: int,
    from_date: date,
    to_date: date,
    customer_id: int | None,
    fmt: str,
    page: int,
    page_size: int,
    user_id: int,
    ip_address: str | None,
) -> OutwardPOReportResponse | bytes:
    start, end = _date_range_utc(from_date, to_date)

    base_where = [
        OutwardPO.organisation_id == org_id,
        OutwardPO.uploaded_at >= start,
        OutwardPO.uploaded_at < end,
    ]
    if customer_id is not None:
        base_where.append(OutwardPO.customer_id == customer_id)

    total = await db.scalar(
        select(sql_func.count())
        .select_from(OutwardPOLine)
        .join(OutwardPO, OutwardPOLine.outward_po_id == OutwardPO.id)
        .where(*base_where)
    ) or 0

    q = (
        select(
            OutwardPO.po_number,
            Customer.name.label("customer_name"),
            OutwardPOLine.ean,
            OutwardPOLine.description,
            OutwardPOLine.ordered_qty,
            OutwardPOLine.packed_qty,
            OutwardPO.status,
            OutwardPO.uploaded_at,
        )
        .join(OutwardPOLine, OutwardPOLine.outward_po_id == OutwardPO.id)
        .join(Customer, OutwardPO.customer_id == Customer.id)
        .where(*base_where)
        .order_by(OutwardPO.uploaded_at.desc(), OutwardPO.id, OutwardPOLine.ean)
    )

    if fmt == "json":
        q = q.limit(page_size).offset((page - 1) * page_size)

    result = await db.execute(q)
    db_rows = result.all()

    await write_audit_log(
        db,
        module=AuditModuleEnum.reports,
        action="report_downloaded",
        resource_type="report",
        resource_id=None,
        user_id=user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={
            "report_type": "outward_po",
            "from_date": str(from_date),
            "to_date": str(to_date),
            "format": fmt,
            "row_count": len(db_rows),
        },
    )
    await db.commit()

    if fmt == "csv":
        headers = ["PO Number", "Customer", "EAN", "Description", "Ordered Qty", "Packed Qty",
                   "Remaining", "Status", "Upload Date"]
        rows = [
            [r.po_number, r.customer_name, r.ean, r.description or "",
             r.ordered_qty, r.packed_qty, r.ordered_qty - r.packed_qty,
             r.status.value, r.uploaded_at.isoformat()]
            for r in db_rows
        ]
        return _rows_to_csv(headers, rows)

    items = [
        OutwardPOReportRow(
            po_number=r.po_number,
            customer_name=r.customer_name,
            ean=r.ean,
            description=r.description,
            ordered_qty=r.ordered_qty,
            packed_qty=r.packed_qty,
            remaining=r.ordered_qty - r.packed_qty,
            status=r.status,
            uploaded_at=r.uploaded_at,
        )
        for r in db_rows
    ]
    return OutwardPOReportResponse(
        items=items, total=total, from_date=from_date, to_date=to_date
    )


async def get_item_packing_report(
    db: AsyncSession,
    *,
    org_id: int,
    from_date: date,
    to_date: date,
    customer_id: int | None,
    fmt: str,
    page: int,
    page_size: int,
    user_id: int,
    ip_address: str | None,
) -> ItemPackingReportResponse | bytes:
    start, end = _date_range_utc(from_date, to_date)

    base_where = [
        OutwardScan.organisation_id == org_id,
        OutwardScan.created_at >= start,
        OutwardScan.created_at < end,
    ]
    if customer_id is not None:
        base_where.append(
            OutwardBox.customer_id == customer_id
        )

    total = await db.scalar(
        select(sql_func.count())
        .select_from(OutwardScan)
        .join(OutwardBox, OutwardScan.outward_box_id == OutwardBox.id)
        .where(*base_where)
    ) or 0

    q = (
        select(
            OutwardScan.created_at,
            User.full_name.label("user_name"),
            OutwardBox.box_id,
            OutwardScan.ean,
            OutwardScan.scan_result,
            OutwardPO.po_number.label("allocated_po"),
            OutwardScan.reject_reason,
            OutwardScan.stock_flagged,
        )
        .join(OutwardBox, OutwardScan.outward_box_id == OutwardBox.id)
        .outerjoin(User, OutwardScan.created_by == User.id)
        .outerjoin(OutwardPOLine, OutwardScan.outward_po_line_id == OutwardPOLine.id)
        .outerjoin(OutwardPO, OutwardPOLine.outward_po_id == OutwardPO.id)
        .where(*base_where)
        .order_by(OutwardScan.created_at.desc())
    )

    if fmt == "json":
        q = q.limit(page_size).offset((page - 1) * page_size)

    result = await db.execute(q)
    db_rows = result.all()

    await write_audit_log(
        db,
        module=AuditModuleEnum.reports,
        action="report_downloaded",
        resource_type="report",
        resource_id=None,
        user_id=user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={
            "report_type": "item_packing",
            "from_date": str(from_date),
            "to_date": str(to_date),
            "format": fmt,
            "row_count": len(db_rows),
        },
    )
    await db.commit()

    if fmt == "csv":
        headers = ["Timestamp", "User", "Box ID", "EAN", "Result",
                   "Allocated PO", "Reject Reason", "Stock Flagged"]
        rows = [
            [r.created_at.isoformat(), r.user_name or "", r.box_id, r.ean,
             r.scan_result.value, r.allocated_po or "", r.reject_reason or "",
             str(r.stock_flagged)]
            for r in db_rows
        ]
        return _rows_to_csv(headers, rows)

    items = [
        ItemPackingReportRow(
            timestamp=r.created_at,
            user_name=r.user_name,
            box_id=r.box_id,
            ean=r.ean,
            scan_result=r.scan_result,
            allocated_po=r.allocated_po,
            reject_reason=r.reject_reason,
            stock_flagged=r.stock_flagged,
        )
        for r in db_rows
    ]
    return ItemPackingReportResponse(
        items=items, total=total, from_date=from_date, to_date=to_date
    )


async def get_variance_report(
    db: AsyncSession,
    *,
    org_id: int,
    from_date: date,
    to_date: date,
    customer_id: int | None,
    fmt: str,
    page: int,
    page_size: int,
    user_id: int,
    ip_address: str | None,
) -> VarianceReportResponse | bytes:
    start, end = _date_range_utc(from_date, to_date)

    cust_where = [InventoryLedgerEntry.organisation_id == org_id]
    if customer_id is not None:
        cust_where.append(InventoryLedgerEntry.customer_id == customer_id)

    # Aggregate ledger: balance per (customer, ean)
    ledger_q = (
        select(
            InventoryLedgerEntry.customer_id,
            InventoryLedgerEntry.ean,
            sql_func.sum(InventoryLedgerEntry.quantity_change).label("balance"),
            sql_func.max(InventoryLedgerEntry.created_at).label("last_movement"),
        )
        .where(*cust_where)
        .group_by(InventoryLedgerEntry.customer_id, InventoryLedgerEntry.ean)
    )
    ledger_result = await db.execute(ledger_q)
    ledger_rows = {(r.customer_id, r.ean): r for r in ledger_result.all()}

    # Aggregate accepted outward scans per (customer, ean) for stock_flagged count
    flag_where = [
        OutwardScan.organisation_id == org_id,
        OutwardScan.scan_result == OutwardScanResultEnum.accepted,
        OutwardScan.stock_flagged == True,  # noqa: E712
    ]
    if customer_id is not None:
        flag_where.append(OutwardBox.customer_id == customer_id)

    flag_q = (
        select(
            OutwardBox.customer_id,
            OutwardScan.ean,
            sql_func.count().label("flagged_count"),
        )
        .join(OutwardBox, OutwardScan.outward_box_id == OutwardBox.id)
        .where(*flag_where)
        .group_by(OutwardBox.customer_id, OutwardScan.ean)
    )
    flag_result = await db.execute(flag_q)
    flag_counts = {(r.customer_id, r.ean): r.flagged_count for r in flag_result.all()}

    # Build variance rows from ledger aggregation
    items_data: list[dict] = []
    for (cid, ean), lr in ledger_rows.items():
        balance = int(lr.balance or 0)
        inward_qty = max(balance, 0)
        outward_qty = max(-balance, 0)
        flagged = flag_counts.get((cid, ean), 0)
        items_data.append({
            "customer_id": cid,
            "ean": ean,
            "total_inward_qty": inward_qty,
            "total_outward_qty": outward_qty,
            "current_balance": balance,
            "stock_flagged_count": flagged,
            "last_movement_date": lr.last_movement,
        })

    # Resolve customer names
    cust_ids = {d["customer_id"] for d in items_data}
    cust_names: dict[int, str] = {}
    if cust_ids:
        cust_result = await db.execute(
            select(Customer.id, Customer.name).where(Customer.id.in_(cust_ids))
        )
        cust_names = {r.id: r.name for r in cust_result.all()}

    total = len(items_data)

    # Apply date range filter on last_movement_date
    items_data = [
        d for d in items_data
        if d["last_movement_date"] and start <= d["last_movement_date"] < end
    ]

    # Sort and paginate
    epoch = datetime.min.replace(tzinfo=UTC)
    items_data.sort(key=lambda d: d["last_movement_date"] or epoch, reverse=True)

    await write_audit_log(
        db,
        module=AuditModuleEnum.reports,
        action="report_downloaded",
        resource_type="report",
        resource_id=None,
        user_id=user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={
            "report_type": "variance",
            "from_date": str(from_date),
            "to_date": str(to_date),
            "format": fmt,
            "row_count": len(items_data),
        },
    )
    await db.commit()

    if fmt == "json":
        items_data = items_data[(page - 1) * page_size: page * page_size]

    if fmt == "csv":
        headers = ["Customer", "EAN", "Total Inward Qty", "Total Outward Qty",
                   "Current Balance", "Stock Flagged Count", "Last Movement Date"]
        rows = [
            [cust_names.get(d["customer_id"], ""), d["ean"],
             d["total_inward_qty"], d["total_outward_qty"],
             d["current_balance"], d["stock_flagged_count"],
             d["last_movement_date"].isoformat() if d["last_movement_date"] else ""]
            for d in items_data
        ]
        return _rows_to_csv(headers, rows)

    return VarianceReportResponse(
        items=[
            VarianceReportRow(
                customer_name=cust_names.get(d["customer_id"], ""),
                ean=d["ean"],
                total_inward_qty=d["total_inward_qty"],
                total_outward_qty=d["total_outward_qty"],
                current_balance=d["current_balance"],
                stock_flagged_count=d["stock_flagged_count"],
                last_movement_date=d["last_movement_date"],
            )
            for d in items_data
        ],
        total=total,
        from_date=from_date,
        to_date=to_date,
    )


async def get_inscan_report(
    db: AsyncSession,
    *,
    org_id: int,
    from_date: date,
    to_date: date,
    customer_id: int | None,
    fmt: str,
    page: int,
    page_size: int,
    user_id: int,
    ip_address: str | None,
) -> InscanReportResponse | bytes:
    """R-1: Customer-wise Inscan Report. Row grain: EAN per completed inward box."""
    start, end = _date_range_utc(from_date, to_date)

    base_where = [
        InwardBox.organisation_id == org_id,
        InwardBox.status == InwardBoxStatusEnum.completed,
        InwardBox.submitted_at >= start,
        InwardBox.submitted_at < end,
    ]
    if customer_id is not None:
        base_where.append(InwardBox.customer_id == customer_id)

    box_result = await db.execute(
        select(
            InwardBox.id,
            InwardBox.inscan_number,
            InwardBox.box_number,
            InwardBox.physical_qty,
            InwardBox.scanned_qty,
            InwardBox.submitted_at,
            Customer.name.label("customer_name"),
            User.full_name.label("user_name"),
            InwardReference.po_number,
            InwardReference.invoice_number,
        )
        .join(Customer, InwardBox.customer_id == Customer.id)
        .outerjoin(User, InwardBox.submitted_by == User.id)
        .outerjoin(InwardReference, InwardBox.inward_reference_id == InwardReference.id)
        .where(*base_where)
        .order_by(InwardBox.submitted_at.desc(), InwardBox.id)
    )
    boxes = box_result.all()

    box_ids = [b.id for b in boxes]
    ean_counts: dict[int, dict[str, int]] = {}
    if box_ids:
        ean_result = await db.execute(
            select(
                InwardScan.inward_box_id,
                InwardScan.ean,
                sql_func.count().label("scanned_qty"),
            )
            .where(InwardScan.inward_box_id.in_(box_ids), InwardScan.is_deleted == False)  # noqa: E712
            .group_by(InwardScan.inward_box_id, InwardScan.ean)
        )
        for row in ean_result.all():
            ean_counts.setdefault(row.inward_box_id, {})[row.ean] = row.scanned_qty

    all_rows: list[InscanReportRow] = []
    for b in boxes:
        box_variance = b.scanned_qty - (b.physical_qty or 0)
        for ean, ean_qty in ean_counts.get(b.id, {}).items():
            all_rows.append(InscanReportRow(
                inscan_number=b.inscan_number,
                customer_name=b.customer_name,
                po_number=b.po_number,
                invoice_number=b.invoice_number,
                box_number=b.box_number,
                ean=ean,
                scanned_qty=ean_qty,
                physical_qty=b.physical_qty,
                variance=box_variance,
                date=b.submitted_at,
                user_name=b.user_name,
            ))

    total = len(all_rows)
    paged_rows = all_rows[(page - 1) * page_size: page * page_size] if fmt == "json" else all_rows

    await write_audit_log(
        db,
        module=AuditModuleEnum.reports,
        action="report_downloaded",
        resource_type="report",
        resource_id=None,
        user_id=user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={
            "report_type": "inscan",
            "from_date": str(from_date),
            "to_date": str(to_date),
            "format": fmt,
            "row_count": len(paged_rows),
        },
    )
    await db.commit()

    if fmt == "csv":
        headers = ["Inscan Number", "Customer", "PO Number", "Invoice Number", "Box Number",
                   "EAN", "Scanned Qty", "Physical Qty", "Variance", "Date", "User"]
        rows = [
            [r.inscan_number, r.customer_name, r.po_number or "", r.invoice_number or "",
             r.box_number or "", r.ean, r.scanned_qty, r.physical_qty or "", r.variance,
             r.date.isoformat(), r.user_name or ""]
            for r in paged_rows
        ]
        return _rows_to_csv(headers, rows)

    return InscanReportResponse(
        items=paged_rows, total=total, from_date=from_date, to_date=to_date
    )
