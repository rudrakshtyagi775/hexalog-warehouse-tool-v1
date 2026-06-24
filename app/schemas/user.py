from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRoleEnum


class UserRoleInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: UserRoleEnum
    created_at: datetime


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool
    created_at: datetime
    roles: list[UserRoleInfo]


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    roles: list[UserRoleEnum] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class AssignRoleRequest(BaseModel):
    role: UserRoleEnum


class AdminPasswordResetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    new_password: str = Field(min_length=8, max_length=128)
