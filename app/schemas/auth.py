from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.enums import UserRoleEnum


# ── Request schemas ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    organisation_id: int


# ── Response sub-objects ──────────────────────────────────────────────────────

class UserInfo(BaseModel):
    id: int
    full_name: str
    email: str


class OrganisationInfo(BaseModel):
    id: int
    name: str


# ── Response schemas ──────────────────────────────────────────────────────────

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserInfo
    roles: list[UserRoleEnum]
    organisation: OrganisationInfo


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class MeResponse(BaseModel):
    user_id: int
    email: str
    full_name: str
    is_active: bool
    organisation_id: int
    roles: list[UserRoleEnum]


# ── Internal dependency injection ─────────────────────────────────────────────

@dataclass
class CurrentUser:
    """Injected into every authenticated route via get_current_user dependency.

    Populated from JWT claims + one DB row load in get_current_user.
    No second DB query is needed to serve GET /me.
    """
    user_id: int
    organisation_id: int
    session_id: str
    roles: list[UserRoleEnum]
    full_name: str
    email: str
    is_active: bool
