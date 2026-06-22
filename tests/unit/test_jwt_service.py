from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest

from app.config import settings
from app.models.enums import UserRoleEnum
from app.services.jwt_service import decode_access_token, issue_access_token


def test_issue_and_decode_roundtrip():
    token, expires_at = issue_access_token(
        user_id=1,
        organisation_id=2,
        roles=[UserRoleEnum.admin],
        session_id="test-session-uuid",
    )
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    assert payload["org"] == "2"
    assert payload["roles"] == ["admin"]
    assert payload["session_id"] == "test-session-uuid"


def test_expires_at_is_approximately_15_minutes():
    _, expires_at = issue_access_token(1, 1, [UserRoleEnum.packer], "s1")
    now = datetime.now(tz=timezone.utc)
    delta = expires_at - now
    assert timedelta(minutes=14) < delta <= timedelta(minutes=16)


def test_expired_token_raises_401():
    from fastapi import HTTPException
    with patch("app.services.jwt_service.settings") as mock_settings:
        mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = -1
        mock_settings.JWT_SECRET_KEY = settings.JWT_SECRET_KEY
        now = datetime.now(tz=timezone.utc)
        payload = {
            "sub": "1", "org": "1", "roles": [], "session_id": "s1",
            "iat": int((now - timedelta(minutes=2)).timestamp()),
            "exp": int((now - timedelta(minutes=1)).timestamp()),
        }
        expired_token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(expired_token)
    assert exc_info.value.status_code == 401


def test_tampered_token_raises_401():
    from fastapi import HTTPException
    token, _ = issue_access_token(1, 1, [UserRoleEnum.packer], "s1")
    # Flip a character in the signature segment
    parts = token.split(".")
    tampered = parts[0] + "." + parts[1] + "." + parts[2][:-1] + ("X" if parts[2][-1] != "X" else "Y")
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(tampered)
    assert exc_info.value.status_code == 401


def test_alg_none_rejected():
    from fastapi import HTTPException
    # Manually craft an unsigned token
    import base64, json
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(
        {"sub": "1", "org": "1", "roles": [], "session_id": "s1",
         "iat": 9999999999, "exp": 9999999999}
    ).encode()).rstrip(b"=").decode()
    none_token = f"{header}.{payload_b64}."
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(none_token)
    assert exc_info.value.status_code == 401
