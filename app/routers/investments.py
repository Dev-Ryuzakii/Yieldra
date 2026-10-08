"""Investor endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.investment_agent import InvestmentAgent
from app.database import get_session
from app.schemas.investment import InvestmentCreate

router = APIRouter(prefix="/investments", tags=["investments"])
_agent = InvestmentAgent()


@router.post("")
async def create_investment(
    payload: InvestmentCreate,
) -> dict:
    """Naira investment payments are unavailable while the payment rail is retired."""
    raise HTTPException(status_code=410, detail="Investment payments are unavailable. Sponsorship payments use PayPal.")


@router.get("/returns/estimate")
async def estimate_returns(
    farm_id: int,
    amount_ngn: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await _agent.calculate_expected_returns(session, farm_id, amount_ngn)
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    return result
