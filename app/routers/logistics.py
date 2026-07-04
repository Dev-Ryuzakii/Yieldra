"""Supply chain / logistics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.logistics_agent import LogisticsAgent
from app.database import get_session

router = APIRouter(prefix="/logistics", tags=["logistics"])
_agent = LogisticsAgent()


class BookRequest(BaseModel):
    facility_id: str
    date: str | None = None


@router.post("/harvests/{harvest_id}/coordinate")
async def coordinate(
    harvest_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """Kick off harvest coordination: ask farmer for readiness, propose storage slots."""
    result = await _agent.initiate_harvest_coordination(session, harvest_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/harvests/{harvest_id}/book")
async def book(
    harvest_id: int, payload: BookRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    """Farmer confirmed READY -> book cold storage + truck, notify parties."""
    result = await _agent.confirm_and_book(
        session, harvest_id, payload.facility_id, payload.date
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result
