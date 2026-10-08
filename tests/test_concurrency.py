"""Two notices for one payment arriving together. Needs row locks, so PostgreSQL only."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.agents.sponsorship_agent import SponsorshipAgent
from app.config import settings
from app.database import async_session_factory
from app.models.payout import FarmerDisbursement

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
