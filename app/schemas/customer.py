import re
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.enums import CustomerStatusEnum


class CustomerCreate(BaseModel):
    name: str
    code: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("code")
    @classmethod
    def code_format(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{2,3}$", v):
            raise ValueError("code must be 2–3 uppercase letters (A–Z only)")
        return v


class CustomerUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    status: CustomerStatusEnum | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("name must not be empty")
        return v


class CustomerResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    organisation_id: int
    name: str
    code: str
    status: CustomerStatusEnum
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class CustomerListItem(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    code: str
    status: CustomerStatusEnum
