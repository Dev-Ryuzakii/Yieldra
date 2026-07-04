"""Lookups for DB-backed reference data (crop parameters, cold storage facilities).

Each function degrades to a safe default if the row hasn't been populated yet, so
agents keep working before the reference tables are filled via the CRUD endpoints.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reference import ColdStorageFacility, CropParameter
from app.utils.money import naira_to_kobo

# Fallbacks used only when a crop has no CropParameter row yet.
DEFAULT_MULTIPLIER = 1.20
DEFAULT_PRICE_PER_KG_KOBO = naira_to_kobo(200)
DEFAULT_HARVEST_MONTHS = 4
DEFAULT_SPACING_CM = 50


async def get_crop_param(session: AsyncSession, crop_type: str) -> CropParameter | None:
    result = await session.execute(
        select(CropParameter).where(CropParameter.crop_type.ilike(crop_type))
    )
    return result.scalars().first()


async def crop_return_multiplier(session: AsyncSession, crop_type: str) -> float:
    cp = await get_crop_param(session, crop_type)
    return float(cp.return_multiplier) if cp else DEFAULT_MULTIPLIER


async def crop_price_per_kg(session: AsyncSession, crop_type: str) -> int:
    cp = await get_crop_param(session, crop_type)
    return int(cp.price_per_kg) if cp else DEFAULT_PRICE_PER_KG_KOBO


async def crop_calendar(session: AsyncSession, crop_type: str) -> dict:
    cp = await get_crop_param(session, crop_type)
    if cp is None:
        return {
            "crop_type": crop_type,
            "plant": "consult local extension officer",
            "harvest_months": DEFAULT_HARVEST_MONTHS,
            "spacing_cm": DEFAULT_SPACING_CM,
        }
    return {
        "crop_type": cp.crop_type,
        "plant": cp.plant_window or "consult local extension officer",
        "harvest_months": cp.harvest_months,
        "spacing_cm": cp.spacing_cm,
    }


async def available_facilities(
    session: AsyncSession, location: str, quantity_kg: float
) -> list[ColdStorageFacility]:
    """Active facilities whose location matches (substring, case-insensitive)."""
    result = await session.execute(
        select(ColdStorageFacility).where(ColdStorageFacility.active.is_(True))
    )
    loc = (location or "").lower()
    facilities = [
        f for f in result.scalars()
        if not loc or loc in f.location.lower() or f.location.lower() in loc
    ]
    return facilities
