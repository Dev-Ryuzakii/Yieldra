"""Reference-data schemas: cold storage facilities + crop parameters."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FacilityCreate(BaseModel):
    facility_code: str = Field(min_length=1, max_length=40)
    name: str
    location: str
    capacity_kg: float = Field(gt=0)
    temperature_celsius: float = 4.0
    active: bool = True


class FacilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    facility_code: str
    name: str
    location: str
    capacity_kg: float
    temperature_celsius: float
    active: bool
    created_at: datetime


class CropParameterCreate(BaseModel):
    crop_type: str = Field(min_length=1, max_length=80)
    price_per_kg_ngn: int = Field(gt=0, description="Default price per kg in Naira")
    return_multiplier: float = Field(gt=0, default=1.20)
    harvest_months: int = Field(ge=1, default=4)
    spacing_cm: int = Field(ge=1, default=50)
    plant_window: str = ""


class CropParameterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crop_type: str
    price_per_kg: int = Field(description="Price per kg in kobo")
    return_multiplier: float
    harvest_months: int
    spacing_cm: int
    plant_window: str
    created_at: datetime
