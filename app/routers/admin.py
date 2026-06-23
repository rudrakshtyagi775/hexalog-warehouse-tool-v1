from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import CurrentUser, require_admin
from app.schemas.common import MessageResponse
from app.services.auth_service import revoke_session, revoke_user_sessions
from app.utils.request import get_client_ip

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_session_endpoint(
    session_id: UUID,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await revoke_session(
        db,
        session_id=session_id,
        admin_user_id=current_user.user_id,
        admin_org_id=current_user.organisation_id,
        ip_address=get_client_ip(request),
    )
    return MessageResponse(message="Session revoked")


@router.delete(
    "/users/{user_id}/sessions",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_user_sessions_endpoint(
    user_id: int,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    revoked = await revoke_user_sessions(
        db,
        target_user_id=user_id,
        admin_user_id=current_user.user_id,
        admin_org_id=current_user.organisation_id,
        ip_address=get_client_ip(request),
    )
    return MessageResponse(message=f"Revoked {revoked} session(s)")
