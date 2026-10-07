"""Sponsorship request/response schemas. Amounts are USD on the way in, cents in the DB."""

from datetime import datetime

from pydantic import BaseModel, Field


class SponsorshipCreate(BaseModel):
    farm_id: int
    sponsor_id: int
    total_usd: float = Field(gt=0, description="Total sponsorship in US dollars")


class EvidenceSubmit(BaseModel):
    photo_url: str = Field(min_length=8, description="https URL or data: URL of the farm photo")


class MilestoneReview(BaseModel):
    approve: bool


class MilestoneRead(BaseModel):
    id: int
    sequence: int
    key: str
    title: str
    evidence_required: str | None
    amount_minor: int = Field(description="Tranche amount in cents")
    amount: str
    status: str
    verdict_summary: str | None
    verdict_confidence: float | None
    verified_at: datetime | None
    paid_at: datetime | None
    paypal_order_id: str | None
    paypal_capture_id: str | None
    failure_reason: str | None


class SponsorshipRead(BaseModel):
    id: int
    reference: str
    status: str
    farm_id: int
    farm_name: str | None
    crop_type: str | None
    sponsor_id: int
    currency: str
    total_minor: int = Field(description="Total in cents")
    total: str
    paid_minor: int
    milestones_paid: int
    next_milestone: MilestoneRead | None
    has_saved_payment_method: bool
    created_at: datetime
    milestones: list[MilestoneRead]


class SponsorshipStarted(BaseModel):
    status: str
    approve_url: str = Field(description="Send the sponsor here to approve on PayPal")
    sponsorship: SponsorshipRead
