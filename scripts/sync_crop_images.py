"""Upsert the licensed cover manifest into the configured Afribase database."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.database import async_session_factory
from app.models.media import CropImage

MANIFEST = Path(__file__).resolve().parent.parent / "data" / "farm_covers.json"


async def main() -> None:
    covers = json.loads(MANIFEST.read_text())
    async with async_session_factory() as session:
        for item in covers:
            values = {"crop": item["crop"], "image_url": item["url"],
                      "alt": item["alt"], "credit": item["credit"],
                      "source_url": item["source"], "license": item["license"],
                      "changes": item["changes"], "illustrative": item["illustrative"]}
            statement = insert(CropImage).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[CropImage.crop],
                set_={key: value for key, value in values.items() if key != "crop"},
            )
            await session.execute(statement)
        await session.commit()
    print(f"Synced {len(covers)} licensed cover records")


if __name__ == "__main__":
    asyncio.run(main())
