"""The active payment flow is PayPal only; old bank rail records are read-only."""

from sqlalchemy import select

from app.agents.sponsorship_agent import SponsorshipAgent
from app.config import settings
from app.models.payout import FarmerDisbursement
from app.models.sponsorship import Sponsorship
from tests.conftest import photo


async def test_agent_rejects_retired_naira_rail(session, world):
    agent = SponsorshipAgent()
    result = await agent.start(session, world.sponsor.id, world.farm.id, 100_000, rail="tuago")
    assert result["status"] == "invalid_rail"
    assert (await session.execute(select(Sponsorship))).scalars().first() is None


async def test_paypal_capture_records_pending_liability_without_bank_checkout(session, world):
    agent = SponsorshipAgent()
    started = await agent.start(session, world.sponsor.id, world.farm.id, 10_000)
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]
    await agent.confirm_payment(session, order_id)
    payout = (await session.execute(select(FarmerDisbursement))).scalars().one()
    assert payout.status == "pending_manual"
    assert payout.tuago_session_id is None
    assert payout.amount_kobo == int(round(2_000 * settings.usd_ngn_rate))


async def test_live_mode_blocks_a_later_charge(session, world, verifier, live_setting):
    agent = SponsorshipAgent()
    started = await agent.start(session, world.sponsor.id, world.farm.id, 10_000)
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]
    await agent.confirm_payment(session, order_id)

    live_setting(paypal_env="live")
    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))
    current = await agent.get(session, started["sponsorship"]["id"])

    assert result["status"] == "payment_failed"
    assert result["releases"][0]["issue"] == "FARMER_PAYOUT_UNAVAILABLE"
    assert current["milestones"][1]["status"] == "payment_failed"
    assert current["paid_minor"] == 2_000
