from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    MeResponse,
    OrganisationInfo,
    RefreshResponse,
    UserInfo,
)
from app.schemas.common import MessageResponse
from app.services.auth_service import (
    LoginResult,
    login,
    logout,
    logout_all_devices,
    refresh_session,
)
from app.utils.request import get_client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE_NAME = "refresh_token"
_COOKIE_PATH = "/api/auth"
_COOKIE_MAX_AGE = 86400  # 24 h — matches SESSION_ABSOLUTE_EXPIRE_HOURS


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="strict",
        path=_COOKIE_PATH,
        max_age=_COOKIE_MAX_AGE,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_COOKIE_NAME,
        path=_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login_endpoint(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    user_agent = request.headers.get("user-agent")
    result: LoginResult = await login(
        db,
        email=body.email,
        password=body.password,
        organisation_id=body.organisation_id,
        ip_address=get_client_ip(request),
        user_agent=user_agent,
    )
    _set_refresh_cookie(response, result.refresh_token)
    return LoginResponse(
        access_token=result.access_token,
        expires_at=result.expires_at,
        user=UserInfo(id=result.user_id, full_name=result.full_name, email=result.email),
        roles=result.roles,
        organisation=OrganisationInfo(
            id=result.organisation_id, name=result.organisation_name
        ),
    )


@router.post("/refresh", response_model=RefreshResponse, status_code=status.HTTP_200_OK)
async def refresh_endpoint(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> RefreshResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    result = await refresh_session(db, refresh_token)
    _set_refresh_cookie(response, result.new_refresh_token)
    return RefreshResponse(
        access_token=result.access_token,
        expires_at=result.expires_at,
    )


@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def logout_endpoint(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Revoke the refresh token session. No Bearer token required — works cookie-only.

    Always returns 200 and clears the cookie, even if the token is unknown or
    already revoked, so the client is always left in a logged-out state.
    """
    if refresh_token:
        await logout(
            db,
            raw_token=refresh_token,
            ip_address=get_client_ip(request),
        )
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out successfully")


@router.post("/logout-all", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def logout_all_endpoint(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    refresh_token: str | None = Cookie(default=None, alias=_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Revoke all other sessions for this user. Current session stays alive."""
    revoked = await logout_all_devices(
        db,
        current_session_id=current_user.session_id,
        user_id=current_user.user_id,
        organisation_id=current_user.organisation_id,
        ip_address=get_client_ip(request),
    )
    return MessageResponse(message=f"Logged out from {revoked} other session(s)")


@router.get("/me", response_model=MeResponse, status_code=status.HTTP_200_OK)
async def me_endpoint(current_user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    """Serve current user info from CurrentUser. No additional DB query."""
    return MeResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        organisation_id=current_user.organisation_id,
        roles=current_user.roles,
    )
