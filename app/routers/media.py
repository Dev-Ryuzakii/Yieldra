"""Public editorial image library. Images are not farm evidence."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.media import CropImage

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/covers")
async def covers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    images = (await session.execute(select(CropImage).order_by(CropImage.crop))).scalars()
    return [
        {"crop": image.crop, "url": image.image_url, "alt": image.alt,
         "credit": image.credit, "source": image.source_url,
         "license": image.license, "changes": image.changes,
         "illustrative": image.illustrative}
        for image in images
    ]
