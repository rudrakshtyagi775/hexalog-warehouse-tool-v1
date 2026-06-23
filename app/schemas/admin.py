from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── Organisation ──────────────────────────────────────────────────────────────

class CreateOrganisationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class UpdateOrganisationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class OrganisationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    created_at: datetime
