"""Farm request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.farm import FarmStatus


class FarmCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    farmer_id: int
    location: str
    crop_type: str
    cover_image_url: str | None = None
    cover_image_alt: str | None = None
    cover_image_credit: str | None = None
    cover_image_source: str | None = None
    cover_image_license: str | None = None
    total_plots: int = Field(ge=0)


class FarmRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    farmer_id: int
    location: str
    crop_type: str
    cover_image_url: str | None
    cover_image_alt: str | None
    cover_image_credit: str | None
    cover_image_source: str | None
    cover_image_license: str | None
    total_plots: int
    available_plots: int
    status: FarmStatus
    created_at: datetime
