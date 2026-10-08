"""Read historical farmer payout accounts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payout import PayoutAccount


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
