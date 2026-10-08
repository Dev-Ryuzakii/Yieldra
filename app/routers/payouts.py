"""Naira payout endpoints — farmer bank accounts and the disbursements owed to them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.payout import FarmerDisbursement
from app.routers.deps import require_operator
from app.services import disbursements, payouts

router = APIRouter(tags=["payouts"])


@router.get("/farmers/{user_id}/payout-account")
async def get_payout_account(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    account = await payouts.get_account(session, user_id)
    if account is None:
        raise HTTPException(status_code=404, detail="no payout account")
    return payouts.describe(account)


async def _disbursement(session: AsyncSession, disbursement_id: int) -> FarmerDisbursement:
    disbursement = await session.get(FarmerDisbursement, disbursement_id)
    if disbursement is None:
        raise HTTPException(status_code=404, detail="disbursement not found")
    return disbursement


@router.get("/disbursements")
async def list_disbursements(
    session: AsyncSession = Depends(get_session), _: None = Depends(require_operator)
) -> list[dict]:
    rows = await session.execute(select(FarmerDisbursement).order_by(FarmerDisbursement.id.desc()))
    return [disbursements.describe(d, operator=True) for d in rows.scalars()]
