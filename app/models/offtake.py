"""OfftakeContract model (Buyer is represented by User with role=buyer)."""

import enum

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class ContractStatus(str, enum.Enum):
    draft = "draft"
    signed = "signed"
    paid = "paid"


class OfftakeContract(Base, TimestampMixin):
    __tablename__ = "offtake_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    harvest_id: Mapped[int] = mapped_column(ForeignKey("harvests.id"), nullable=False)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    quantity_kg: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Monetary values stored in kobo (smallest Naira unit).
    price_per_kg: Mapped[int] = mapped_column(Integer, nullable=False)
    total_value: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name="contract_status"),
        default=ContractStatus.draft,
        nullable=False,
    )
    pdf_url: Mapped[str | None] = mapped_column(String(400), nullable=True)

    harvest: Mapped["Harvest"] = relationship(back_populates="offtake_contracts")
    buyer: Mapped["User"] = relationship(back_populates="contracts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OfftakeContract {self.id} harvest={self.harvest_id} {self.status.value}>"
