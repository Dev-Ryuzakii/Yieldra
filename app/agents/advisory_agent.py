"""Farmer Advisory Agent — multilingual crop advice over Telegram.

Model: settings.model_advisory (fast, multilingual: Yoruba / Pidgin / Hausa / English).
Delegates photo diagnosis to the Crop Vision Agent.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.vision_agent import VisionAgent
from app.config import settings
from app.models.farm import Farm
from app.models.user import User
from app.tools.weather import WEATHER_TOOLS, get_weather_forecast
from app.tools.telegram import send_text_message
from app.utils.language import detect_language
from app.utils.logger import get_logger

log = get_logger("yieldra.agent.advisory")

SYSTEM_PROMPT = (
    "You are Yieldra's Farmer Advisory Agent supporting smallholder farmers across "
    "Nigeria. You MUST respond in the farmer's preferred language: Yoruba, Nigerian "
    "Pidgin, Hausa, or English. You can give planting advice, answer questions about "
    "fertilizer and pests, and send weather alerts. Keep responses short and practical "
    "(at most 3 sentences) — most farmers read on basic smartphones. Never give advice "
    "that contradicts safe agricultural practices."
)


class AdvisoryAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            model=settings.model_advisory,
            system_prompt=SYSTEM_PROMPT,
            tools=WEATHER_TOOLS,
        )
        self.vision = VisionAgent()

    async def respond_to_farmer(
        self,
        session: AsyncSession,
        farmer_id: int,
        message: str,
        photo_url: str | None = None,
    ) -> dict[str, Any]:
        """Detect language, build farm context, generate + send a reply via Telegram."""
        farmer = await session.get(User, farmer_id)
        if farmer is None:
            return {"status": "error", "message": "farmer not found"}

        language = (
            farmer.language_preference.value
            if farmer.language_preference
            else detect_language(message)
        )
        if not language or language == "english":
            language = detect_language(message)

        farm = await self._farmer_farm(session, farmer_id)
        crop_type = farm.crop_type if farm else "your crop"
        location = farm.location if farm else "your area"

        # Photo present -> delegate to vision agent.
        if photo_url:
            diag = await self.vision.diagnose_crop_photo(photo_url, crop_type, language)
            reply = diag["diagnosis"]
        else:
            context = {
                "farmer_name": farmer.name,
                "preferred_language": language,
                "crop_type": crop_type,
                "location": location,
                "farm_status": farm.status.value if farm else None,
            }
            result = await self.run_with_tools(
                messages=[{"role": "user", "content": message}],
                context=context,
            )
            reply = result.content

        await send_text_message(farmer.phone, reply)
        log.info("advisory reply farmer=%s lang=%s photo=%s", farmer_id, language, bool(photo_url))
        return {"status": "ok", "language": language, "reply": reply}

    async def send_weather_alert(self, session: AsyncSession, farm_id: int) -> dict[str, Any]:
        """Check the 7-day forecast; if rain/drought, proactively message the farmer."""
        farm = await session.get(Farm, farm_id)
        if farm is None:
            return {"status": "error", "message": "farm not found"}
        farmer = await session.get(User, farm.farmer_id)
        forecast = await get_weather_forecast(farm.location, days=7)
        if not forecast.get("rain_expected"):
            return {"status": "no_alert", "farm_id": farm_id}

        language = farmer.language_preference.value if farmer else "english"
        context = {"forecast": forecast, "crop_type": farm.crop_type, "language": language}
        result = await self.run(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Write a short weather alert for a {farm.crop_type} farmer in "
                        f"{language}. Rain is expected on days {forecast['rain_days']}."
                    ),
                }
            ],
            context=context,
        )
        if farmer is not None:
            await send_text_message(farmer.phone, result)
        return {"status": "alert_sent", "farm_id": farm_id, "message": result}

    # -- helpers -----------------------------------------------------------
    @staticmethod
    async def _farmer_farm(session: AsyncSession, farmer_id: int) -> Farm | None:
        result = await session.execute(
            select(Farm).where(Farm.farmer_id == farmer_id).limit(1)
        )
        return result.scalars().first()
