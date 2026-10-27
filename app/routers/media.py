"""Public editorial image library. Images are not farm evidence."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.media import CropImage
from app.services.crop_covers import cover_for_crop, known_crops

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/covers")
async def covers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    images = {image.crop: image for image in
              (await session.execute(select(CropImage).order_by(CropImage.crop))).scalars()}
    return [
        {"crop": crop, **cover_for_crop(crop, images.get(crop))}
        for crop in sorted(set(images) | set(known_crops()))
    ]
