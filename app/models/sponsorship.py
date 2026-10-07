"""Sponsorship models — a sponsor funds a farm in tranches released on verified milestones.

A ``Sponsorship`` is paid through PayPal. The first tranche is captured when the
sponsor approves, which also saves their PayPal account; each later tranche is
charged to that saved account only after the farmer's photo for the matching
``SponsorshipMilestone`` passes verification.

Money is stored as integers in minor units of ``currency`` (US cents for USD).
"""

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class SponsorshipStatus(str, enum.Enum):
    pending_approval = "pending_approval"  # PayPal order created, sponsor has not approved
    active = "active"                      # first tranche paid, later tranches outstanding
    completed = "completed"                # every tranche paid
    cancelled = "cancelled"                # stopped; no further charges possible


class MilestoneStatus(str, enum.Enum):
    locked = "locked"                        # an earlier milestone is still open
    awaiting_evidence = "awaiting_evidence"  # farmer should send a photo
    needs_review = "needs_review"            # verification unsure; a person decides
    payment_failed = "payment_failed"        # verified, but the PayPal charge failed
    paid = "paid"                            # verified and charged


class Sponsorship(Base, TimestampMixin):
    __tablename__ = "sponsorships"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Random public reference; also the root of every PayPal idempotency key.
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True, nullable=False)
    sponsor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[SponsorshipStatus] = mapped_column(
        Enum(SponsorshipStatus, name="sponsorship_status"),
        default=SponsorshipStatus.pending_approval,
        nullable=False,
    )
    # The PayPal order the sponsor approves (pays the first tranche).
    paypal_order_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    # PayPal payment token for the sponsor's saved account. It authorises charges, so
    # it is never returned by the API; encrypt this column before running in production.
    paypal_vault_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    farm: Mapped["Farm"] = relationship()
    sponsor: Mapped["User"] = relationship()
    milestones: Mapped[list["SponsorshipMilestone"]] = relationship(
        back_populates="sponsorship",
        cascade="all, delete-orphan",
        order_by="SponsorshipMilestone.sequence",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Sponsorship {self.id} farm={self.farm_id} {self.status.value}>"


class SponsorshipMilestone(Base, TimestampMixin):
    __tablename__ = "sponsorship_milestones"
    __table_args__ = (
        UniqueConstraint("sponsorship_id", "sequence", name="uq_sponsorship_milestone_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sponsorship_id: Mapped[int] = mapped_column(
        ForeignKey("sponsorships.id"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    # What the farmer's photo must show. Null for the first tranche (paid on approval).
    evidence_required: Mapped[str | None] = mapped_column(Text, nullable=True)
    share_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, name="milestone_status"),
        default=MilestoneStatus.locked,
        nullable=False,
    )

    # Latest verification of the farmer's photo.
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    verdict_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Payment for this tranche.
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Charges PayPal has definitively refused. Part of the idempotency key, so a retry
    # after a refusal is a new request but a retry after a timeout is not.
    payment_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    sponsorship: Mapped["Sponsorship"] = relationship(back_populates="milestones")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SponsorshipMilestone {self.id} #{self.sequence} {self.status.value}>"
