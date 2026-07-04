"""Harvest and ColdStorageBooking models."""

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class HarvestStatus(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    in_transit = "in_transit"
    stored = "stored"
    sold = "sold"


class Harvest(Base, TimestampMixin):
    __tablename__ = "harvests"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    yield_kg: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[HarvestStatus] = mapped_column(
        Enum(HarvestStatus, name="harvest_status"),
        default=HarvestStatus.pending,
        nullable=False,
    )

    farm: Mapped["Farm"] = relationship(back_populates="harvests")
    cold_storage_bookings: Mapped[list["ColdStorageBooking"]] = relationship(
        back_populates="harvest", cascade="all, delete-orphan"
    )
    offtake_contracts: Mapped[list["OfftakeContract"]] = relationship(
        back_populates="harvest", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Harvest {self.id} farm={self.farm_id} {self.status.value}>"


class ColdStorageBooking(Base, TimestampMixin):
    __tablename__ = "cold_storage_bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    harvest_id: Mapped[int] = mapped_column(ForeignKey("harvests.id"), nullable=False)
    facility_name: Mapped[str] = mapped_column(String(160), nullable=False)
    booked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    temperature_celsius: Mapped[float | None] = mapped_column(Numeric(4, 1), nullable=True)

    harvest: Mapped["Harvest"] = relationship(back_populates="cold_storage_bookings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ColdStorageBooking {self.id} {self.facility_name} confirmed={self.confirmed}>"
