"""The three demo farms and their farmers, added without deleting anything.

The app calls ``add_if_empty`` at startup so a fresh hosted database has farms
to sponsor; ``scripts/add_demo_farms.py`` calls ``add_missing`` by hand.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.farm import Farm, FarmStatus
from app.models.user import Language, User, UserRole

FARMS = [
    ("Adunni Okafor", "+2348030000001", Language.yoruba,
     "Adunni Cassava Plot", "Ogun", "cassava", 10),
    ("Musa Ibrahim", "+2348030000002", Language.hausa,
     "Musa Maize Field", "Benue", "maize", 8),
    ("Grace Eze", "+2348030000003", Language.english,
     "Grace Tomato Garden", "Kano", "tomatoes", 12),
]


async def add_missing(session: AsyncSession) -> int:
    """Insert each demo farmer and farm that is not there yet. Returns farms added."""
    added = 0
    for name, phone, language, farm_name, location, crop, plots in FARMS:
        farmer = (await session.execute(select(User).where(User.phone == phone))).scalars().first()
        if farmer is None:
            farmer = User(name=name, phone=phone, role=UserRole.farmer, language_preference=language)
            session.add(farmer)
            await session.flush()
        exists = await session.execute(select(Farm.id).where(Farm.name == farm_name))
        if exists.first() is None:
            session.add(Farm(name=farm_name, farmer_id=farmer.id, location=location,
                             crop_type=crop, total_plots=plots, available_plots=plots,
                             status=FarmStatus.listed))
            added += 1
    await session.flush()
    return added


async def add_if_empty(session: AsyncSession) -> int:
    """Add the demo farms only when the database has no farms at all."""
    if (await session.execute(select(func.count(Farm.id)))).scalar_one():
        return 0
    return await add_missing(session)
