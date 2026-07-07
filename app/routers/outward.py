from datetime import date

import structlog
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import require_admin, require_admin_or_packer, require_packer
from app.models.enums import OutwardBoxStatusEnum, OutwardPoStatusEnum, UserRoleEnum
from app.models.outward import OutwardBox, OutwardPO
from app.schemas.outward import (
    LabelGenerateRequest,
    LabelGenerateResponse,
    LabelHistoryResponse,
    OpenPOListResponse,
    OutwardBoxCreate,
    OutwardBoxListResponse,
    OutwardBoxResponse,
    OutwardPOPreviewResponse,
    OutwardPOResponse,
    OutwardPOToggleRequest,
    OutwardScanCreate,
    OutwardScanCreateResponse,
    OutwardScanResponse,
)
from app.services import label_service, outward_service
from app.utils.request import get_client_ip

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/outward", tags=["outward"])


def _box_to_response(box: OutwardBox) -> OutwardBoxResponse:
    return OutwardBoxResponse(
        id=box.id,
        box_id=box.box_id,
        customer_id=box.customer_id,
        status=box.status,
        print_count=box.print_count,
        closed_at=box.closed_at,
        scans=[
            OutwardScanResponse(
                id=s.id,
                ean=s.ean,
                scan_result=s.scan_result,
                stock_flagged=s.stock_flagged,
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
    current_user=Depends(require_admin),
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
        filename=file.filename or "po.csv",
        csv_bytes=csv_bytes,
    )
    # Eagerly load lines for response serialisation — lazy="raise" blocks post-commit access
    result = await db.execute(
        select(OutwardPO).options(selectinload(OutwardPO.lines)).where(OutwardPO.id == po.id)
    )
    po_with_lines = result.scalar_one()
    return po_with_lines


@router.get("/pos", response_model=OpenPOListResponse)
async def list_open_pos_endpoint(
    customer_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user=Depends(require_admin_or_packer),
    db: AsyncSession = Depends(get_db),
) -> OpenPOListResponse:
    # Admin sees POs in every status; packer is restricted to open POs only (PRD).
    is_admin = UserRoleEnum.admin in current_user.roles
    return await outward_service.list_open_pos(
        db,
        org_id=current_user.organisation_id,
        customer_id=customer_id,
        search=search,
        from_date=from_date,
        to_date=to_date,
        status_filter=None if is_admin else OutwardPoStatusEnum.open,
        page=page,
        page_size=page_size,
    )


@router.patch("/pos/{po_id}/status", response_model=OutwardPOResponse)
async def toggle_po_status_endpoint(
    po_id: int,
    body: OutwardPOToggleRequest,
    request: Request,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OutwardPOResponse:
    return await outward_service.toggle_po_status(
        db,
        po_id=po_id,
        org_id=current_user.organisation_id,
        new_status=body.status,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )


@router.post("/pos/preview", response_model=OutwardPOPreviewResponse)
async def preview_po_endpoint(
    customer_id: int = Form(...),
    file: UploadFile = ...,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OutwardPOPreviewResponse:
    csv_bytes = await file.read()
    return await outward_service.preview_po(
        db,
        org_id=current_user.organisation_id,
        customer_id=customer_id,
        filename=file.filename or "po.csv",
        csv_bytes=csv_bytes,
    )


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
    current_user=Depends(require_packer),
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
        stock_flagged=scan.stock_flagged,
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
        stock_flagged=scan.stock_flagged,
        created_at=scan.created_at,
    )


# ── Labels ────────────────────────────────────────────────────────────────────

async def _load_boxes_for_pdf(
    db: AsyncSession, *, org_id: int, box_ids: list[str]
) -> list[tuple[str, str]]:
    from app.models.customer import Customer

    result = await db.execute(
        select(OutwardBox.box_id, Customer.name)
        .join(Customer, OutwardBox.customer_id == Customer.id)
        .where(OutwardBox.organisation_id == org_id, OutwardBox.box_id.in_(box_ids))
    )
    found = {box_id: customer_name for box_id, customer_name in result.all()}
    missing = [b for b in box_ids if b not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Box(es) not found: {', '.join(missing)}",
        )
    return [(box_id, found[box_id]) for box_id in box_ids]


@router.post(
    "/labels/generate", response_model=LabelGenerateResponse, status_code=status.HTTP_201_CREATED
)
async def generate_labels_endpoint(
    body: LabelGenerateRequest,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> LabelGenerateResponse:
    return await outward_service.generate_labels(
        db,
        org_id=current_user.organisation_id,
        customer_id=body.customer_id,
        count=body.count,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )


@router.get("/labels/pdf")
async def download_labels_pdf_endpoint(
    request: Request,
    box_ids: list[str] = Query(...),
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> Response:
    boxes = await _load_boxes_for_pdf(db, org_id=current_user.organisation_id, box_ids=box_ids)
    pdf_bytes = label_service.render_label_pdf(boxes)
    await label_service.audit_label_download(
        db,
        box_ids=box_ids,
        org_id=current_user.organisation_id,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=labels.pdf"},
    )


@router.get("/labels/history", response_model=LabelHistoryResponse)
async def label_history_endpoint(
    customer_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> LabelHistoryResponse:
    return await outward_service.get_label_history(
        db,
        org_id=current_user.organisation_id,
        user_id=current_user.user_id,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )


@router.post("/boxes/{box_id}/reprint")
async def reprint_label_endpoint(
    box_id: str,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await label_service.increment_print_count(
        db,
        box_id=box_id,
        org_id=current_user.organisation_id,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    boxes = await _load_boxes_for_pdf(db, org_id=current_user.organisation_id, box_ids=[box_id])
    pdf_bytes = label_service.render_label_pdf(boxes)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={box_id}.pdf"},
    )


@router.get("/packing-history", response_model=OutwardBoxListResponse)
async def packing_history_endpoint(
    days: int = Query(default=30, ge=1, le=365),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> OutwardBoxListResponse:
    return await outward_service.list_packing_history(
        db,
        org_id=current_user.organisation_id,
        user_id=current_user.user_id,
        days=days,
        page=page,
        page_size=page_size,
    )
