import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_user, require_inward_operator, require_packer
from app.models.enums import OutwardBoxStatusEnum
from app.models.outward import OutwardBox, OutwardPO
from app.schemas.outward import (
    OutwardBoxCreate,
    OutwardBoxResponse,
    OutwardPOResponse,
    OutwardScanCreate,
    OutwardScanCreateResponse,
    OutwardScanResponse,
)
from app.services import outward_service
from app.utils.request import get_client_ip

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/outward", tags=["outward"])


def _box_to_response(box: OutwardBox) -> OutwardBoxResponse:
    return OutwardBoxResponse(
        id=box.id,
        box_id=box.box_id,
        customer_id=box.customer_id,
        status=box.status,
        scans=[
            OutwardScanResponse(
                id=s.id,
                ean=s.ean,
                scan_result=s.scan_result,
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
    current_user=Depends(require_inward_operator),
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
        csv_bytes=csv_bytes,
    )
    # Eagerly load lines for response serialisation — lazy="raise" blocks post-commit access
    result = await db.execute(
        select(OutwardPO).options(selectinload(OutwardPO.lines)).where(OutwardPO.id == po.id)
    )
    po_with_lines = result.scalar_one()
    return po_with_lines


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
    current_user=Depends(get_current_user),
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
        created_at=scan.created_at,
    )
