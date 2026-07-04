"""Reference / configuration tables (previously hardcoded constants).

  * ColdStorageFacility — real cold rooms the logistics agent can book.
  * CropParameter — per-crop pricing, return multiplier, and calendar.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class ColdStorageFacility(Base, TimestampMixin):
    __tablename__ = "cold_storage_facilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    facility_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    location: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    capacity_kg: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    temperature_celsius: Mapped[float] = mapped_column(Numeric(4, 1), default=4.0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ColdStorageFacility {self.facility_code} {self.location}>"


class CropParameter(Base, TimestampMixin):
    __tablename__ = "crop_parameters"

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_type: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    # Default market price per kg, in kobo.
    price_per_kg: Mapped[int] = mapped_column(Integer, nullable=False)
    # Expected gross return multiplier on an investment (e.g. 1.25 = +25%).
    return_multiplier: Mapped[float] = mapped_column(Numeric(4, 2), default=1.20, nullable=False)
    harvest_months: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    spacing_cm: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    plant_window: Mapped[str] = mapped_column(String(40), default="", nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CropParameter {self.crop_type} x{self.return_multiplier}>"
