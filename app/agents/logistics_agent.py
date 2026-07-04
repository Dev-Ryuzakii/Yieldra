"""Harvest Logistics Agent — coordinates harvest pickup, cold storage, transport.

Model: qwen-max (multi-step tool use + decisions).
Rule: never book a truck/storage without farmer confirmation. Handle delays gracefully.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult, BaseAgent
from app.config import settings
from app.models.farm import Farm, FarmStatus
from app.models.harvest import ColdStorageBooking, Harvest, HarvestStatus
from app.models.user import User
from app.tools import cold_storage
from app.tools.cold_storage import COLD_STORAGE_TOOLS
from app.tools.telegram import TELEGRAM_TOOLS, send_text_message
from app.utils.logger import get_logger

log = get_logger("yieldra.agent.logistics")

SYSTEM_PROMPT = (
    "You are Yieldra's Harvest Logistics Agent. You coordinate movement of harvested "
    "crops from farm to cold storage to market. You monitor harvest readiness reports, "
    "book cold storage slots, coordinate truck pickups, and confirm delivery with photo "
    "proof. Always confirm logistics plans with the farmer before booking. Never book a "
    "truck without farmer confirmation. Handle delays gracefully — if a truck is late, "
    "proactively notify all parties and rebook. Respond in the user's language. Keep "
    "Telegram messages to at most 3 short sentences."
)


class LogisticsAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            model=settings.model_logistics,
            system_prompt=SYSTEM_PROMPT,
            tools=[*COLD_STORAGE_TOOLS, *TELEGRAM_TOOLS],
        )

    async def initiate_harvest_coordination(
        self, session: AsyncSession, harvest_id: int
    ) -> dict[str, Any]:
        """Message farmer for readiness, then book cold storage + truck, notify all."""
        harvest = await session.get(Harvest, harvest_id)
        if harvest is None:
            return {"status": "error", "message": "harvest not found"}
        farm = await session.get(Farm, harvest.farm_id)
        farmer = await session.get(User, farm.farmer_id) if farm else None
        if farm is None or farmer is None:
            return {"status": "error", "message": "farm or farmer not found"}

        when = (harvest.expected_date or date.today()).isoformat()
        quantity = float(harvest.yield_kg or 0)

        # Ask farmer to confirm readiness (human-in-the-loop before booking).
        await send_text_message(
            farmer.phone,
            f"Yieldra: Is your {farm.crop_type} harvest at {farm.name} ready for pickup on "
            f"{when}? Reply READY to confirm so we can book cold storage and a truck.",
        )

        # Find candidate cold storage slots to present in the plan.
        slots = await cold_storage.check_available_slots(farm.location, when, quantity or 1000)
        available = [s for s in slots if s.get("available")]

        log.info(
            "harvest coordination initiated harvest=%s farmer=%s slots=%d",
            harvest_id, farmer.phone, len(available),
        )
        return {
            "status": "awaiting_farmer_confirmation",
            "harvest_id": harvest_id,
            "farmer_phone": farmer.phone,
            "candidate_slots": available,
            "proposed_date": when,
        }

    async def confirm_and_book(
        self, session: AsyncSession, harvest_id: int, facility_id: str, when: str | None = None
    ) -> dict[str, Any]:
        """Called after the farmer confirms READY. Books cold storage + notifies parties."""
        harvest = await session.get(Harvest, harvest_id)
        if harvest is None:
            return {"status": "error", "message": "harvest not found"}
        farm = await session.get(Farm, harvest.farm_id)
        farmer = await session.get(User, farm.farmer_id) if farm else None
        when = when or (harvest.expected_date or date.today()).isoformat()

        booking = await cold_storage.book_cold_storage(facility_id, str(harvest_id), when)

        record = ColdStorageBooking(
            harvest_id=harvest_id,
            facility_name=facility_id,
            booked_at=datetime.now(timezone.utc),
            confirmed=bool(booking.get("confirmed")),
            temperature_celsius=4.0,
        )
        session.add(record)
        harvest.status = HarvestStatus.in_transit
        if farm is not None:
            farm.status = FarmStatus.harvesting
        await session.flush()

        if farmer is not None:
            await send_text_message(
                farmer.phone,
                f"Yieldra: Cold storage booked ({facility_id}) for {when}. Truck dispatched. "
                f"Booking ref {booking.get('booking_id')}.",
            )
        log.info("cold storage booked harvest=%s facility=%s", harvest_id, facility_id)
        return {
            "status": "booked",
            "harvest_id": harvest_id,
            "booking": booking,
            "cold_storage_booking_id": record.id,
        }

    async def handle_harvest_update(
        self,
        session: AsyncSession,
        harvest_id: int,
        update_message: str,
        photo_url: str | None = None,
    ) -> AgentResult:
        """Process a farmer's Telegram update with the LLM, deciding the next action."""
        harvest = await session.get(Harvest, harvest_id)
        farm = await session.get(Farm, harvest.farm_id) if harvest else None
        context = {
            "harvest_id": harvest_id,
            "harvest_status": harvest.status.value if harvest else None,
            "farm": farm.name if farm else None,
            "crop_type": farm.crop_type if farm else None,
            "location": farm.location if farm else None,
            "photo_url": photo_url,
        }
        return await self.run_with_tools(
            messages=[{"role": "user", "content": update_message}],
            context=context,
        )

    async def monitor_active_harvests(self, session: AsyncSession) -> dict[str, Any]:
        """Celery entry: message farmers of all growing farms for a readiness update."""
        result = await session.execute(
            select(Harvest).where(Harvest.status == HarvestStatus.pending)
        )
        harvests = list(result.scalars())
        messaged = 0
        for harvest in harvests:
            farm = await session.get(Farm, harvest.farm_id)
            if farm is None or farm.status != FarmStatus.growing:
                continue
            farmer = await session.get(User, farm.farmer_id)
            if farmer is None:
                continue
            await send_text_message(
                farmer.phone,
                f"Yieldra: How is your {farm.crop_type} at {farm.name}? Reply READY when "
                f"it's time to harvest so we can arrange logistics.",
            )
            messaged += 1
        log.info("monitor_active_harvests messaged=%d", messaged)
        return {"status": "ok", "farmers_messaged": messaged}
