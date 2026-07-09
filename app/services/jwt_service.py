from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, status

from app.config import settings
from app.models.enums import UserRoleEnum

_ALGORITHM = "HS256"  # Never derive from token header — hardcoded unconditionally


def issue_access_token(
    user_id: int,
    organisation_id: int,
    roles: list[UserRoleEnum],
    session_id: str,
) -> tuple[str, datetime]:
    """Return (encoded_jwt, expires_at_utc)."""
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "org": str(organisation_id),
        "roles": [r.value for r in roles],
        "session_id": session_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTP 401 on any error.

    `algorithms` is an explicit list — alg:none is rejected unconditionally
    because we never allow the algorithm to be derived from the token header.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "org", "roles", "session_id", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return payload
