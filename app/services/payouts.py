"""Farmer payout accounts: a verified bank account registered with Tuago."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payout import PayoutAccount
from app.models.user import User
from app.tools import tuago
from app.tools.tuago import TuagoError
from app.utils.logger import get_logger

log = get_logger("yieldra.payouts")


async def get_account(session: AsyncSession, user_id: int) -> PayoutAccount | None:
    result = await session.execute(select(PayoutAccount).where(PayoutAccount.user_id == user_id))
    return result.scalars().first()


def describe(account: PayoutAccount) -> dict[str, Any]:
    """API shape. The full account number is never returned."""
    return {
        "user_id": account.user_id,
        "bank_code": account.bank_code,
        "bank_name": account.bank_name,
        "account_number_masked": account.masked,
        "account_name": account.account_name,
    }


async def register_account(
    session: AsyncSession, user: User, bank_code: str, account_number: str
) -> dict[str, Any]:
    """Verify a bank account with Tuago and save it as the user's payout account.

    Registering again replaces the previous account.
    """
    bank_code = (bank_code or "").strip()
    account_number = (account_number or "").strip().replace(" ", "")
    if not (account_number.isdigit() and len(account_number) == 10):
        return {"status": "invalid", "message": "account number must be 10 digits"}
    if not bank_code or len(bank_code) > 12 or not bank_code.isalnum():
        return {"status": "invalid", "message": "bank code is not valid"}

    try:
        banks = await tuago.list_banks()
        subaccount = await tuago.create_subaccount(user.name, bank_code, account_number)
    except TuagoError as exc:
        log.warning("payout account rejected user=%s code=%s", user.id, exc.code)
        return {"status": "rejected", "code": exc.code, "message": str(exc)}

    bank_name = next((b["name"] for b in banks if b["code"] == bank_code), None)
    account = await get_account(session, user.id)
    if account is None:
        account = PayoutAccount(user_id=user.id)
        session.add(account)
    account.bank_code = bank_code
    account.bank_name = bank_name
    account.account_number = account_number
    account.account_name = subaccount.get("account_name") or user.name
    account.tuago_subaccount_id = subaccount["subaccount_id"]
    await session.flush()
    log.info("payout account saved user=%s bank=%s", user.id, bank_code)
    return {"status": "saved", **describe(account)}
