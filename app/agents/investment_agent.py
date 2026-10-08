"""Investment Agent — historical share records and return distribution.

New naira investment creation is paused after retirement of its payment rail.

Model: settings.model_investment (complex financial reasoning).
Human-in-the-loop: payouts above ₦100,000 pause for investor Telegram approval.
Re-confirmation: investments above ₦500,000 require explicit investor confirmation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.config import settings
from app.models.farm import Farm
from app.models.investment import Investment, InvestmentStatus
from app.models.user import User
from app.services.reference import crop_return_multiplier
from app.tools.telegram import send_text_message
from app.utils.logger import get_logger
from app.utils.money import format_naira, naira_to_kobo

log = get_logger("yieldra.agent.investment")

# Human-in-the-loop thresholds (kobo).
PAYOUT_APPROVAL_THRESHOLD = naira_to_kobo(100_000)

SYSTEM_PROMPT = (
    "You are Yieldra's Investment Agent. You manage farm investment transactions on "
    "behalf of investors. You can report on existing investment records, "
    "tokenize farm plot shares, and send Telegram confirmations. Always confirm investment "
    "amounts before creating records. Investments above ₦500,000 require explicit investor "
    "re-confirmation. Respond in the investor's preferred language."
)


class InvestmentAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(model=settings.model_investment, system_prompt=SYSTEM_PROMPT)

    # -- core operations ---------------------------------------------------
    async def process_investment(
        self, session: AsyncSession, investor_id: int, farm_id: int,
        amount_ngn: int, payment_reference: str,
    ) -> dict[str, Any]:
        """The retired naira collection rail cannot verify a new investment."""
        return {"status": "payment_failed", "message": "Investment payments are unavailable"}

    async def calculate_expected_returns(
        self, session: AsyncSession, farm_id: int, investment_amount_ngn: int
    ) -> dict[str, Any]:
        """Estimate returns for a prospective investment based on crop type."""
        farm = await session.get(Farm, farm_id)
        if farm is None:
            return {"status": "error", "message": "farm not found"}
        amount_kobo = naira_to_kobo(investment_amount_ngn)
        multiplier = await crop_return_multiplier(session, farm.crop_type)
        expected = int(round(amount_kobo * multiplier))
        return {
            "farm_id": farm_id,
            "crop_type": farm.crop_type,
            "invested_kobo": amount_kobo,
            "expected_return_kobo": expected,
            "expected_profit_kobo": expected - amount_kobo,
            "multiplier": multiplier,
        }

    async def distribute_returns(
        self, session: AsyncSession, harvest_id: int, gross_proceeds_kobo: int
    ) -> dict[str, Any]:
        """Distribute sale proceeds to investors pro-rata by share.

        Payouts above ₦100,000 are held for Telegram approval (human-in-the-loop).
        """
        from app.models.harvest import Harvest

        harvest = await session.get(Harvest, harvest_id)
        if harvest is None:
            return {"status": "error", "message": "harvest not found"}

        result = await session.execute(
            select(Investment).where(
                Investment.farm_id == harvest.farm_id,
                Investment.status == InvestmentStatus.active,
                # A recorded return means this payout is already queued.
                Investment.actual_return_ngn == 0,
            )
        )
        investments = list(result.scalars())
        if not investments:
            return {"status": "no_investors", "harvest_id": harvest_id}

        total_shares = sum(float(inv.shares) for inv in investments) or 1.0
        payouts: list[dict[str, Any]] = []
        pending_approval: list[dict[str, Any]] = []

        for inv in investments:
            share_frac = float(inv.shares) / total_shares
            payout_kobo = int(round(gross_proceeds_kobo * share_frac))
            investor = await session.get(User, inv.investor_id)

            if payout_kobo > PAYOUT_APPROVAL_THRESHOLD:
                # Hold: ask investor to approve before sending money.
                if investor is not None:
                    await send_text_message(
                        investor.phone,
                        f"Yieldra: Your payout of {format_naira(payout_kobo)} is ready. "
                        f"Reply APPROVE to receive it.",
                    )
                pending_approval.append(
                    {"investment_id": inv.id, "payout_kobo": payout_kobo}
                )
                continue

            # No programmatic payout rail: record what is owed and queue it for a
            # manual transfer. The investment stays active until that is done.
            inv.actual_return_ngn = payout_kobo
            payouts.append(
                {"investment_id": inv.id, "payout_kobo": payout_kobo, "transfer": "manual"}
            )
            if investor is not None:
                await send_text_message(
                    investor.phone,
                    f"Yieldra: Your return of {format_naira(payout_kobo)} is confirmed and is "
                    f"being paid to your account.",
                )

        await session.flush()
        log.info(
            "distribute_returns harvest=%s paid=%d pending=%d",
            harvest_id, len(payouts), len(pending_approval),
        )
        return {
            "status": "completed",
            "harvest_id": harvest_id,
            "paid": payouts,
            "pending_approval": pending_approval,
        }

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _share_percentage(farm: Farm, amount_kobo: int) -> float:
        """Share = this plot's contribution vs. total plots (1 plot per investment)."""
        total = max(farm.total_plots, 1)
        return 100.0 / total
