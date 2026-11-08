"""AI photo checks. These rows never authorize a PayPal charge or a farmer payout."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base
from app.models.base import TimestampMixin


class PhotoCheck(Base, TimestampMixin):
    __tablename__ = "photo_checks"
    __table_args__ = (
        UniqueConstraint("milestone_id", "evidence_sha256", name="uq_photo_check_milestone_digest"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True, nullable=False)
    milestone_id: Mapped[int] = mapped_column(
        ForeignKey("sponsorship_milestones.id"), index=True, nullable=False
    )
    stage_key: Mapped[str] = mapped_column(String(40), nullable=False)
    crop_type: Mapped[str] = mapped_column(String(80), nullable=False)
    milestone_title: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_required: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(40), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # pending, needs_review, approved, rejected, new_photo_requested, superseded
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="cencori", nullable=False)
    # live, mock, or blocked. Set when the analysis attempt finishes.
    provider_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    issues: Mapped[list | None] = mapped_column(JSON, nullable=True)
    limitations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommended_disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_reported_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    visible_text_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    submitted_by_role: Mapped[str] = mapped_column(String(20), nullable=False)
    submitted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PhotoCheckReview(Base, TimestampMixin):
    """One human decision. Recording it does not charge PayPal or pay a farmer."""

    __tablename__ = "photo_check_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    photo_check_id: Mapped[int] = mapped_column(
        ForeignKey("photo_checks.id"), index=True, nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
