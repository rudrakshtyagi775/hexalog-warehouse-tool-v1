from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import UserRoleEnum
from app.models.user import User
from app.schemas.auth import CurrentUser
from app.services.jwt_service import decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Decode JWT and load the user row for is_active check and /me data.

    Roles are taken from the JWT (cryptographically verified) to avoid a second
    DB query. Role changes propagate after the access token refreshes (~15 min).
    One SELECT per authenticated request: the User row.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    payload = decode_access_token(credentials.credentials)

    try:
        user_id = int(payload["sub"])
        organisation_id = int(payload["org"])
        session_id = payload["session_id"]
        roles = [UserRoleEnum(r) for r in payload.get("roles", [])]
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    return CurrentUser(
        user_id=user_id,
        organisation_id=organisation_id,
        session_id=session_id,
        roles=roles,
        full_name=user.full_name,
        email=user.email,
        is_active=user.is_active,
    )


def require_roles(*allowed_roles: UserRoleEnum):
    """Return a dependency that enforces role membership.

    Admins implicitly pass every role check — always include UserRoleEnum.admin
    in the allowed_roles set.
    """
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(r in current_user.roles for r in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check


# Convenience shorthands — admin implicitly passes all checks
require_admin = require_roles(UserRoleEnum.admin)
require_inward_operator = require_roles(UserRoleEnum.inward_operator, UserRoleEnum.admin)
require_packer = require_roles(UserRoleEnum.packer, UserRoleEnum.admin)
