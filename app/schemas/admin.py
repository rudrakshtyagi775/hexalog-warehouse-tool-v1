from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import AuditModuleEnum, UserRoleEnum


class OrgResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    is_active: bool
    created_at: datetime


class UserRoleResponse(BaseModel):
    model_config = {"from_attributes": True}
    role: UserRoleEnum
    created_at: datetime


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    email: str
    full_name: str
    is_active: bool
    roles: list[UserRoleResponse]
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    roles: list[UserRoleEnum] = Field(..., min_length=1)


class AuditLogResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    module: AuditModuleEnum
    action: str
    resource_type: str
    resource_id: int | None
    user_id: int | None
    ip_address: str | None
    created_at: datetime

    @field_validator("ip_address", mode="before")
    @classmethod
    def coerce_ip(cls, v: object) -> str | None:
        return str(v) if v is not None else None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int


class StatsResponse(BaseModel):
    inward_boxes_completed_this_month: int
    total_items_scanned: int
    total_pos_uploaded: int
    active_customers: int


class RecentSubmission(BaseModel):
    inscan_number: str
    customer_name: str
    box_id: str
    scanned_qty: int
    submitted_at: datetime | None


class RecentSubmissionsResponse(BaseModel):
    items: list[RecentSubmission]
