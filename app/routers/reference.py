"""CRUD for reference data: cold storage facilities + crop parameters."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.reference import ColdStorageFacility, CropParameter
from app.schemas.reference import (
    CropParameterCreate,
    CropParameterRead,
    FacilityCreate,
    FacilityRead,
)
from app.utils.money import naira_to_kobo

router = APIRouter(prefix="/reference", tags=["reference"])


# -- cold storage facilities ----------------------------------------------
@router.post("/facilities", response_model=FacilityRead, status_code=201)
async def create_facility(
    payload: FacilityCreate, session: AsyncSession = Depends(get_session)
) -> ColdStorageFacility:
    exists = await session.execute(
        select(ColdStorageFacility).where(
            ColdStorageFacility.facility_code == payload.facility_code
        )
    )
    if exists.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="facility_code already exists")
    facility = ColdStorageFacility(**payload.model_dump())
    session.add(facility)
    await session.flush()
    await session.refresh(facility)
    return facility


@router.get("/facilities", response_model=list[FacilityRead])
async def list_facilities(session: AsyncSession = Depends(get_session)) -> list[ColdStorageFacility]:
    result = await session.execute(select(ColdStorageFacility).order_by(ColdStorageFacility.id))
    return list(result.scalars())


# -- crop parameters -------------------------------------------------------
@router.post("/crops", response_model=CropParameterRead, status_code=201)
async def create_crop(
    payload: CropParameterCreate, session: AsyncSession = Depends(get_session)
) -> CropParameter:
    exists = await session.execute(
        select(CropParameter).where(CropParameter.crop_type.ilike(payload.crop_type))
    )
    if exists.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="crop_type already exists")
    crop = CropParameter(
        crop_type=payload.crop_type,
        price_per_kg=naira_to_kobo(payload.price_per_kg_ngn),
        return_multiplier=payload.return_multiplier,
        harvest_months=payload.harvest_months,
        spacing_cm=payload.spacing_cm,
        plant_window=payload.plant_window,
    )
    session.add(crop)
    await session.flush()
    await session.refresh(crop)
    return crop


@router.get("/crops", response_model=list[CropParameterRead])
async def list_crops(session: AsyncSession = Depends(get_session)) -> list[CropParameter]:
    result = await session.execute(select(CropParameter).order_by(CropParameter.crop_type))
    return list(result.scalars())
