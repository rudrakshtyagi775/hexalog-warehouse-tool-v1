from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import CurrentUser, require_admin
from app.models.enums import UserRoleEnum
from app.models.organisation import Organisation
from app.schemas.admin import (
    AdminPasswordResetRequest,
    AssignRoleRequest,
    AuditLogListResponse,
    OrgResponse,
    OrgUpdateRequest,
    RecentSubmissionsResponse,
    StatsResponse,
    UpdateUserRequest,
    UserCreate,
    UserResponse,
)
from app.schemas.common import MessageResponse
from app.services import admin_service, report_service
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


@router.patch("/organisations/me", response_model=OrgResponse)
async def update_my_org_endpoint(
    body: OrgUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrgResponse:
    return await admin_service.update_organisation(
        db,
        org_id=current_user.organisation_id,
        req=body,
        admin_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )


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


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: int,
    body: UpdateUserRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await admin_service.update_user(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        full_name=body.full_name,
        is_active=body.is_active,
        current_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )


@router.post("/users/{user_id}/roles", response_model=UserResponse)
async def assign_role_endpoint(
    user_id: int,
    body: AssignRoleRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await admin_service.assign_role(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        role=body.role,
        assigning_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )


@router.delete("/users/{user_id}/roles/{role}", response_model=UserResponse)
async def revoke_role_endpoint(
    user_id: int,
    role: UserRoleEnum,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    return await admin_service.revoke_role(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        role=role,
        revoking_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )


@router.post("/users/{user_id}/password-reset", response_model=MessageResponse)
async def admin_password_reset_endpoint(
    user_id: int,
    body: AdminPasswordResetRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await admin_service.admin_password_reset(
        db,
        user_id=user_id,
        org_id=current_user.organisation_id,
        new_password=body.new_password,
        admin_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return MessageResponse(message="Password reset successfully")


@router.delete("/inward-boxes/{box_id}", response_model=MessageResponse)
async def delete_inward_box_endpoint(
    box_id: str,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    await admin_service.delete_inward_box(
        db,
        box_id=box_id,
        org_id=current_user.organisation_id,
        admin_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return MessageResponse(message="Box deleted")


@router.get("/reports/outward-po")
async def report_outward_po_endpoint(
    request: Request,
    from_date: date = Query(...),
    to_date: date = Query(...),
    customer_id: int | None = Query(default=None),
    format: Literal["json", "csv"] = Query(default="json"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await report_service.get_outward_po_report(
        db,
        org_id=current_user.organisation_id,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
        fmt=format,
        page=page,
        page_size=page_size,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    if isinstance(result, bytes):
        filename = f"report-outward-po-{from_date}.csv"
        return Response(
            content=result,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return result


@router.get("/reports/inscan")
async def report_inscan_endpoint(
    request: Request,
    from_date: date = Query(...),
    to_date: date = Query(...),
    customer_id: int | None = Query(default=None),
    format: Literal["json", "csv"] = Query(default="json"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await report_service.get_inscan_report(
        db,
        org_id=current_user.organisation_id,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
        fmt=format,
        page=page,
        page_size=page_size,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    if isinstance(result, bytes):
        filename = f"report-inscan-{from_date}.csv"
        return Response(
            content=result,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return result


@router.get("/reports/item-packing")
async def report_item_packing_endpoint(
    request: Request,
    from_date: date = Query(...),
    to_date: date = Query(...),
    customer_id: int | None = Query(default=None),
    format: Literal["json", "csv"] = Query(default="json"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await report_service.get_item_packing_report(
        db,
        org_id=current_user.organisation_id,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
        fmt=format,
        page=page,
        page_size=page_size,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    if isinstance(result, bytes):
        filename = f"report-item-packing-{from_date}.csv"
        return Response(
            content=result,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return result


@router.get("/reports/variance")
async def report_variance_endpoint(
    request: Request,
    from_date: date = Query(...),
    to_date: date = Query(...),
    customer_id: int | None = Query(default=None),
    format: Literal["json", "csv"] = Query(default="json"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await report_service.get_variance_report(
        db,
        org_id=current_user.organisation_id,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
        fmt=format,
        page=page,
        page_size=page_size,
        user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    if isinstance(result, bytes):
        filename = f"report-variance-{from_date}.csv"
        return Response(
            content=result,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    return result
