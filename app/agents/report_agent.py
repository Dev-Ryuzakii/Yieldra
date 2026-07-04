"""Portfolio Report Agent — weekly investment performance reports for investors.

Model: qwen-turbo (fast, cheap summaries).
Rules: clear friendly language, specific numbers, honest about issues, always end
with the estimated payout date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.config import settings
from app.models.farm import Farm
from app.models.harvest import Harvest, HarvestStatus
from app.models.investment import Investment, InvestmentStatus
from app.models.user import User, UserRole
from app.services.reference import crop_calendar
from app.tools.telegram import send_text_message
from app.utils.logger import get_logger
from app.utils.money import format_naira

log = get_logger("yieldra.agent.report")

SYSTEM_PROMPT = (
    "You are Yieldra's Portfolio Report Agent. You keep investors informed about their "
    "farm investments. Write reports in clear, friendly language. Include specific numbers "
    "— yields, timelines, returns. If a farm is underperforming or facing issues, be honest "
    "but constructive. Always end with the estimated payout date. Keep it concise — this is "
    "delivered over Telegram. Respond in the investor's preferred language."
)


class ReportAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(model=settings.model_report, system_prompt=SYSTEM_PROMPT)

    async def generate_investor_report(
        self, session: AsyncSession, investor_id: int
    ) -> str:
        """Aggregate an investor's active positions and produce a plain-language summary."""
        investor = await session.get(User, investor_id)
        if investor is None:
            return ""

        summary = await self._portfolio_summary(session, investor_id)
        if not summary["positions"]:
            return (
                f"Hi {investor.name.split()[0]}, you have no active Yieldra investments "
                f"right now. Browse open farms to start earning."
            )

        try:
            report = await self.run(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Write this investor's weekly portfolio update as a short Telegram "
                            "message. Use the figures provided. End with the estimated payout date."
                        ),
                    }
                ],
                context={"investor_name": investor.name, **summary},
            )
        except Exception:  # noqa: BLE001 — never let a model outage drop the report
            log.exception("report model call failed; using deterministic fallback")
            report = ""
        return report or self._fallback_text(investor, summary)

    async def send_weekly_reports(self, session: AsyncSession) -> dict[str, Any]:
        """Celery entry: generate + Telegram a report to every investor with active money."""
        result = await session.execute(
            select(User).where(User.role == UserRole.investor)
        )
        investors = list(result.scalars())
        sent = 0
        for investor in investors:
            report = await self.generate_investor_report(session, investor.id)
            if not report:
                continue
            await send_text_message(investor.phone, report)
            sent += 1
        log.info("send_weekly_reports sent=%d", sent)
        return {"status": "ok", "reports_sent": sent}

    # -- helpers -----------------------------------------------------------
    async def _portfolio_summary(
        self, session: AsyncSession, investor_id: int
    ) -> dict[str, Any]:
        result = await session.execute(
            select(Investment).where(
                Investment.investor_id == investor_id,
                Investment.status == InvestmentStatus.active,
            )
        )
        investments = list(result.scalars())

        positions: list[dict[str, Any]] = []
        total_invested = 0
        total_expected = 0
        soonest_payout: date | None = None

        for inv in investments:
            farm = await session.get(Farm, inv.farm_id)
            if farm is None:
                continue
            harvest = await self._latest_harvest(session, farm.id)
            payout_eta = await self._estimate_payout_date(session, farm, harvest)
            if payout_eta and (soonest_payout is None or payout_eta < soonest_payout):
                soonest_payout = payout_eta

            total_invested += inv.amount_ngn
            total_expected += inv.expected_return_ngn
            positions.append(
                {
                    "farm": farm.name,
                    "crop_type": farm.crop_type,
                    "location": farm.location,
                    "farm_status": farm.status.value,
                    "harvest_status": harvest.status.value if harvest else "pending",
                    "yield_kg": float(harvest.yield_kg) if harvest and harvest.yield_kg else None,
                    "invested": format_naira(inv.amount_ngn),
                    "expected_return": format_naira(inv.expected_return_ngn),
                    "share_pct": float(inv.shares),
                    "payout_eta": payout_eta.isoformat() if payout_eta else "TBD",
                }
            )

        return {
            "positions": positions,
            "total_invested": format_naira(total_invested),
            "total_expected_return": format_naira(total_expected),
            "expected_profit": format_naira(total_expected - total_invested),
            "estimated_payout_date": soonest_payout.isoformat() if soonest_payout else "TBD",
        }

    @staticmethod
    async def _latest_harvest(session: AsyncSession, farm_id: int) -> Harvest | None:
        result = await session.execute(
            select(Harvest)
            .where(Harvest.farm_id == farm_id)
            .order_by(Harvest.id.desc())
        )
        return result.scalars().first()

    @staticmethod
    async def _estimate_payout_date(
        session: AsyncSession, farm: Farm, harvest: Harvest | None
    ) -> date | None:
        """Payout follows the sale. Use harvest dates when known, else crop calendar."""
        if harvest and harvest.status == HarvestStatus.sold:
            base = harvest.actual_date or date.today()
            return base + timedelta(days=7)
        if harvest and harvest.expected_date:
            return harvest.expected_date + timedelta(days=14)
        cal = await crop_calendar(session, farm.crop_type)
        months = int(cal.get("harvest_months", 4))
        return date.today() + timedelta(days=months * 30 + 14)

    def _fallback_text(self, investor: User, summary: dict[str, Any]) -> str:
        """Used only if the model returns empty (keeps the pipeline robust)."""
        lines = [f"Hi {investor.name.split()[0]}, your Yieldra update:"]
        for p in summary["positions"]:
            lines.append(
                f"• {p['farm']} ({p['crop_type']}): {p['farm_status']}, "
                f"invested {p['invested']}, est. return {p['expected_return']}."
            )
        lines.append(
            f"Total invested {summary['total_invested']}, "
            f"expected {summary['total_expected_return']}. "
            f"Estimated payout: {summary['estimated_payout_date']}."
        )
        return "\n".join(lines)
