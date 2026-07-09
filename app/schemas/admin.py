from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import (
    AuditModuleEnum,
    OutwardPoStatusEnum,
    OutwardScanResultEnum,
    UserRoleEnum,
)


class UpdateUserRequest(BaseModel):
    model_config = {"str_strip_whitespace": True}

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class AssignRoleRequest(BaseModel):
    role: UserRoleEnum


class AdminPasswordResetRequest(BaseModel):
    model_config = {"str_strip_whitespace": True}

    new_password: str = Field(min_length=8, max_length=128)


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


class OrgUpdateRequest(BaseModel):
    model_config = {"str_strip_whitespace": True}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class StatsResponse(BaseModel):
    inward_boxes_completed_this_month: int
    total_items_scanned: int
    total_pos_uploaded: int
    active_customers: int
    outward_boxes_this_month: int
    outward_scans_this_month: int
    open_po_count: int


class OutwardPOReportRow(BaseModel):
    po_number: str
    customer_name: str
    ean: str
    description: str | None
    ordered_qty: int
    packed_qty: int
    remaining: int
    status: OutwardPoStatusEnum
    uploaded_at: datetime


class OutwardPOReportResponse(BaseModel):
    items: list[OutwardPOReportRow]
    total: int
    from_date: date
    to_date: date


class ItemPackingReportRow(BaseModel):
    timestamp: datetime
    user_name: str | None
    box_id: str
    ean: str
    scan_result: OutwardScanResultEnum
    allocated_po: str | None
    reject_reason: str | None
    stock_flagged: bool


class ItemPackingReportResponse(BaseModel):
    items: list[ItemPackingReportRow]
    total: int
    from_date: date
    to_date: date


class VarianceReportRow(BaseModel):
    customer_name: str
    ean: str
    total_inward_qty: int
    total_outward_qty: int
    current_balance: int
    stock_flagged_count: int
    last_movement_date: datetime | None


class VarianceReportResponse(BaseModel):
    items: list[VarianceReportRow]
    total: int
    from_date: date
    to_date: date


class InscanReportRow(BaseModel):
    inscan_number: str
    customer_name: str
    po_number: str | None
    invoice_number: str | None
    box_number: str | None
    ean: str
    scanned_qty: int
    physical_qty: int | None
    variance: int
    date: datetime
    user_name: str | None
    status: str


class InscanReportResponse(BaseModel):
    items: list[InscanReportRow]
    total: int
    from_date: date
    to_date: date


class RecentSubmission(BaseModel):
    inscan_number: str
    customer_name: str
    box_id: str
    scanned_qty: int
    submitted_at: datetime | None


class RecentSubmissionsResponse(BaseModel):
    items: list[RecentSubmission]
