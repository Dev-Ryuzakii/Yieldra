"""Operator console data — every tranche and farmer payout in one ledger."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.farm import Farm
from app.models.payout import FarmerDisbursement, PayoutAccount
from app.models.sponsorship import Sponsorship, SponsorshipMilestone
from app.models.user import User
from app.routers.deps import require_operator
from app.services import evidence_store

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/ledger")
async def ledger(
    session: AsyncSession = Depends(get_session), _: None = Depends(require_operator)
) -> dict:
    """One row per tranche, with its verdict, payment and farmer payout."""
    result = await session.execute(
        select(SponsorshipMilestone, Sponsorship, Farm)
        .join(Sponsorship, SponsorshipMilestone.sponsorship_id == Sponsorship.id)
        .join(Farm, Sponsorship.farm_id == Farm.id)
        .order_by(Sponsorship.id.desc(), SponsorshipMilestone.sequence)
    )
    triples = result.all()
    users = {u.id: u for u in (await session.execute(select(User))).scalars()}
    payouts = {
        d.milestone_id: d for d in (await session.execute(select(FarmerDisbursement))).scalars()
    }
    banked = {a.user_id for a in (await session.execute(select(PayoutAccount))).scalars()}

    rows = []
    for milestone, sponsorship, farm in triples:
        sponsor = users.get(sponsorship.sponsor_id)
        farmer = users.get(farm.farmer_id)
        payout = payouts.get(milestone.id)
        rows.append(
            {
                "milestone_id": milestone.id,
                "sponsorship_id": sponsorship.id,
                "reference": sponsorship.reference,
                "sponsorship_status": sponsorship.status.value,
                "rail": sponsorship.rail,
                "currency": sponsorship.currency,
                "farm_id": farm.id,
                "farm": farm.name,
                "crop": farm.crop_type,
                "farmer": farmer.name if farmer else None,
                "farmer_id": farm.farmer_id,
                "farmer_has_bank": farm.farmer_id in banked,
                "sponsor": sponsor.name if sponsor else None,
                "sequence": milestone.sequence,
                "stage": milestone.title,
                "status": milestone.status.value,
                "amount": milestone.amount_minor / 100,
                "confidence": (
                    float(milestone.verdict_confidence)
                    if milestone.verdict_confidence is not None
                    else None
                ),
                "verdict": milestone.verdict_summary,
                "photo_url": evidence_store.url_for(milestone.evidence_photo),
                "payment_reference": milestone.payment_reference,
                "paypal_capture_id": milestone.paypal_capture_id,
                "failure_reason": milestone.failure_reason,
                "verified_at": milestone.verified_at,
                "paid_at": milestone.paid_at,
                "payout_id": payout.id if payout else None,
                "payout_status": payout.status if payout else None,
                "payout_naira": payout.amount_kobo / 100 if payout else None,
                "payout_bank": payout.pay_bank_name if payout else None,
                "payout_account": payout.pay_account_number if payout else None,
                "payout_account_name": payout.pay_account_name if payout else None,
            }
        )
    return {"rows": rows}
