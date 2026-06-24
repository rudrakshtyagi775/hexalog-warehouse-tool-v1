from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import CurrentUser, require_admin
from app.schemas.admin import (
    CreateOrganisationRequest,
    OrganisationResponse,
    UpdateOrganisationRequest,
)
from app.schemas.common import MessageResponse
from app.services.auth_service import revoke_session, revoke_user_sessions
from app.services.organisation_service import (
    create_organisation,
    get_organisation,
    list_user_organisations,
    update_organisation,
)
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


# ── Organisation management ───────────────────────────────────────────────────
# Route ordering is significant: /organisations/me MUST be declared before any
# /organisations/{id} route. FastAPI resolves in registration order and would
# otherwise treat the literal "me" as an integer path parameter, shadowing this
# endpoint. Keep all /me routes above any /{id} routes in this section.

@router.get(
    "/organisations/me",
    response_model=OrganisationResponse,
    status_code=status.HTTP_200_OK,
)
async def get_current_organisation(
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganisationResponse:
    """Return the organisation currently active in the admin's JWT."""
    org = await get_organisation(db, org_id=current_user.organisation_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )
    return org


@router.patch(
    "/organisations/me",
    response_model=OrganisationResponse,
    status_code=status.HTTP_200_OK,
)
async def update_current_organisation(
    body: UpdateOrganisationRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganisationResponse:
    """Update the name and/or active status of the current organisation."""
    org = await update_organisation(
        db,
        org_id=current_user.organisation_id,
        name=body.name,
        is_active=body.is_active,
        admin_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return org


@router.get(
    "/organisations",
    response_model=list[OrganisationResponse],
    status_code=status.HTTP_200_OK,
)
async def list_organisations(
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[OrganisationResponse]:
    """List all organisations the requesting admin belongs to."""
    return await list_user_organisations(db, user_id=current_user.user_id)


@router.post(
    "/organisations",
    response_model=OrganisationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organisation_endpoint(
    body: CreateOrganisationRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganisationResponse:
    """Create a new organisation. The requesting admin is automatically enrolled as its admin."""
    org = await create_organisation(
        db,
        name=body.name,
        creating_user_id=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return org
