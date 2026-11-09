"""Audit trail for operator changes to which farmer owns a farm.

``Farm.farmer_id`` stays the single source of truth for ownership. Each row here
records one change to it: who made it, when, the previous and new farmer, and why.
Sponsorships, payments, payout records and photo evidence are never rewritten.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class FarmAssignmentEvent(Base, TimestampMixin):
    __tablename__ = "farm_assignment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True, nullable=False)
    previous_farmer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    new_farmer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Signed-in operator's user row. Null when the shared operator key was used.
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # session, operator_key or development
    actor_via: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Whether the previous owner had signed in to the web app: registered or unregistered.
    previous_owner_kind: Mapped[str] = mapped_column(String(20), nullable=False)
