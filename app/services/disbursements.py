"""Record naira obligations for PayPal-funded farm milestones."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.farm import Farm
from app.models.payout import DisbursementStatus, FarmerDisbursement
from app.models.sponsorship import Sponsorship, SponsorshipMilestone
from app.utils.money import format_naira


def convert_to_kobo(source_minor: int, rate: float) -> int:
    """US cents -> kobo at ``rate`` naira per dollar (cents x rate == kobo)."""
    return int(round(source_minor * rate))


async def for_milestone(session: AsyncSession, milestone_id: int) -> FarmerDisbursement | None:
    result = await session.execute(
        select(FarmerDisbursement).where(FarmerDisbursement.milestone_id == milestone_id)
    )
    return result.scalars().first()


async def create_for_milestone(
    session: AsyncSession,
    sponsorship: Sponsorship,
    milestone: SponsorshipMilestone,
    farm: Farm,
) -> FarmerDisbursement:
    """Record a farmer liability for a paid PayPal tranche. Safe to call twice."""
    existing = await for_milestone(session, milestone.id)
    if existing is not None:
        return existing
    rate = float(settings.usd_ngn_rate)
    disbursement = FarmerDisbursement(
        milestone_id=milestone.id,
        farmer_id=farm.farmer_id,
        source_currency=sponsorship.currency,
        source_minor=milestone.amount_minor,
        rate=rate,
        amount_kobo=convert_to_kobo(milestone.amount_minor, rate),
        status=DisbursementStatus.pending_manual.value,
    )
    session.add(disbursement)
    await session.flush()
    return disbursement


def describe(disbursement: FarmerDisbursement, operator: bool = False) -> dict[str, Any]:
    """API shape. Funding account details are for the operator only."""
    out: dict[str, Any] = {
        "id": disbursement.id,
        "milestone_id": disbursement.milestone_id,
        "status": disbursement.status,
        "amount_kobo": disbursement.amount_kobo,
        "amount": format_naira(disbursement.amount_kobo),
        "rate": float(disbursement.rate),
        "paid_at": disbursement.paid_at,
    }
    if operator:
        out.update(
            farmer_id=disbursement.farmer_id,
            source_minor=disbursement.source_minor,
            source_currency=disbursement.source_currency,
            tuago_reference=disbursement.tuago_reference,
            pay_bank_name=disbursement.pay_bank_name,
            pay_account_number=disbursement.pay_account_number,
            pay_account_name=disbursement.pay_account_name,
            pay_expires_at=disbursement.pay_expires_at,
            created_at=disbursement.created_at,
        )
    return out
