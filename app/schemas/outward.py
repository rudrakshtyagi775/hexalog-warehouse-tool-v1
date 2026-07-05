from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import OutwardBoxStatusEnum, OutwardPoStatusEnum, OutwardScanResultEnum

# ── Request schemas ───────────────────────────────────────────────────────────

class OutwardBoxCreate(BaseModel):
    customer_id: int


class OutwardScanCreate(BaseModel):
    ean: str


class OutwardPOToggleRequest(BaseModel):
    status: OutwardPoStatusEnum


class LabelGenerateRequest(BaseModel):
    customer_id: int
    count: int = Field(ge=1, le=50)


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
    stock_flagged: bool
    created_at: datetime


class OutwardBoxResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    box_id: str
    customer_id: int
    status: OutwardBoxStatusEnum
    print_count: int
    closed_at: datetime | None
    scans: list[OutwardScanResponse]
    created_at: datetime
    is_read_only: bool  # True when status == closed


class OutwardScanCreateResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    ean: str
    scan_result: OutwardScanResultEnum
    stock_flagged: bool
    created_at: datetime
    note: str | None = None


class OpenPOSummary(BaseModel):
    id: int
    po_number: str
    customer_id: int
    customer_name: str
    status: OutwardPoStatusEnum
    uploaded_at: datetime
    total_ordered: int
    total_packed: int
    progress_pct: float


class OpenPOListResponse(BaseModel):
    items: list[OpenPOSummary]
    total: int


class OutwardPOPreviewRow(BaseModel):
    po_number: str
    ean: str
    ordered_qty: int
    description: str | None


class OutwardPOPreviewResponse(BaseModel):
    first_10_rows: list[OutwardPOPreviewRow]
    total_rows: int
    problems: list[str]
    is_valid: bool
    consolidation_notice: str | None = None


class LabelGenerateResponse(BaseModel):
    box_ids: list[str]


class LabelHistoryItem(BaseModel):
    box_id: str
    customer_name: str
    status: OutwardBoxStatusEnum
    print_count: int
    created_at: datetime
    closed_at: datetime | None


class LabelHistoryResponse(BaseModel):
    items: list[LabelHistoryItem]
    total: int


class OutwardBoxSummary(BaseModel):
    box_id: str
    customer_name: str
    status: OutwardBoxStatusEnum
    scan_count: int
    closed_at: datetime | None
    created_at: datetime


class OutwardBoxListResponse(BaseModel):
    items: list[OutwardBoxSummary]
    total: int
