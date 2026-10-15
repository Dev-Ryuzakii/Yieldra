"""Add the demo farms and their farmers to any database, including Afribase.

Unlike ``seed_db.py`` this never deletes anything: it inserts each farmer and
farm only if a row with the same phone or farm name is not already there, so it
is safe to run against the hosted database more than once. The app also does
this by itself at startup when the farms table is empty (DEMO_FARMS=true).

    DATABASE_URL=postgresql+asyncpg://... python scripts/add_demo_farms.py
"""

from __future__ import annotations

import asyncio

from app.database import async_session_factory, engine
from app.services.demo_farms import add_missing


async def main() -> None:
    async with async_session_factory() as session:
        added = await add_missing(session)
        await session.commit()
    await engine.dispose()
    print(f"Added {added} farm(s); existing farms were left unchanged.")


if __name__ == "__main__":
    asyncio.run(main())
