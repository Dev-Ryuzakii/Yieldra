"""Farmer disbursements — getting PayPal-funded tranches to the farmer in naira.

Tuago cannot send money, so a disbursement is a Tuago checkout routed through the
farmer's subaccount. Yieldra transfers the naira into it; Tuago verifies the
transfer with the bank and settles it to the farmer's own account. Every payout
therefore has a bank-verified record instead of an untracked manual transfer.

Steps:  create_for_milestone -> open_funding -> (Yieldra transfers) -> confirm
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.farm import Farm
from app.models.payout import DisbursementStatus, FarmerDisbursement
from app.models.sponsorship import Sponsorship, SponsorshipMilestone
from app.models.user import User
from app.services import payouts
from app.services.notify import notify
from app.tools import tuago
from app.tools.tuago import TuagoError
from app.utils.logger import get_logger
from app.utils.money import format_naira

log = get_logger("yieldra.disbursements")


def convert_to_kobo(source_minor: int, rate: float) -> int:
    """US cents -> kobo at ``rate`` naira per dollar (cents x rate == kobo)."""
    return int(round(source_minor * rate))


async def for_milestone(session: AsyncSession, milestone_id: int) -> FarmerDisbursement | None:
    result = await session.execute(
        select(FarmerDisbursement).where(FarmerDisbursement.milestone_id == milestone_id)
    )
    return result.scalars().first()


async def create_for_milestone(
    session: AsyncSession,
    sponsorship: Sponsorship,
    milestone: SponsorshipMilestone,
    farm: Farm,
) -> FarmerDisbursement:
    """Record what the farmer is owed for a paid PayPal tranche. Safe to call twice."""
    existing = await for_milestone(session, milestone.id)
    if existing is not None:
        return existing
    rate = float(settings.usd_ngn_rate)
    disbursement = FarmerDisbursement(
        milestone_id=milestone.id,
        farmer_id=farm.farmer_id,
        source_currency=sponsorship.currency,
        source_minor=milestone.amount_minor,
        rate=rate,
        amount_kobo=convert_to_kobo(milestone.amount_minor, rate),
        status=DisbursementStatus.needs_bank_details.value,
    )
    session.add(disbursement)
    await session.flush()
    await open_funding(session, disbursement)
    return disbursement


async def open_funding(session: AsyncSession, disbursement: FarmerDisbursement) -> dict[str, Any]:
    """Issue (or re-issue) the Tuago account Yieldra transfers this payout into."""
    if disbursement.status == DisbursementStatus.paid.value:
        return {"status": "paid"}
    account = await payouts.get_account(session, disbursement.farmer_id)
    if account is None:
        disbursement.status = DisbursementStatus.needs_bank_details.value
        await session.flush()
        return {"status": disbursement.status}

    farmer = await session.get(User, disbursement.farmer_id)
    try:
        checkout = await tuago.create_checkout(
            amount_minor=disbursement.amount_kobo,
            customer_email=settings.operator_email,
            customer_name="Yieldra",
            description=f"Yieldra payout to {farmer.name if farmer else 'farmer'}",
            redirect_url=f"{settings.public_base_url.rstrip('/')}/console",
            subaccount=account.tuago_subaccount_id,
        )
        details = await tuago.bank_transfer_details(checkout["session_id"])
    except TuagoError as exc:
        log.warning("could not open funding for disbursement %s: %s", disbursement.id, exc.code)
        return {"status": "error", "code": exc.code, "message": str(exc)}

    disbursement.tuago_session_id = checkout["session_id"]
    disbursement.tuago_reference = checkout.get("reference") or details.get("reference")
    disbursement.pay_bank_name = details.get("bank_name")
    disbursement.pay_account_number = details.get("account_number")
    disbursement.pay_account_name = details.get("account_name")
    disbursement.pay_expires_at = details.get("expires_at") or checkout.get("expires_at")
    disbursement.status = DisbursementStatus.awaiting_funding.value
    await session.flush()
    return {"status": disbursement.status}


async def open_pending_for_farmer(session: AsyncSession, farmer_id: int) -> int:
    """After a farmer adds bank details, issue funding accounts for what they are owed."""
    result = await session.execute(
        select(FarmerDisbursement).where(
            FarmerDisbursement.farmer_id == farmer_id,
            FarmerDisbursement.status == DisbursementStatus.needs_bank_details.value,
        )
    )
    opened = 0
    for disbursement in result.scalars():
        outcome = await open_funding(session, disbursement)
        opened += outcome.get("status") == DisbursementStatus.awaiting_funding.value
    return opened


async def confirm(session: AsyncSession, disbursement: FarmerDisbursement) -> dict[str, Any]:
    """Ask Tuago whether the funding transfer has landed, and record the answer."""
    if disbursement.status == DisbursementStatus.paid.value:
        return {"status": "paid", "disbursement_id": disbursement.id}
    if not disbursement.tuago_session_id:
        return {"status": disbursement.status, "disbursement_id": disbursement.id}
    try:
        checkout = await tuago.get_checkout(disbursement.tuago_session_id)
    except TuagoError as exc:
        return {"status": "error", "code": exc.code, "disbursement_id": disbursement.id}

    if tuago.is_paid(checkout.get("status")):
        if checkout.get("amount_minor") != disbursement.amount_kobo:
            log.error(
                "disbursement %s amount mismatch expected=%s got=%s",
                disbursement.id, disbursement.amount_kobo, checkout.get("amount_minor"),
            )
            return {"status": "error", "code": "amount_mismatch", "disbursement_id": disbursement.id}
        disbursement.status = DisbursementStatus.paid.value
        disbursement.paid_at = datetime.now(timezone.utc)
        await session.commit()
        farmer = await session.get(User, disbursement.farmer_id)
        account = await payouts.get_account(session, disbursement.farmer_id)
        where = f"{account.bank_name or 'bank'} account {account.masked}" if account else "bank account"
        await notify(
            farmer,
            f"Yieldra: {format_naira(disbursement.amount_kobo)} is on its way to your {where}.",
        )
        log.info("disbursement paid id=%s kobo=%s", disbursement.id, disbursement.amount_kobo)
    elif tuago.is_failed(checkout.get("status")):
        disbursement.status = DisbursementStatus.failed.value
        await session.commit()
    return {"status": disbursement.status, "disbursement_id": disbursement.id}


def describe(disbursement: FarmerDisbursement, operator: bool = False) -> dict[str, Any]:
    """API shape. Funding account details are for the operator only."""
    out: dict[str, Any] = {
        "id": disbursement.id,
        "milestone_id": disbursement.milestone_id,
        "status": disbursement.status,
        "amount_kobo": disbursement.amount_kobo,
        "amount": format_naira(disbursement.amount_kobo),
        "rate": float(disbursement.rate),
        "paid_at": disbursement.paid_at,
    }
    if operator:
        out.update(
            farmer_id=disbursement.farmer_id,
            source_minor=disbursement.source_minor,
            source_currency=disbursement.source_currency,
            tuago_reference=disbursement.tuago_reference,
            pay_bank_name=disbursement.pay_bank_name,
            pay_account_number=disbursement.pay_account_number,
            pay_account_name=disbursement.pay_account_name,
            pay_expires_at=disbursement.pay_expires_at,
            created_at=disbursement.created_at,
        )
    return out
