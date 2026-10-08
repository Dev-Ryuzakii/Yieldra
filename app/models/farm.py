"""Farm and FarmPlot models."""

import enum

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class FarmStatus(str, enum.Enum):
    listed = "listed"
    funded = "funded"
    growing = "growing"
    harvesting = "harvesting"
    completed = "completed"


class PlotStatus(str, enum.Enum):
    available = "available"
    reserved = "reserved"
    owned = "owned"


class Farm(Base, TimestampMixin):
    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    crop_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Editorial cover image only. Milestone evidence is stored separately and verified.
    cover_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_image_alt: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cover_image_credit: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cover_image_source: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_image_license: Mapped[str | None] = mapped_column(String(80), nullable=True)
    total_plots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_plots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[FarmStatus] = mapped_column(
        Enum(FarmStatus, name="farm_status"), default=FarmStatus.listed, nullable=False
    )

    farmer: Mapped["User"] = relationship(back_populates="farms")
    plots: Mapped[list["FarmPlot"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )
    harvests: Mapped[list["Harvest"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )
    investments: Mapped[list["Investment"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Farm {self.id} {self.name} ({self.crop_type})>"


class FarmPlot(Base, TimestampMixin):
    __tablename__ = "farm_plots"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), nullable=False)
    investor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    share_percentage: Mapped[float] = mapped_column(Numeric(6, 3), default=0, nullable=False)
    # Stored in kobo (smallest Naira unit).
    amount_invested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[PlotStatus] = mapped_column(
        Enum(PlotStatus, name="plot_status"), default=PlotStatus.available, nullable=False
    )

    farm: Mapped["Farm"] = relationship(back_populates="plots")
    investor: Mapped["User"] = relationship(back_populates="plots")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FarmPlot {self.id} farm={self.farm_id} {self.status.value}>"
