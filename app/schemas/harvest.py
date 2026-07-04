"""Harvest request/response schemas."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.harvest import HarvestStatus


class HarvestCreate(BaseModel):
    farm_id: int
    expected_date: date | None = None


class HarvestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    farm_id: int
    expected_date: date | None
    actual_date: date | None
    yield_kg: float | None
    status: HarvestStatus
    created_at: datetime
