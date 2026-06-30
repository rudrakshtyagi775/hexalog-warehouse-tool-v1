from datetime import datetime

from pydantic import BaseModel

from app.models.enums import OutwardBoxStatusEnum, OutwardPoStatusEnum, OutwardScanResultEnum

# ── Request schemas ───────────────────────────────────────────────────────────

class OutwardBoxCreate(BaseModel):
    customer_id: int


class OutwardScanCreate(BaseModel):
    ean: str


# ── Response schemas ──────────────────────────────────────────────────────────

class OutwardPOLineResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    description: str | None
    ordered_qty: int
    packed_qty: int


class OutwardPOResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    po_number: str
    customer_id: int
    status: OutwardPoStatusEnum
    uploaded_at: datetime
    lines: list[OutwardPOLineResponse]


class OutwardScanResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    scan_result: OutwardScanResultEnum
    created_at: datetime


class OutwardBoxResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    box_id: str
    customer_id: int
    status: OutwardBoxStatusEnum
    scans: list[OutwardScanResponse]
    created_at: datetime
    is_read_only: bool  # True when status == closed


class OutwardScanCreateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    scan_result: OutwardScanResultEnum
    created_at: datetime
    note: str | None = None
