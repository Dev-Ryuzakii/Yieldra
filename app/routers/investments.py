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
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Process an investment: verify payment -> create records -> Telegram confirm."""
    result = await _agent.process_investment(
        session,
        investor_id=payload.investor_id,
        farm_id=payload.farm_id,
        amount_ngn=payload.amount_ngn,
        payment_reference=payload.payment_reference,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result["message"])
    if result.get("status") == "payment_failed":
        raise HTTPException(status_code=402, detail="payment verification failed")
    return result


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
