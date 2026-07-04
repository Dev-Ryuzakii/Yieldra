"""Weather + crop calendar tools. Mock data (deterministic) for development."""

from __future__ import annotations

import hashlib
from typing import Any

from app.agents.base import Tool
from app.database import async_session_factory
from app.services.reference import crop_calendar
from app.utils.logger import get_logger

log = get_logger("yieldra.tools.weather")

_CONDITIONS = ["sunny", "partly cloudy", "rain", "heavy rain", "dry/hot"]


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16)


async def get_weather_forecast(location: str, days: int = 7) -> dict[str, Any]:
    """Return a deterministic mock ``days``-day forecast for a location."""
    base = _seed(location.lower())
    forecast = []
    for d in range(min(days, 14)):
        cond = _CONDITIONS[(base + d) % len(_CONDITIONS)]
        temp = 22 + ((base + d) % 12)
        forecast.append({"day": d + 1, "condition": cond, "temp_c": temp})
    rain_days = [f["day"] for f in forecast if "rain" in f["condition"]]
    log.info("weather location=%s days=%d rain_days=%s", location, days, rain_days)
    return {
        "location": location,
        "forecast": forecast,
        "rain_expected": bool(rain_days),
        "rain_days": rain_days,
    }


async def get_crop_calendar(crop_type: str, location: str) -> dict[str, Any]:
    """Return planting/harvest guidance for a crop type (from crop_parameters)."""
    async with async_session_factory() as session:
        info = await crop_calendar(session, crop_type)
    return {"location": location, **info}


WEATHER_TOOLS: list[Tool] = [
    Tool(
        name="get_weather_forecast",
        description="Get a multi-day weather forecast for a farm location.",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "days": {"type": "integer", "default": 7},
            },
            "required": ["location"],
        },
        handler=get_weather_forecast,
    ),
    Tool(
        name="get_crop_calendar",
        description="Get planting and harvest timing guidance for a crop type and location.",
        parameters={
            "type": "object",
            "properties": {
                "crop_type": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["crop_type", "location"],
        },
        handler=get_crop_calendar,
    ),
]
