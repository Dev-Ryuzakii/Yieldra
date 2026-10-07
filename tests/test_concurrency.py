"""Two notices for one payment arriving together. Needs row locks, so PostgreSQL only."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.agents.sponsorship_agent import SponsorshipAgent
from app.config import settings
from app.database import async_session_factory
from app.models.payout import FarmerDisbursement
from app.services import naira_payments
from app.tools import tuago

pytestmark = pytest.mark.skipif(
    not settings.database_url.startswith("postgresql"),
    reason="row locking is only meaningful on PostgreSQL (set TEST_DATABASE_URL)",
)


async def test_paypal_return_and_webhook_together_capture_and_notify_once(session, world, sent):
    agent = SponsorshipAgent()
    started = await agent.start(session, world.sponsor.id, world.farm.id, 10_000)
    await session.commit()
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]

    async def confirm():
        async with async_session_factory() as own:
            return (await agent.confirm_payment(own, order_id))["status"]

    outcomes = sorted(await asyncio.gather(confirm(), confirm()))

    assert outcomes == ["active", "already_confirmed"]
    assert len([t for chat, t in sent if chat == world.sponsor.phone]) == 1
    async with async_session_factory() as check:
        assert len((await check.execute(select(FarmerDisbursement))).scalars().all()) == 1


async def test_tuago_webhook_and_page_refresh_together_settle_once(session, world, banked, sent):
    agent = SponsorshipAgent()
    started = await agent.start(session, world.sponsor.id, world.farm.id, 10_000_000, rail="tuago")
    await session.commit()
    session_id = started["approve_url"].split("session=")[1]
    await tuago.simulate_payment(session_id, "success")

    async def settle():
        async with async_session_factory() as own:
            return (await naira_payments.settle(own, agent, session_id=session_id))["status"]

    outcomes = sorted(await asyncio.gather(settle(), settle()))

    assert outcomes == ["already_paid", "paid"]
    assert len([t for chat, t in sent if chat == world.farmer.phone]) == 1
