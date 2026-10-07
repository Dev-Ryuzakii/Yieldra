"""Routes a Tuago payment notice to whatever it pays for.

A Tuago checkout in Yieldra is either a sponsor's naira tranche or a farmer
disbursement Yieldra funds itself. Whichever it is, the claim is re-checked with
Tuago before anything is marked paid, so a forged or replayed notice does nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sponsorship_agent import SponsorshipAgent
from app.models.payout import FarmerDisbursement
from app.services import disbursements


async def settle(
    session: AsyncSession,
    agent: SponsorshipAgent,
    reference: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Confirm the payment identified by a Tuago reference and/or checkout id."""
    if not reference and not session_id:
        return {"status": "ignored", "reason": "no reference"}

    milestone = await agent.find_tuago_milestone(session, reference, session_id)
    if milestone is not None:
        return {"kind": "tranche", **await agent.confirm_tuago_payment(session, milestone)}

    conditions = []
    if session_id:
        conditions.append(FarmerDisbursement.tuago_session_id == session_id)
    if reference:
        conditions.append(FarmerDisbursement.tuago_reference == reference)
    result = await session.execute(
        select(FarmerDisbursement)
        .where(or_(*conditions))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    disbursement = result.scalars().first()
    if disbursement is not None:
        return {"kind": "disbursement", **await disbursements.confirm(session, disbursement)}
    return {"status": "ignored", "reason": "unknown reference"}
