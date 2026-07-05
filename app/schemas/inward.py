from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import InwardBoxStatusEnum, InwardCodeTypeEnum, InwardReferenceStatusEnum

# ── Request schemas ───────────────────────────────────────────────────────────

class POLineCreate(BaseModel):
    ean: str
    ordered_qty: int = Field(gt=0)
    description: str | None = None


class InwardReferenceCreate(BaseModel):
    customer_id: int
    po_number: str | None = None
    invoice_number: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "InwardReferenceCreate":
        if not (self.po_number or self.invoice_number):
            raise ValueError("At least one of po_number or invoice_number is required")
        return self


class BoxCreate(BaseModel):
    customer_id: int
    inward_reference_id: int | None = None
    box_number: str | None = None


class BoxClose(BaseModel):
    physical_qty: int = Field(ge=0)


class ScanCreate(BaseModel):
    ean: str
    code_type: InwardCodeTypeEnum = InwardCodeTypeEnum.ean
    is_manual_entry: bool = False


# ── Response schemas ──────────────────────────────────────────────────────────

class InwardReferenceResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    customer_id: int
    po_number: str | None
    invoice_number: str | None
    status: InwardReferenceStatusEnum
    created_at: datetime
    is_duplicate: bool = False
    duplicate_message: str | None = None


class InwardReferenceSummary(BaseModel):
    id: int
    customer_id: int
    customer_name: str
    po_number: str | None
    invoice_number: str | None
    status: InwardReferenceStatusEnum
    created_at: datetime


class InwardReferenceListResponse(BaseModel):
    items: list[InwardReferenceSummary]
    total: int


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
    inward_reference_id: int | None
    box_number: str | None
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


class InwardBoxSummary(BaseModel):
    box_id: str
    customer_name: str
    status: InwardBoxStatusEnum
    scanned_qty: int
    inscan_number: str | None
    created_at: datetime
    submitted_at: datetime | None


class InwardBoxListResponse(BaseModel):
    items: list[InwardBoxSummary]
    total: int
