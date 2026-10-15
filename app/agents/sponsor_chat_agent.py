"""Sponsor Chat Agent — answers sponsors' questions in the website chat.

Model: settings.model_advisory (fast). It explains farms, plot prices and how
milestone payments work, using live farm data as context. It never takes
payment in chat; it points sponsors to the "Sponsor this farm" button.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.config import settings
from app.models.farm import Farm
from app.models.user import User
from app.services.plots import plot_price_usd

SYSTEM_PROMPT = (
    "You are Yieldra's assistant on the website, helping people who want to sponsor "
    "smallholder farms in Nigeria. Answer in the sponsor's language, in at most four short "
    "sentences, warmly and plainly.\n"
    "Facts you can rely on:\n"
    "- A sponsor picks a farm and how many plots to back. Total = plots x the crop's price "
    "per plot in US dollars (prices are in the context).\n"
    "- The total is paid through PayPal in four stages: Seed and land 20%, Planted 30%, "
    "Established 30%, Harvest 20%. Only the first is paid at checkout; the PayPal account "
    "is saved and each later stage is charged only after the farmer's photo for that stage "
    "passes an AI check. Unclear photos go to a person to review.\n"
    "- A sponsor can stop at any time from their sponsorship page; paid stages stay paid "
    "and no later stage is charged.\n"
    "- This is not an investment: sponsors get photos and updates, not a financial return.\n"
    "- Each paid stage records an amount owed to the farmer in naira. Farmer payouts are "
    "currently pending while a settlement method is set up. Never claim the farmer has "
    "already received money.\n"
    "- Payments run in PayPal's test (sandbox) mode for this demo.\n"
    "Only mention farms listed in the context, with their real plots left and prices. You "
    "cannot take payments or sign people up in chat: to sponsor, they press 'Sponsor this "
    "farm' on a farm card. If asked something you don't know, say so."
)


class SponsorChatAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(model=settings.model_advisory, system_prompt=SYSTEM_PROMPT)

    async def reply(
        self, session: AsyncSession, messages: list[dict], extra_context: dict | None = None
    ) -> str:
        context = {"open_farms": await open_farms(session), **(extra_context or {})}
        return await self.run(messages=messages, context=context)


async def open_farms(session: AsyncSession) -> list[dict]:
    """Farms with their plots left and price per plot, as the assistant sees them."""
    farms = list((await session.execute(select(Farm).order_by(Farm.id))).scalars())
    listed = []
    for farm in farms:
        farmer = await session.get(User, farm.farmer_id)
        listed.append({
            "name": farm.name,
            "crop": farm.crop_type,
            "location": f"{farm.location}, Nigeria",
            "farmer_first_name": farmer.name.split()[0] if farmer else None,
            "plots_left": farm.available_plots,
            "price_per_plot_usd": plot_price_usd(farm.crop_type),
        })
    return listed


def farms_text(farms: list[dict]) -> str:
    """The open farms as a chat message, for when no model is available."""
    open_ = [farm for farm in farms if farm["plots_left"] > 0]
    if not open_:
        return "Yieldra: No farms are open for sponsorship right now. Check back soon."
    lines = [
        f"- {farm['name']} ({farm['crop']}, {farm['location']}): ${farm['price_per_plot_usd']} "
        f"per plot, {farm['plots_left']} plot(s) left"
        for farm in open_
    ]
    site = settings.public_base_url.rstrip("/")
    return (
        "Yieldra: Farms you can sponsor:\n" + "\n".join(lines)
        + f"\n\nTo sponsor, open {site}/#farms and press 'Sponsor this farm'."
    )
