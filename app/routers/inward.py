import structlog
from fastapi import APIRouter, Depends, Form, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies.auth import require_inward_operator
from app.models.inward import InwardPO
from app.schemas.inward import POResponse
from app.services import inward_service
from app.utils.request import get_client_ip

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/inward", tags=["inward"])


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
