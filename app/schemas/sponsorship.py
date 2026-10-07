"""Sponsorship request/response schemas.

Amounts arrive in major units (dollars or naira) and are stored in minor units.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SponsorshipCreate(BaseModel):
    """Start a sponsorship for an existing user (chat sponsors, scripts)."""

    farm_id: int
    sponsor_id: int
    total_usd: float | None = Field(None, gt=0, description="Total in US dollars (PayPal)")
    total_ngn: float | None = Field(None, gt=0, description="Total in naira (Tuago)")


class SponsorCheckout(BaseModel):
    """Start a sponsorship from the web: the sponsor is identified by email."""

    farm_id: int
    amount: float = Field(gt=0, description="Total, in dollars for paypal or naira for tuago")
    rail: Literal["paypal", "tuago"] = "paypal"
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=160, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EvidenceSubmit(BaseModel):
    photo_url: str = Field(min_length=8, description="https URL or data: URL of the farm photo")


class MilestoneReview(BaseModel):
    approve: bool


class FarmerPayoutRead(BaseModel):
    id: int
    milestone_id: int
    status: str
    amount_kobo: int
    amount: str
    rate: float
    paid_at: datetime | None


class MilestoneRead(BaseModel):
    id: int
    sequence: int
    key: str
    title: str
    evidence_required: str | None
    amount_minor: int = Field(description="Tranche amount in minor units")
    amount: str
    status: str
    verdict_summary: str | None
    verdict_confidence: float | None
    evidence_photo_url: str | None
    sponsor_update: str | None
    verified_at: datetime | None
    paid_at: datetime | None
    payment_url: str | None = Field(description="Tuago checkout to pay, when one is open")
    paypal_order_id: str | None
    paypal_capture_id: str | None
    failure_reason: str | None
    farmer_payout: FarmerPayoutRead | None


class SponsorshipRead(BaseModel):
    id: int
    reference: str
    track_url: str
    status: str
    rail: str
    farm_id: int
    farm_name: str | None
    crop_type: str | None
    location: str | None
    sponsor_id: int
    currency: str
    total_minor: int = Field(description="Total in minor units")
    total: str
    paid_minor: int
    paid: str
    milestones_paid: int
    next_milestone: MilestoneRead | None
    has_saved_payment_method: bool
    created_at: datetime
    milestones: list[MilestoneRead]


class SponsorshipStarted(BaseModel):
    status: str
    approve_url: str = Field(description="Send the sponsor here to pay the first tranche")
    track_url: str = Field(description="The sponsor's private page for this sponsorship")
    sponsorship: SponsorshipRead
