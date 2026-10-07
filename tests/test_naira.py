"""The naira leg: farmer payout accounts, Tuago-paid sponsorships, and farmer disbursements."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.sponsorship_agent import SponsorshipAgent
from app.models.payout import FarmerDisbursement, PayoutAccount
from app.models.sponsorship import SponsorshipMilestone
from app.services import disbursements, naira_payments, payouts
from app.tools import tuago
from app.tools.tuago import TuagoError

from tests.conftest import make_verdict, photo

NGN_TOTAL = 10_000_000  # ₦100,000 -> tranches of ₦20k / ₦30k / ₦30k / ₦20k
USD_TOTAL = 10_000      # $100


@pytest.fixture
def agent() -> SponsorshipAgent:
    return SponsorshipAgent()


def statuses(sponsorship: dict) -> list[str]:
    return [m["status"] for m in sponsorship["milestones"]]


async def pay_open_checkout(agent, session, sponsorship_id: int) -> dict:
    """Pay whichever naira tranche is open, as the sponsor would in WhatsApp."""
    row = await session.execute(
        select(SponsorshipMilestone).where(
            SponsorshipMilestone.sponsorship_id == sponsorship_id,
            SponsorshipMilestone.tuago_session_id.is_not(None),
            SponsorshipMilestone.payment_url.is_not(None),
        )
    )
    milestone = row.scalars().first()
    await tuago.simulate_payment(milestone.tuago_session_id, "success")
    return await naira_payments.settle(session, agent, session_id=milestone.tuago_session_id)


# -- payout accounts -------------------------------------------------------
async def test_farmer_registers_a_verified_bank_account(session, world):
    result = await payouts.register_account(session, world.farmer, "058", "0123456789")

    assert result["status"] == "saved"
    assert result["bank_name"] == "Guaranty Trust Bank"
    assert result["account_number_masked"] == "****6789"
    assert "0123456789" not in str(result)  # the full number is never echoed back
    account = await payouts.get_account(session, world.farmer.id)
    assert account.tuago_subaccount_id.startswith("MOCK-SUB-")


@pytest.mark.parametrize(
    ("bank_code", "number"),
    [("058", "12345"), ("058", "01234567ab"), ("", "0123456789"), ("05 8", "0123456789")],
)
async def test_bad_bank_details_are_refused_before_calling_tuago(session, world, bank_code, number):
    result = await payouts.register_account(session, world.farmer, bank_code, number)
    assert result["status"] == "invalid"
    assert await payouts.get_account(session, world.farmer.id) is None


async def test_registering_again_replaces_the_account(session, world, banked):
    await payouts.register_account(session, world.farmer, "044", "9876543210")
    await session.commit()

    rows = (await session.execute(select(PayoutAccount))).scalars().all()
    assert len(rows) == 1 and rows[0].bank_code == "044" and rows[0].masked == "****3210"


async def test_account_tuago_rejects_is_not_saved(session, world, monkeypatch):
    async def reject(*args):
        raise TuagoError("Could not resolve account", 422, "validation_error")

    monkeypatch.setattr(tuago, "create_subaccount", reject)
    result = await payouts.register_account(session, world.farmer, "058", "0123456789")

    assert result["status"] == "rejected" and result["code"] == "validation_error"
    assert await payouts.get_account(session, world.farmer.id) is None


# -- naira sponsorships (Tuago rail) ---------------------------------------
async def test_naira_sponsorship_needs_the_farmer_to_have_a_bank_account(agent, session, world):
    result = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    assert result["status"] == "farmer_not_ready"


async def test_naira_sponsorship_runs_stage_by_stage(agent, session, world, banked, sent, verifier):
    started = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    await session.commit()
    s = started["sponsorship"]
    assert s["rail"] == "tuago" and s["currency"] == "NGN" and s["total"] == "₦100,000.00"
    assert [m["amount"] for m in s["milestones"]] == [
        "₦20,000.00", "₦30,000.00", "₦30,000.00", "₦20,000.00",
    ]
    assert statuses(s) == ["awaiting_payment", "locked", "locked", "locked"]
    assert started["approve_url"] == s["milestones"][0]["payment_url"]

    # The checkout is routed to the farmer's own bank account.
    session_id = started["approve_url"].split("session=")[1]
    account = await payouts.get_account(session, world.farmer.id)
    assert tuago._mock_sessions[session_id]["subaccount"] == account.tuago_subaccount_id
    assert tuago._mock_sessions[session_id]["redirectUrl"] == s["track_url"]

    # Sponsor pays tranche 1 -> active, stage 2 opens, farmer is told where money goes.
    paid = await pay_open_checkout(agent, session, s["id"])
    assert paid["kind"] == "tranche" and paid["status"] == "paid"
    s = await agent.get(session, s["id"])
    assert s["status"] == "active"
    assert statuses(s) == ["paid", "awaiting_evidence", "locked", "locked"]
    assert s["milestones"][0]["payment_url"] is None
    to_farmer = [t for chat, t in sent if chat == world.farmer.phone]
    assert "₦20,000.00" in to_farmer[-1] and "****6789" in to_farmer[-1] and "PROOF" in to_farmer[-1]

    # Farmer proves stage 2 -> nothing is charged; the sponsor is asked to pay.
    sent.clear()
    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))
    assert result["status"] == "payment_requested"
    assert result["releases"][0]["status"] == "awaiting_payment"
    s = await agent.get(session, s["id"])
    assert statuses(s) == ["paid", "awaiting_payment", "locked", "locked"]
    link = s["milestones"][1]["payment_url"]
    assert link and s["next_milestone"]["payment_url"] == link
    to_sponsor = [t for chat, t in sent if chat == world.sponsor.phone]
    assert "₦30,000.00" in to_sponsor[0] and to_sponsor[0].endswith(f"Pay here: {link}")
    assert "asked to pay ₦30,000.00" in [t for chat, t in sent if chat == world.farmer.phone][0]

    # ... and stage 3 stays shut until they do.
    blocked = await agent.submit_evidence(session, world.farm.id, photo("established"))
    assert blocked["status"] == "nothing_pending"

    await pay_open_checkout(agent, session, s["id"])
    s = await agent.get(session, s["id"])
    assert statuses(s) == ["paid", "paid", "awaiting_evidence", "locked"]

    for stage in ("established", "harvest"):
        assert (await agent.submit_evidence(session, world.farm.id, photo(stage)))["status"] == (
            "payment_requested"
        )
        await pay_open_checkout(agent, session, s["id"])
    done = await agent.get(session, s["id"])
    assert done["status"] == "completed" and done["paid"] == "₦100,000.00"
    # Naira tranches settle to the farmer through Tuago: no separate disbursement.
    assert (await session.execute(select(FarmerDisbursement))).first() is None


async def test_unpaid_checkout_does_not_activate(agent, session, world, banked):
    started = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    await session.commit()
    session_id = started["approve_url"].split("session=")[1]

    result = await naira_payments.settle(session, agent, session_id=session_id)

    assert result["status"] == "pending"
    assert (await agent.get(session, started["sponsorship"]["id"]))["status"] == "pending_approval"


async def test_payment_of_the_wrong_amount_is_not_accepted(agent, session, world, banked):
    started = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    await session.commit()
    session_id = started["approve_url"].split("session=")[1]
    await tuago.simulate_payment(session_id, "success")
    tuago._mock_sessions[session_id]["amountMinor"] = "100"

    result = await naira_payments.settle(session, agent, session_id=session_id)

    assert result == {"kind": "tranche", "milestone_id": result["milestone_id"],
                      "status": "error", "code": "amount_mismatch"}
    assert (await agent.get(session, started["sponsorship"]["id"]))["paid_minor"] == 0


async def test_same_payment_notice_twice_is_harmless(agent, session, world, banked, sent):
    started = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    await session.commit()
    session_id = started["approve_url"].split("session=")[1]
    await tuago.simulate_payment(session_id, "success")

    first = await naira_payments.settle(session, agent, session_id=session_id)
    count = len(sent)
    second = await naira_payments.settle(session, agent, session_id=session_id)

    assert first["status"] == "paid" and second["status"] == "already_paid"
    assert len(sent) == count
    assert (await agent.get(session, started["sponsorship"]["id"]))["paid_minor"] == 2_000_000


async def test_unknown_reference_is_ignored(agent, session, world):
    assert (await naira_payments.settle(session, agent, reference="nope"))["status"] == "ignored"
    assert (await naira_payments.settle(session, agent))["status"] == "ignored"


async def test_failed_checkout_is_replaced_on_refresh(agent, session, world, banked):
    started = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    await session.commit()
    old_link = started["approve_url"]
    await tuago.simulate_payment(old_link.split("session=")[1], "failed")

    sponsorship = await agent.by_reference(session, started["sponsorship"]["reference"])
    refreshed = await agent.refresh(session, sponsorship)

    new_link = refreshed["milestones"][0]["payment_url"]
    assert new_link and new_link != old_link
    assert statuses(refreshed)[0] == "awaiting_payment"


async def test_tuago_outage_when_requesting_payment_can_be_retried(
    agent, session, world, banked, verifier, monkeypatch
):
    started = await agent.start(session, world.sponsor.id, world.farm.id, NGN_TOTAL, rail="tuago")
    await session.commit()
    await pay_open_checkout(agent, session, started["sponsorship"]["id"])
    real_create = tuago.create_checkout
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TuagoError("down", 503, "core_unavailable")
        return await real_create(**kwargs)

    monkeypatch.setattr(tuago, "create_checkout", flaky)
    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))
    assert result["status"] == "payment_failed"
    assert result["releases"][0]["issue"] == "core_unavailable"
    milestone_id = result["releases"][0]["milestone_id"]

    retry = await agent.retry_payment(session, milestone_id)

    assert retry["status"] == "awaiting_payment"
    assert statuses(await agent.get(session, started["sponsorship"]["id"]))[1] == "awaiting_payment"


@pytest.mark.parametrize("total", [499_999, 500_000_001])
async def test_naira_amount_limits(agent, session, world, banked, total):
    result = await agent.start(session, world.sponsor.id, world.farm.id, total, rail="tuago")
    assert result["status"] == "invalid_amount" and "₦5,000.00" in result["message"]


async def test_unknown_rail_is_refused(agent, session, world):
    result = await agent.start(session, world.sponsor.id, world.farm.id, USD_TOTAL, rail="cash")
    assert result["status"] == "invalid_rail"


# -- farmer disbursements (PayPal rail) ------------------------------------
async def start_paypal(agent, session, world) -> dict:
    started = await agent.start(session, world.sponsor.id, world.farm.id, USD_TOTAL)
    await session.commit()
    confirmed = await agent.confirm_payment(
        session, started["sponsorship"]["milestones"][0]["paypal_order_id"]
    )
    return confirmed["sponsorship"]


async def test_paypal_tranche_waits_for_bank_details_then_opens_funding(
    agent, session, world, sent
):
    s = await start_paypal(agent, session, world)

    payout = (await session.execute(select(FarmerDisbursement))).scalars().one()
    assert payout.status == "needs_bank_details"
    assert payout.source_minor == 2000 and float(payout.rate) == 1500
    assert payout.amount_kobo == 3_000_000  # $20 x 1,500 = ₦30,000
    assert payout.pay_account_number is None

    await payouts.register_account(session, world.farmer, "058", "0123456789")
    assert await disbursements.open_pending_for_farmer(session, world.farmer.id) == 1
    await session.commit()

    await session.refresh(payout)
    assert payout.status == "awaiting_funding"
    assert payout.pay_account_number and payout.pay_account_name == "Tuago Gateway"
    account = await payouts.get_account(session, world.farmer.id)
    assert tuago._mock_sessions[payout.tuago_session_id]["subaccount"] == account.tuago_subaccount_id
    # The sponsor sees that the farmer is owed naira, never the funding account.
    public = (await agent.get(session, s["id"]))["milestones"][0]["farmer_payout"]
    assert public["amount"] == "₦30,000.00" and public["status"] == "awaiting_funding"
    assert "pay_account_number" not in public


async def test_funding_a_disbursement_pays_the_farmer(agent, session, world, banked, sent):
    await start_paypal(agent, session, world)
    payout = (await session.execute(select(FarmerDisbursement))).scalars().one()
    assert payout.status == "awaiting_funding"  # bank details were already on file
    assert "₦30,000.00 is being sent to your bank account" in [
        t for chat, t in sent if chat == world.farmer.phone
    ][0]

    # Not paid until Tuago says the transfer landed.
    assert (await disbursements.confirm(session, payout))["status"] == "awaiting_funding"

    await tuago.simulate_payment(payout.tuago_session_id, "success")
    sent.clear()
    result = await naira_payments.settle(session, agent, reference=payout.tuago_reference)

    assert result["kind"] == "disbursement" and result["status"] == "paid"
    await session.refresh(payout)
    assert payout.status == "paid" and payout.paid_at is not None
    assert sent == [(world.farmer.phone,
                     "Yieldra: ₦30,000.00 is on its way to your Guaranty Trust Bank account ****6789.")]
    # Confirming again changes nothing and sends nothing.
    assert (await disbursements.confirm(session, payout))["status"] == "paid"
    assert len(sent) == 1


async def test_every_paypal_tranche_gets_one_disbursement(agent, session, world, banked, verifier):
    s = await start_paypal(agent, session, world)
    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["releases"][0]["farmer_kobo"] == 4_500_000  # $30 -> ₦45,000
    assert result["releases"][0]["farmer_payout_status"] == "awaiting_funding"
    rows = (await session.execute(select(FarmerDisbursement))).scalars().all()
    assert sorted(d.amount_kobo for d in rows) == [3_000_000, 4_500_000]

    # Asking again for the same tranche returns the same record.
    milestone = await session.get(SponsorshipMilestone, s["milestones"][1]["id"])
    sponsorship = await agent.by_reference(session, s["reference"])
    again = await disbursements.create_for_milestone(session, sponsorship, milestone, world.farm)
    assert again.id in {d.id for d in rows}
    assert len((await session.execute(select(FarmerDisbursement))).scalars().all()) == 2


async def test_underfunded_disbursement_is_not_marked_paid(agent, session, world, banked):
    await start_paypal(agent, session, world)
    payout = (await session.execute(select(FarmerDisbursement))).scalars().one()
    await tuago.simulate_payment(payout.tuago_session_id, "success")
    tuago._mock_sessions[payout.tuago_session_id]["amountMinor"] = "1000"

    result = await disbursements.confirm(session, payout)

    assert result["code"] == "amount_mismatch"
    await session.refresh(payout)
    assert payout.status == "awaiting_funding"


async def test_payout_record_failure_never_undoes_the_charge(
    agent, session, world, verifier, monkeypatch
):
    async def boom(*args, **kwargs):
        raise RuntimeError("disbursement table on fire")

    s = await start_paypal(agent, session, world)
    monkeypatch.setattr(disbursements, "create_for_milestone", boom)

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "released" and result["releases"][0]["status"] == "paid"
    assert (await agent.get(session, s["id"]))["paid_minor"] == 5000


def test_conversion_rounds_to_whole_kobo():
    assert disbursements.convert_to_kobo(2000, 1500) == 3_000_000
    assert disbursements.convert_to_kobo(3333, 1575.5) == 5_251_142
