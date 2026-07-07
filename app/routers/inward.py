from datetime import date

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import require_inward_operator
from app.models.enums import InwardBoxStatusEnum, InwardReferenceStatusEnum
from app.models.inward import InwardBox, InwardPO
from app.schemas.inward import (
    BoxClose,
    BoxCreate,
    BoxResponse,
    InwardBoxListResponse,
    InwardReferenceCreate,
    InwardReferenceListResponse,
    InwardReferenceResponse,
    POResponse,
    ScanCreate,
    ScanCreateResponse,
    ScanResponse,
)
from app.services import inward_service
from app.utils.request import get_client_ip

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/inward", tags=["inward"])


def _box_to_response(box: InwardBox) -> BoxResponse:
    return BoxResponse(
        id=box.id,
        box_id=box.box_id,
        customer_id=box.customer_id,
        status=box.status,
        physical_qty=box.physical_qty,
        scanned_qty=box.scanned_qty,
        inscan_number=box.inscan_number,
        inward_reference_id=box.inward_reference_id,
        box_number=box.box_number,
        scans=[
            ScanResponse(
                id=s.id,
                ean=s.ean,
                code_type=s.code_type,
                is_deleted=s.is_deleted,
                created_at=s.created_at,
            )
            for s in box.scans
        ],
        created_at=box.created_at,
        is_read_only=box.status == InwardBoxStatusEnum.completed,
    )


@router.post("/pos", response_model=POResponse, status_code=status.HTTP_201_CREATED)
async def upload_po_endpoint(
    request: Request,
    po_number: str = Form(...),
    customer_id: int = Form(...),
    file: UploadFile = ...,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> POResponse:
    csv_bytes = await file.read()
    po = await inward_service.upload_po(
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
        select(InwardPO).options(selectinload(InwardPO.lines)).where(InwardPO.id == po.id)
    )
    po_with_lines = result.scalar_one()
    return po_with_lines


@router.post(
    "/references", response_model=InwardReferenceResponse, status_code=status.HTTP_201_CREATED
)
async def create_reference_endpoint(
    body: InwardReferenceCreate,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> InwardReferenceResponse:
    reference, is_duplicate = await inward_service.get_or_create_reference(
        db,
        organisation_id=current_user.organisation_id,
        customer_id=body.customer_id,
        po_number=body.po_number,
        invoice_number=body.invoice_number,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return InwardReferenceResponse(
        id=reference.id,
        customer_id=reference.customer_id,
        po_number=reference.po_number,
        invoice_number=reference.invoice_number,
        status=reference.status,
        created_at=reference.created_at,
        is_duplicate=is_duplicate,
        duplicate_message=(
            "An inward already exists for this PO/Invoice. Continue adding boxes to it?"
            if is_duplicate
            else None
        ),
    )


@router.get("/references", response_model=InwardReferenceListResponse)
async def list_references_endpoint(
    customer_id: int | None = Query(default=None),
    status_filter: InwardReferenceStatusEnum | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> InwardReferenceListResponse:
    return await inward_service.list_references(
        db,
        org_id=current_user.organisation_id,
        customer_id=customer_id,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.post("/references/{reference_id}/finish", response_model=InwardReferenceResponse)
async def finish_reference_endpoint(
    reference_id: int,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> InwardReferenceResponse:
    reference = await inward_service.finish_reference(
        db,
        reference_id=reference_id,
        organisation_id=current_user.organisation_id,
        finished_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return InwardReferenceResponse(
        id=reference.id,
        customer_id=reference.customer_id,
        po_number=reference.po_number,
        invoice_number=reference.invoice_number,
        status=reference.status,
        created_at=reference.created_at,
    )


@router.post("/boxes", response_model=BoxResponse, status_code=status.HTTP_201_CREATED)
async def create_box_endpoint(
    body: BoxCreate,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.create_box(
        db,
        customer_id=body.customer_id,
        organisation_id=current_user.organisation_id,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
        inward_reference_id=body.inward_reference_id,
        box_number=body.box_number,
    )
    return _box_to_response(box)


@router.get("/boxes", response_model=InwardBoxListResponse)
async def list_boxes_endpoint(
    status_filter: InwardBoxStatusEnum | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> InwardBoxListResponse:
    return await inward_service.list_boxes(
        db,
        org_id=current_user.organisation_id,
        created_by=current_user.user_id,
        status_filter=status_filter,
        customer_id=customer_id,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
    )


@router.get("/boxes/{box_id}", response_model=BoxResponse)
async def get_box_endpoint(
    box_id: str,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.get_box(
        db, box_id=box_id, organisation_id=current_user.organisation_id
    )
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")
    return _box_to_response(box)


@router.post("/boxes/{box_id}/close", response_model=BoxResponse)
async def close_box_endpoint(
    box_id: str,
    body: BoxClose,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.close_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        physical_qty=body.physical_qty,
        closed_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.post("/boxes/{box_id}/submit", response_model=BoxResponse)
async def submit_box_endpoint(
    box_id: str,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.submit_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        submitted_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.post("/boxes/{box_id}/reopen", response_model=BoxResponse)
async def reopen_box_endpoint(
    box_id: str,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.reopen_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        reopened_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.post(
    "/boxes/{box_id}/scans",
    response_model=ScanCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_scan_endpoint(
    box_id: str,
    body: ScanCreate,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> ScanCreateResponse:
    scan, note = await inward_service.add_scan(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        ean=body.ean,
        code_type=body.code_type,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
        is_manual_entry=body.is_manual_entry,
    )
    return ScanCreateResponse(
        id=scan.id,
        ean=scan.ean,
        code_type=scan.code_type,
        is_deleted=scan.is_deleted,
        created_at=scan.created_at,
        note=note,
    )


@router.delete("/scans/{scan_id}", response_model=ScanResponse)
async def delete_scan_endpoint(
    scan_id: int,
    request: Request,
    current_user=Depends(require_inward_operator),
    db: AsyncSession = Depends(get_db),
) -> ScanResponse:
    scan = await inward_service.delete_scan(
        db,
        scan_id=scan_id,
        organisation_id=current_user.organisation_id,
        deleted_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return ScanResponse(
        id=scan.id,
        ean=scan.ean,
        code_type=scan.code_type,
        is_deleted=scan.is_deleted,
        created_at=scan.created_at,
    )
