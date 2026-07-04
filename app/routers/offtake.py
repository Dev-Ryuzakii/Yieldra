"""Offtake / buyer-matching endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.offtake_agent import OfftakeAgent
from app.database import get_session

router = APIRouter(prefix="/offtake", tags=["offtake"])
_agent = OfftakeAgent()


class DealRequest(BaseModel):
    buyer_id: int
    price_per_kg_ngn: int | None = None


@router.get("/harvests/{harvest_id}/buyers")
async def list_buyers(
    harvest_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await _agent.find_buyers(session, harvest_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/harvests/{harvest_id}/deal")
async def initiate_deal(
    harvest_id: int, payload: DealRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await _agent.initiate_deal(
        session, harvest_id, payload.buyer_id, payload.price_per_kg_ngn
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result
