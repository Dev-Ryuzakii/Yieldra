"""The demo farms and their farmers, added without deleting anything.

The app calls ``add_missing`` at startup (unless DEMO_FARMS=false) so a hosted
database has farms to sponsor; ``scripts/add_demo_farms.py`` does the same by hand.
"""

from __future__ import annotations

from sqlalchemy import select
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
    ("Chinedu Okeke", "+2348030000004", Language.english,
     "Okeke Rice Paddy", "Ebonyi", "rice", 15),
    ("Halima Sani", "+2348030000005", Language.hausa,
     "Halima Groundnut Farm", "Kano", "groundnuts", 10),
    ("Bisi Adeyemi", "+2348030000006", Language.yoruba,
     "Bisi Pepper Rows", "Oyo", "peppers", 6),
    ("Ibrahim Yusuf", "+2348030000007", Language.hausa,
     "Yusuf Maize Acres", "Kaduna", "maize", 20),
    ("Ngozi Eze", "+2348030000008", Language.english,
     "Ngozi Cassava Garden", "Enugu", "cassava", 12),
    ("Tunde Bello", "+2348030000009", Language.yoruba,
     "Bello Tomato Greenhouse", "Plateau", "tomatoes", 8),
    ("Amina Garba", "+2348030000010", Language.hausa,
     "Garba Rice Fields", "Niger", "rice", 18),
    ("Emeka Nwankwo", "+2348030000011", Language.english,
     "Nwankwo Pepper Farm", "Anambra", "peppers", 9),
    ("Fatima Abubakar", "+2348030000012", Language.hausa,
     "Fatima Groundnut Plots", "Bauchi", "groundnuts", 14),
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

