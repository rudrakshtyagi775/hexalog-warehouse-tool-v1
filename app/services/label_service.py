import base64
import io

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuditModuleEnum
from app.models.outward import OutwardBox
from app.services.audit_service import write_audit_log

# A6 / 4x6in thermal target (OUT-7). 1in = 96px at CSS reference DPI.
_LABEL_PAGE_CSS = """
    @page { size: 105mm 148mm; margin: 8mm; }
    body { font-family: sans-serif; text-align: center; }
    .label { page-break-after: always; }
    .label:last-child { page-break-after: auto; }
    .box-id { font-size: 28pt; font-weight: bold; margin-top: 12mm; word-break: break-all; }
    .customer { font-size: 12pt; color: #444; margin-top: 4mm; }
    .qr { margin-top: 8mm; }
    .qr img { width: 55mm; height: 55mm; }
"""


def _qr_data_uri(data: str) -> str:
    """Render `data` as a QR code PNG data URI. Raises HTTPException 503 if the
    qrcode dependency is unavailable in this environment."""
    try:
        import qrcode
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Label rendering is unavailable: qrcode dependency not installed.",
        ) from exc

    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_label_pdf(boxes: list[tuple[str, str]]) -> bytes:
    """Render one A6 label per (box_id, customer_name) pair into a single PDF.

    Each label shows the human-readable Box ID plus a QR code encoding it
    (OUT-7). Raises HTTPException 503 if WeasyPrint's native dependencies
    (Pango/GObject) are not installed on this host.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Label PDF rendering is unavailable on this server "
            "(missing WeasyPrint native dependencies).",
        ) from exc

    labels_html = "".join(
        f"""
        <div class="label">
            <div class="customer">{customer_name}</div>
            <div class="box-id">{box_id}</div>
            <div class="qr"><img src="{_qr_data_uri(box_id)}" alt="{box_id}"></div>
        </div>
        """
        for box_id, customer_name in boxes
    )
    html = f"<html><head><style>{_LABEL_PAGE_CSS}</style></head><body>{labels_html}</body></html>"
    return HTML(string=html).write_pdf()


async def increment_print_count(
    db: AsyncSession,
    *,
    box_id: str,
    org_id: int,
    user_id: int,
    ip_address: str | None,
) -> OutwardBox:
    result = await db.execute(
        select(OutwardBox).where(
            OutwardBox.box_id == box_id,
            OutwardBox.organisation_id == org_id,
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")

    box.print_count += 1

    await write_audit_log(
        db,
        module=AuditModuleEnum.outward,
        action="label_reprinted",
        resource_type="outward_box",
        resource_id=box.id,
        user_id=user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={"box_id": box_id, "new_print_count": box.print_count},
    )

    await db.commit()
    await db.refresh(box)
    return box
