"""Investment model — tracks investor capital and returns per farm."""

import enum

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class InvestmentStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    completed = "completed"
    refunded = "refunded"


class Investment(Base, TimestampMixin):
    __tablename__ = "investments"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), nullable=False)
    investor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Monetary values stored in kobo (smallest Naira unit).
    amount_ngn: Mapped[int] = mapped_column(Integer, nullable=False)
    shares: Mapped[float] = mapped_column(Numeric(8, 3), default=0, nullable=False)
    expected_return_ngn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_return_ngn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[InvestmentStatus] = mapped_column(
        Enum(InvestmentStatus, name="investment_status"),
        default=InvestmentStatus.pending,
        nullable=False,
    )

    farm: Mapped["Farm"] = relationship(back_populates="investments")
    investor: Mapped["User"] = relationship(back_populates="investments")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Investment {self.id} farm={self.farm_id} {self.status.value}>"
