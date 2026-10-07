"""Naira payout endpoints — farmer bank accounts and the disbursements owed to them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.payout import FarmerDisbursement
from app.models.user import User
from app.routers.deps import require_operator
from app.services import disbursements, payouts
from app.tools import tuago
from app.tools.tuago import TuagoError

router = APIRouter(tags=["payouts"])


class PayoutAccountCreate(BaseModel):
    bank_code: str = Field(min_length=2, max_length=12)
    account_number: str = Field(min_length=10, max_length=10, pattern=r"^\d{10}$")


@router.get("/banks")
async def list_banks() -> list[dict]:
    """Banks a farmer can be paid at, with the codes used to register an account."""
    try:
        return await tuago.list_banks()
    except TuagoError as exc:
        raise HTTPException(status_code=502, detail=f"Tuago: {exc.code}") from exc


@router.post("/farmers/{user_id}/payout-account", status_code=201)
async def set_payout_account(
    user_id: int,
    payload: PayoutAccountCreate,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Register a farmer's bank account (farmers normally do this by chat: BANK <code> <number>)."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    result = await payouts.register_account(session, user, payload.bank_code, payload.account_number)
    if result["status"] != "saved":
        raise HTTPException(status_code=422, detail=result.get("message", "account not accepted"))
    result["disbursements_opened"] = await disbursements.open_pending_for_farmer(session, user.id)
    return result


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


@router.post("/disbursements/{disbursement_id}/funding")
async def open_funding(
    disbursement_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Issue (or re-issue) the Tuago account to transfer this payout into."""
    disbursement = await _disbursement(session, disbursement_id)
    outcome = await disbursements.open_funding(session, disbursement)
    if outcome.get("status") == "error":
        raise HTTPException(status_code=502, detail=f"Tuago: {outcome.get('code')}")
    return disbursements.describe(disbursement, operator=True)


@router.post("/disbursements/{disbursement_id}/check")
async def check_disbursement(
    disbursement_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Ask Tuago whether the funding transfer has landed."""
    disbursement = await _disbursement(session, disbursement_id)
    await disbursements.confirm(session, disbursement)
    return disbursements.describe(disbursement, operator=True)


@router.post("/disbursements/{disbursement_id}/simulate")
async def simulate_disbursement(
    disbursement_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Sandbox only: mark the funding transfer as received, then confirm it."""
    disbursement = await _disbursement(session, disbursement_id)
    if not disbursement.tuago_session_id:
        raise HTTPException(status_code=409, detail="no funding account issued yet")
    try:
        await tuago.simulate_payment(disbursement.tuago_session_id, "success")
    except TuagoError as exc:
        raise HTTPException(status_code=409, detail=f"Tuago: {exc.code}") from exc
    await disbursements.confirm(session, disbursement)
    return disbursements.describe(disbursement, operator=True)
