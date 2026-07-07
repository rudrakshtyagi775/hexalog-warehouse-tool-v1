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
    body { font-family: sans-serif; text-align: center; margin: 0; }
    .label { page-break-after: always; }
    .label:last-child { page-break-after: auto; }
    .title { font-size: 10pt; font-weight: bold; letter-spacing: 2px; color: #666; margin-top: 2mm }
    .box-id { font-size: 28pt; font-weight: bold; margin-top: 6mm; word-break: break-all; }
    .customer { font-size: 12pt; color: #444; margin-top: 4mm; }
    .meta { font-size: 9pt; color: #666; margin-top: 2mm; }
    .qr { margin-top: 6mm; }
    .qr img { width: 45mm; height: 45mm; }
    .barcode { margin-top: 4mm; }
    .barcode img { width: 70mm; height: 20mm; }
    .box-id-repeat { font-size: 11pt; font-weight: bold; margin-top: 2mm; word-break: break-all; }
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


def _barcode_data_uri(data: str) -> str:
    """Render `data` as a Code128 barcode PNG data URI. Raises HTTPException 503
    if the python-barcode dependency is unavailable in this environment."""
    try:
        import barcode
        from barcode.writer import ImageWriter
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Label rendering is unavailable: python-barcode dependency not installed.",
        ) from exc

    code = barcode.get("code128", data, writer=ImageWriter())
    buf = io.BytesIO()
    code.write(buf, options={"write_text": False, "module_height": 12.0, "quiet_zone": 2.0})
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _parse_box_id(box_id: str) -> tuple[str, str]:
    """Split a Box ID (<prefix>-<customer code>-<counter>) into (code, counter)
    for display as distinct label fields, without assuming a specific prefix."""
    parts = box_id.split("-")
    if len(parts) < 3:
        return "", ""
    return parts[-2], parts[-1]


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

    def _render_one(box_id: str, customer_name: str) -> str:
        code, counter = _parse_box_id(box_id)
        return f"""
        <div class="label">
            <div class="title">BOX LABEL</div>
            <div class="box-id">{box_id}</div>
            <div class="customer">{customer_name}</div>
            <div class="meta">Org/Client Code: {code} &nbsp;&middot;&nbsp; Counter: {counter}</div>
            <div class="qr"><img src="{_qr_data_uri(box_id)}" alt="{box_id}"></div>
            <div class="barcode"><img src="{_barcode_data_uri(box_id)}" alt="{box_id}"></div>
            <div class="box-id-repeat">{box_id}</div>
        </div>
        """

    labels_html = "".join(_render_one(box_id, customer_name) for box_id, customer_name in boxes)
    html = f"<html><head><style>{_LABEL_PAGE_CSS}</style></head><body>{labels_html}</body></html>"
    return HTML(string=html).write_pdf()


async def audit_label_download(
    db: AsyncSession,
    *,
    box_ids: list[str],
    org_id: int,
    user_id: int,
    ip_address: str | None,
) -> None:
    """Audit every label PDF fetch (PRD §10: 'every print/reprint'). Does not
    touch print_count — that stays exactly as generate/reprint already manage it.
    """
    await write_audit_log(
        db,
        module=AuditModuleEnum.outward,
        action="label_printed",
        resource_type="outward_box",
        resource_id=None,
        user_id=user_id,
        organisation_id=org_id,
        ip_address=ip_address,
        after_data={"box_ids": box_ids},
    )
    await db.commit()


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
