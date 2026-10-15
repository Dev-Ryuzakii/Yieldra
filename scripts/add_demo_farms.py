"""Add the demo farms and their farmers to any database, including Afribase.

Unlike ``seed_db.py`` this never deletes anything: it inserts each farmer and
farm only if a row with the same phone or farm name is not already there, so it
is safe to run against the hosted database more than once.

    DATABASE_URL=postgresql+asyncpg://... python scripts/add_demo_farms.py

Run ``scripts/sync_crop_images.py`` afterwards for the farm cover photos.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import async_session_factory, engine
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


async def main() -> None:
    added = 0
    async with async_session_factory() as session:
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
        await session.commit()
    await engine.dispose()
    print(f"Added {added} farm(s); existing farms were left unchanged.")


if __name__ == "__main__":
    asyncio.run(main())
