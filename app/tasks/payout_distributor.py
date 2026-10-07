"""Payout distributor task — distributes returns for sold harvests.

Runs daily at 09:00 WAT (see beat schedule). Finds sold harvests that still
have active (unpaid) investments and a signed/paid offtake contract, then asks
the Investment Agent to work out each investor's share pro-rata and queue it for a
manual transfer (Tuago, the naira rail, has no payout API). Payouts above ₦100,000
are held for investor Telegram approval (handled inside the agent).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.investment_agent import InvestmentAgent
from app.models.harvest import Harvest, HarvestStatus
from app.models.investment import Investment, InvestmentStatus
from app.models.offtake import ContractStatus, OfftakeContract
from app.tasks.celery_app import celery_app
from app.tasks.runner import run_task
from app.utils.logger import get_logger

log = get_logger("yieldra.task.payout_distributor")


async def _distribute_all(session: AsyncSession) -> dict[str, Any]:
    agent = InvestmentAgent()

    sold = await session.execute(
        select(Harvest).where(Harvest.status == HarvestStatus.sold)
    )
    harvests = list(sold.scalars())

    processed: list[dict[str, Any]] = []
    for harvest in harvests:
        # Only pay out once there are still-active investments to settle.
        active = await session.execute(
            select(Investment.id).where(
                Investment.farm_id == harvest.farm_id,
                Investment.status == InvestmentStatus.active,
                Investment.actual_return_ngn == 0,
            )
        )
        if active.first() is None:
            continue

        # Gross proceeds come from the settled offtake contract for this harvest.
        contract = await session.execute(
            select(OfftakeContract)
            .where(
                OfftakeContract.harvest_id == harvest.id,
                OfftakeContract.status.in_(
                    [ContractStatus.signed, ContractStatus.paid]
                ),
            )
            .order_by(OfftakeContract.total_value.desc())
        )
        deal = contract.scalars().first()
        if deal is None:
            continue

        result = await agent.distribute_returns(session, harvest.id, deal.total_value)
        processed.append({"harvest_id": harvest.id, "result": result})

    return {"status": "ok", "harvests_processed": len(processed), "details": processed}


@celery_app.task(name="yieldra.distribute_completed_payouts")
def distribute_completed_payouts() -> dict[str, Any]:
    result = run_task(_distribute_all)
    log.info("distribute_completed_payouts processed=%s", result.get("harvests_processed"))
    return result
