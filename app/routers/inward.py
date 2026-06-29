import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import get_current_user, require_inward_operator, require_packer
from app.models.enums import InwardBoxStatusEnum
from app.models.inward import InwardBox, InwardPO
from app.schemas.inward import BoxClose, BoxCreate, BoxResponse, POResponse, ScanCreate, ScanCreateResponse, ScanResponse
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


@router.post("/boxes", response_model=BoxResponse, status_code=status.HTTP_201_CREATED)
async def create_box_endpoint(
    body: BoxCreate,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.create_box(
        db,
        customer_id=body.customer_id,
        organisation_id=current_user.organisation_id,
        created_by=current_user.user_id,
        user_roles=current_user.roles,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)


@router.get("/boxes/{box_id}", response_model=BoxResponse)
async def get_box_endpoint(
    box_id: str,
    current_user=Depends(get_current_user),
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
    current_user=Depends(require_packer),
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


@router.post(
    "/boxes/{box_id}/scans",
    response_model=ScanCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_scan_endpoint(
    box_id: str,
    body: ScanCreate,
    request: Request,
    current_user=Depends(require_packer),
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
    current_user=Depends(require_packer),
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
