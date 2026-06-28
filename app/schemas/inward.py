from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import InwardBoxStatusEnum, InwardCodeTypeEnum, InwardReferenceStatusEnum


# ── Request schemas ───────────────────────────────────────────────────────────

class POLineCreate(BaseModel):
    ean: str
    ordered_qty: int = Field(gt=0)
    description: str | None = None


class BoxCreate(BaseModel):
    customer_id: int


class BoxClose(BaseModel):
    physical_qty: int = Field(ge=0)


class ScanCreate(BaseModel):
    ean: str
    code_type: InwardCodeTypeEnum = InwardCodeTypeEnum.ean


# ── Response schemas ──────────────────────────────────────────────────────────

class POLineResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    description: str | None
    ordered_qty: int
    packed_qty: int


class POResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    po_number: str
    customer_id: int
    status: InwardReferenceStatusEnum
    uploaded_at: datetime
    lines: list[POLineResponse]


class ScanResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    code_type: InwardCodeTypeEnum
    is_deleted: bool
    created_at: datetime


class BoxResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    box_id: str
    customer_id: int
    status: InwardBoxStatusEnum
    physical_qty: int | None
    scanned_qty: int
    inscan_number: str | None
    scans: list[ScanResponse]
    created_at: datetime
    is_read_only: bool  # True when status == completed


class ScanCreateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    code_type: InwardCodeTypeEnum
    is_deleted: bool
    created_at: datetime
    note: str | None = None
