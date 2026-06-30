from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import CurrentUser, require_admin
from app.models.organisation import Organisation
from app.schemas.admin import (
    AuditLogListResponse,
    OrgResponse,
    RecentSubmissionsResponse,
    StatsResponse,
    UserCreate,
    UserResponse,
)
from app.schemas.common import MessageResponse
from app.services import admin_service
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


@router.get("/stats", response_model=StatsResponse)
async def get_stats_endpoint(
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> StatsResponse:
    return await admin_service.get_stats(db, organisation_id=current_user.organisation_id)


@router.get("/recent-submissions", response_model=RecentSubmissionsResponse)
async def get_recent_submissions_endpoint(
    limit: int = 10,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> RecentSubmissionsResponse:
    return await admin_service.get_recent_submissions(
        db, organisation_id=current_user.organisation_id, limit=min(limit, 50)
    )


@router.get("/audit-logs", response_model=AuditLogListResponse)
async def list_audit_logs_endpoint(
    page: int = 1,
    page_size: int = 20,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    return await admin_service.list_audit_logs(
        db,
        organisation_id=current_user.organisation_id,
        page=max(1, page),
        page_size=min(page_size, 100),
    )


@router.get("/organisations/me", response_model=OrgResponse)
async def get_my_org_endpoint(
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrgResponse:
    org = await db.get(Organisation, current_user.organisation_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return OrgResponse.model_validate(org)


@router.get("/users", response_model=list[UserResponse])
async def list_users_endpoint(
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    return await admin_service.list_users(db, organisation_id=current_user.organisation_id)


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user_endpoint(
    payload: UserCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await admin_service.create_user(
        db,
        organisation_id=current_user.organisation_id,
        created_by=current_user.user_id,
        ip_address=get_client_ip(request),
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        roles=payload.roles,
    )
