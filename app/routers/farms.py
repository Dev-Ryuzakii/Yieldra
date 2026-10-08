"""Farm registration + management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.farm import Farm
from app.models.harvest import Harvest
from app.schemas.farm import FarmCreate, FarmRead
from app.schemas.harvest import HarvestCreate, HarvestRead

router = APIRouter(prefix="/farms", tags=["farms"])


@router.post("", response_model=FarmRead, status_code=201)
async def create_farm(
    payload: FarmCreate, session: AsyncSession = Depends(get_session)
) -> Farm:
    farm = Farm(
        name=payload.name,
        farmer_id=payload.farmer_id,
        location=payload.location,
        crop_type=payload.crop_type,
        cover_image_url=payload.cover_image_url,
        cover_image_alt=payload.cover_image_alt,
        cover_image_credit=payload.cover_image_credit,
        cover_image_source=payload.cover_image_source,
        cover_image_license=payload.cover_image_license,
        total_plots=payload.total_plots,
        available_plots=payload.total_plots,
    )
    session.add(farm)
    await session.flush()
    await session.refresh(farm)
    return farm


@router.get("", response_model=list[FarmRead])
async def list_farms(session: AsyncSession = Depends(get_session)) -> list[Farm]:
    result = await session.execute(select(Farm).order_by(Farm.id))
    return list(result.scalars())


@router.get("/{farm_id}", response_model=FarmRead)
async def get_farm(farm_id: int, session: AsyncSession = Depends(get_session)) -> Farm:
    farm = await session.get(Farm, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="farm not found")
    return farm


@router.post("/{farm_id}/harvests", response_model=HarvestRead, status_code=201)
async def create_harvest(
    farm_id: int, payload: HarvestCreate, session: AsyncSession = Depends(get_session)
) -> Harvest:
    farm = await session.get(Farm, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="farm not found")
    harvest = Harvest(farm_id=farm_id, expected_date=payload.expected_date)
    session.add(harvest)
    await session.flush()
    await session.refresh(harvest)
    return harvest
