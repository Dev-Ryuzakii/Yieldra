"""Sponsorship flow: PayPal approval, photo verification, tranche release and its guards."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.agents.sponsorship_agent import SponsorshipAgent
from app.models.sponsorship import MilestoneStatus, Sponsorship, SponsorshipMilestone
from app.models.user import User, UserRole
from app.tools import paypal
from app.tools.paypal import PayPalError

from tests.conftest import make_verdict, photo

TOTAL = 10_000  # $100.00 -> tranches of $20 / $30 / $30 / $20


@pytest.fixture
def agent() -> SponsorshipAgent:
    return SponsorshipAgent()


async def start_and_confirm(agent, session, world, total: int = TOTAL) -> dict:
    started = await agent.start(session, world.sponsor.id, world.farm.id, total)
    await session.commit()
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]
    confirmed = await agent.confirm_payment(session, order_id)
    assert confirmed["status"] == "active"
    return confirmed["sponsorship"]


def statuses(sponsorship: dict) -> list[str]:
    return [m["status"] for m in sponsorship["milestones"]]


async def test_start_plans_four_tranches_and_returns_paypal_link(agent, session, world):
    result = await agent.start(session, world.sponsor.id, world.farm.id, TOTAL)

    assert result["status"] == "pending_approval"
    assert "token=MOCK-ORDER-" in result["approve_url"]
    s = result["sponsorship"]
    assert [m["key"] for m in s["milestones"]] == ["kickoff", "planted", "established", "harvest"]
    assert [m["amount_minor"] for m in s["milestones"]] == [2000, 3000, 3000, 2000]
    assert statuses(s) == ["locked"] * 4
    assert s["paid_minor"] == 0
    assert "cassava stem cuttings" in s["milestones"][1]["evidence_required"]


async def test_odd_totals_still_add_up_exactly(agent, session, world):
    result = await agent.start(session, world.sponsor.id, world.farm.id, 9_999)
    amounts = [m["amount_minor"] for m in result["sponsorship"]["milestones"]]
    assert sum(amounts) == 9_999


@pytest.mark.parametrize("total", [999, 1_000_001])
async def test_amount_outside_limits_is_refused(agent, session, world, total):
    result = await agent.start(session, world.sponsor.id, world.farm.id, total)
    assert result["status"] == "invalid_amount"
    assert (await session.execute(select(Sponsorship))).first() is None


async def test_confirm_pays_first_tranche_and_saves_payment_method(agent, session, world, sent):
    s = await start_and_confirm(agent, session, world)

    assert s["status"] == "active"
    assert statuses(s) == ["paid", "awaiting_evidence", "locked", "locked"]
    assert s["paid_minor"] == 2000
    assert s["has_saved_payment_method"] is True
    # The token that authorises charges never leaves the server.
    assert "paypal_vault_id" not in s and "MOCK-VAULT" not in str(s)
    assert s["milestones"][0]["paypal_capture_id"].startswith("MOCK-CAP-")

    to_farmer = [text for chat, text in sent if chat == world.farmer.phone]
    to_sponsor = [text for chat, text in sent if chat == world.sponsor.phone]
    # The farmer is told in naira ($20 at the test rate of 1,500) and how to get paid.
    assert "PROOF" in to_farmer[0] and "₦30,000.00" in to_farmer[0]
    assert "farmer payout is pending" in to_farmer[0]
    assert "$20.00" in to_sponsor[0] and "$100.00" in to_sponsor[0]
    assert s["track_url"] in to_sponsor[0]


async def test_confirming_twice_does_not_capture_twice(agent, session, world, monkeypatch):
    s = await start_and_confirm(agent, session, world)
    order_id = s["milestones"][0]["paypal_order_id"]

    async def must_not_capture(order_id):
        raise AssertionError("captured a second time")

    monkeypatch.setattr(paypal, "capture_order", must_not_capture)
    again = await agent.confirm_payment(session, order_id)

    assert again["status"] == "already_confirmed"
    assert again["sponsorship"]["paid_minor"] == 2000


async def test_unknown_order_is_rejected(agent, session, world):
    assert (await agent.confirm_payment(session, "NOPE"))["status"] == "error"


async def test_verified_photo_releases_the_next_tranche(agent, session, world, sent, verifier):
    await start_and_confirm(agent, session, world)
    sent.clear()

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "released"
    assert result["milestone"] == "planted"
    assert result["releases"][0]["status"] == "paid"
    assert result["releases"][0]["amount_minor"] == 3000

    s = await agent.get(session, result["releases"][0]["sponsorship_id"])
    assert statuses(s) == ["paid", "paid", "awaiting_evidence", "locked"]
    assert s["paid_minor"] == 5000
    assert s["milestones"][1]["verdict_confidence"] == pytest.approx(0.92)

    # The model was asked about the right stage, with the photo inlined (no URL leaks).
    asked = verifier.calls[0]
    assert asked["crop_type"] == "cassava" and asked["milestone_title"] == "Planting complete"
    assert asked["photo_url"].startswith("data:image/jpeg;base64,")

    to_sponsor = [text for chat, text in sent if chat == world.sponsor.phone]
    to_farmer = [text for chat, text in sent if chat == world.farmer.phone]
    assert "$30.00" in to_sponsor[0] and "Planting complete" in to_sponsor[0]
    assert "₦45,000.00" in to_farmer[0] and "Crop established" in to_farmer[0]
    # The update is kept so the sponsor can read it on their page too.
    assert s["milestones"][1]["sponsor_update"] == to_sponsor[0]
    assert s["milestones"][1]["evidence_photo_url"].startswith("/static/evidence/")


async def test_rejected_photo_moves_no_money(agent, session, world, sent, verifier, monkeypatch):
    s = await start_and_confirm(agent, session, world)
    verifier.next = make_verdict(met=False, confidence=0.95)

    async def must_not_charge(**kwargs):
        raise AssertionError("charged on a rejected photo")

    monkeypatch.setattr(paypal, "charge_saved_method", must_not_charge)
    result = await agent.submit_evidence(session, world.farm.id, photo("bare-soil"))

    assert result["status"] == "rejected" and result["releases"] == []
    after = await agent.get(session, s["id"])
    assert statuses(after) == ["paid", "awaiting_evidence", "locked", "locked"]
    assert after["paid_minor"] == 2000
    assert "does not yet show" in sent[-1][1]


@pytest.mark.parametrize(
    "verdict",
    [
        make_verdict(crop=False, confidence=0.99),
        make_verdict(scene=False, confidence=0.99),
        make_verdict(confidence=0.30),
    ],
    ids=["wrong-crop", "not-a-farm-photo", "low-confidence"],
)
async def test_every_reject_reason_blocks_release(agent, session, world, verifier, verdict):
    await start_and_confirm(agent, session, world)
    verifier.next = verdict
    result = await agent.submit_evidence(session, world.farm.id, photo("x"))
    assert result["status"] == "rejected"


async def test_same_photo_cannot_be_submitted_twice(agent, session, world, sent, verifier):
    await start_and_confirm(agent, session, world)
    verifier.next = make_verdict(met=False)
    await agent.submit_evidence(session, world.farm.id, photo("same"))

    # Even if the model would now say yes, the photo was already judged.
    verifier.next = make_verdict()
    result = await agent.submit_evidence(session, world.farm.id, photo("same"))

    assert result["status"] == "duplicate_photo"
    assert len(verifier.calls) == 1
    assert "used before" in sent[-1][1]


async def test_photo_from_an_earlier_stage_cannot_unlock_the_next(agent, session, world, verifier):
    await start_and_confirm(agent, session, world)
    await agent.submit_evidence(session, world.farm.id, photo("planted"))

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "duplicate_photo"


async def test_unsure_verdict_is_held_for_a_person(agent, session, world, sent, verifier):
    s = await start_and_confirm(agent, session, world)
    verifier.next = make_verdict(confidence=0.60)

    result = await agent.submit_evidence(session, world.farm.id, photo("blurry"))

    assert result["status"] == "needs_review" and result["releases"] == []
    held = await agent.get(session, s["id"])
    assert statuses(held)[1] == "needs_review" and held["paid_minor"] == 2000
    milestone_id = held["milestones"][1]["id"]

    approved = await agent.review_milestone(session, milestone_id, approve=True)
    assert approved["status"] == "released"
    after = await agent.get(session, s["id"])
    assert statuses(after) == ["paid", "paid", "awaiting_evidence", "locked"]

    # Reviewing it again is refused: it is no longer waiting for review.
    assert (await agent.review_milestone(session, milestone_id, approve=True))["status"] == "error"


async def test_reviewer_can_reject_and_farmer_must_send_a_new_photo(
    agent, session, world, sent, verifier
):
    s = await start_and_confirm(agent, session, world)
    verifier.next = make_verdict(confidence=0.60)
    await agent.submit_evidence(session, world.farm.id, photo("blurry"))
    milestone_id = (await agent.get(session, s["id"]))["milestones"][1]["id"]

    result = await agent.review_milestone(session, milestone_id, approve=False)

    assert result["status"] == "rejected"
    after = await agent.get(session, s["id"])
    assert statuses(after)[1] == "awaiting_evidence" and after["paid_minor"] == 2000
    assert "not accepted" in sent[-1][1]
    again = await agent.submit_evidence(session, world.farm.id, photo("blurry"))
    assert again["status"] == "duplicate_photo"


async def test_model_outage_changes_nothing_and_photo_can_be_resent(
    agent, session, world, sent, verifier
):
    s = await start_and_confirm(agent, session, world)
    verifier.error = RuntimeError("provider down")

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "verification_unavailable"
    assert (await agent.get(session, s["id"]))["paid_minor"] == 2000

    verifier.error = None
    retry = await agent.submit_evidence(session, world.farm.id, photo("planted"))
    assert retry["status"] == "released"


async def test_full_run_completes_and_forgets_the_saved_payment_method(
    agent, session, world, verifier, monkeypatch
):
    deleted: list[str] = []
    real_delete = paypal.delete_saved_method

    async def spy_delete(vault_id):
        deleted.append(vault_id)
        return await real_delete(vault_id)

    monkeypatch.setattr(paypal, "delete_saved_method", spy_delete)
    s = await start_and_confirm(agent, session, world)

    for stage in ("planted", "established", "harvest"):
        result = await agent.submit_evidence(session, world.farm.id, photo(stage))
        assert result["status"] == "released" and result["milestone"] == stage

    done = await agent.get(session, s["id"])
    assert done["status"] == "completed"
    assert statuses(done) == ["paid"] * 4
    assert done["paid_minor"] == TOTAL
    assert done["next_milestone"] is None
    assert done["has_saved_payment_method"] is False
    assert len(deleted) == 1 and deleted[0].startswith("MOCK-VAULT-")

    nothing = await agent.submit_evidence(session, world.farm.id, photo("extra"))
    assert nothing["status"] == "nothing_pending"


async def test_declined_charge_can_be_retried_with_a_fresh_key(
    agent, session, world, sent, verifier, monkeypatch
):
    s = await start_and_confirm(agent, session, world)
    references: list[str] = []
    real_charge = paypal.charge_saved_method

    async def flaky_charge(**kwargs):
        references.append(kwargs["reference"])
        if len(references) == 1:
            raise PayPalError("declined", status_code=422, issue="INSTRUMENT_DECLINED")
        return await real_charge(**kwargs)

    monkeypatch.setattr(paypal, "charge_saved_method", flaky_charge)
    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "payment_failed"
    assert result["verdict"]["milestone_met"] is True  # the photo passed...
    assert result["releases"][0] == {
        "sponsorship_id": s["id"],
        "milestone_id": s["milestones"][1]["id"],
        "amount_minor": 3000,
        "rail": "paypal",
        "status": "payment_failed",  # ...but the money did not move
        "issue": "INSTRUMENT_DECLINED",
    }
    failed = await agent.get(session, s["id"])
    assert statuses(failed)[1] == "payment_failed" and failed["paid_minor"] == 2000
    assert statuses(failed)[2] == "locked"
    assert any("could not charge" in text for chat, text in sent if chat == world.sponsor.phone)
    assert any("being processed" in text for chat, text in sent if chat == world.farmer.phone)

    retry = await agent.retry_payment(session, s["milestones"][1]["id"])

    assert retry["status"] == "paid"
    assert statuses(await agent.get(session, s["id"]))[:3] == ["paid", "paid", "awaiting_evidence"]
    # PayPal refused the first attempt, so the retry is a new request.
    assert references[0].endswith("-m2-a0") and references[1].endswith("-m2-a1")


async def test_timeout_retry_reuses_the_idempotency_key(agent, session, world, verifier, monkeypatch):
    s = await start_and_confirm(agent, session, world)
    references: list[str] = []
    real_charge = paypal.charge_saved_method

    async def timing_out_charge(**kwargs):
        references.append(kwargs["reference"])
        if len(references) == 1:
            raise httpx.ReadTimeout("no answer from PayPal")
        return await real_charge(**kwargs)

    monkeypatch.setattr(paypal, "charge_saved_method", timing_out_charge)
    await agent.submit_evidence(session, world.farm.id, photo("planted"))
    failed = await agent.get(session, s["id"])
    assert statuses(failed)[1] == "payment_failed"
    assert failed["milestones"][1]["failure_reason"] == "NETWORK_ReadTimeout"

    retry = await agent.retry_payment(session, s["milestones"][1]["id"])

    assert retry["status"] == "paid"
    # Outcome of the first attempt is unknown, so the same key must be sent again.
    assert references[0] == references[1]


async def test_missing_saved_method_fails_the_tranche_cleanly(
    agent, session, world, verifier, monkeypatch
):
    real_capture = paypal.capture_order

    async def capture_without_vault(order_id):
        return {**await real_capture(order_id), "vault_id": None}

    monkeypatch.setattr(paypal, "capture_order", capture_without_vault)
    started = await agent.start(session, world.sponsor.id, world.farm.id, TOTAL)
    await session.commit()
    confirmed = await agent.confirm_payment(
        session, started["sponsorship"]["milestones"][0]["paypal_order_id"]
    )
    assert confirmed["status"] == "active" and confirmed["saved_payment_method"] is False

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "payment_failed"
    assert result["releases"][0]["issue"] == "NO_SAVED_PAYMENT_METHOD"


async def test_captured_amount_mismatch_does_not_activate(agent, session, world, monkeypatch):
    real_capture = paypal.capture_order

    async def short_capture(order_id):
        return {**await real_capture(order_id), "amount_minor": 1}

    monkeypatch.setattr(paypal, "capture_order", short_capture)
    started = await agent.start(session, world.sponsor.id, world.farm.id, TOTAL)
    await session.commit()

    result = await agent.confirm_payment(
        session, started["sponsorship"]["milestones"][0]["paypal_order_id"]
    )

    assert result["status"] == "error"
    assert (await agent.get(session, started["sponsorship"]["id"]))["status"] == "pending_approval"


async def test_cancel_stops_all_future_charges(agent, session, world, sent, verifier):
    s = await start_and_confirm(agent, session, world)

    result = await agent.cancel(session, s["id"])

    assert result["status"] == "cancelled"
    after = await agent.get(session, s["id"])
    assert after["status"] == "cancelled" and after["has_saved_payment_method"] is False
    assert after["paid_minor"] == 2000  # what was paid stays paid
    assert any("not be charged again" in text for chat, text in sent)
    blocked = await agent.submit_evidence(session, world.farm.id, photo("planted"))
    assert blocked["status"] == "nothing_pending"


async def test_backing_out_at_paypal_cancels_the_pending_sponsorship(agent, session, world):
    started = await agent.start(session, world.sponsor.id, world.farm.id, TOTAL)
    await session.commit()
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]

    assert (await agent.abandon(session, order_id))["status"] == "cancelled"
    assert (await agent.confirm_payment(session, order_id))["status"] == "error"


async def test_one_photo_releases_every_sponsor_waiting_on_that_stage(
    agent, session, world, verifier
):
    second = User(name="Tunde Abroad", phone="2002", role=UserRole.sponsor)
    session.add(second)
    await session.commit()
    first = await start_and_confirm(agent, session, world)
    started = await agent.start(session, second.id, world.farm.id, 20_000)
    await session.commit()
    await agent.confirm_payment(session, started["sponsorship"]["milestones"][0]["paypal_order_id"])

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert len(verifier.calls) == 1  # judged once, not once per sponsor
    assert sorted(r["amount_minor"] for r in result["releases"]) == [3000, 6000]
    assert all(r["status"] == "paid" for r in result["releases"])
    assert (await agent.get(session, first["id"]))["paid_minor"] == 5000
    assert (await agent.get(session, started["sponsorship"]["id"]))["paid_minor"] == 10_000


async def test_failed_chat_message_never_undoes_a_payment(agent, session, world, verifier, monkeypatch):
    async def broken_send(phone, message):
        raise httpx.ConnectError("telegram down")

    monkeypatch.setattr("app.services.notify.send_text_message", broken_send)
    s = await start_and_confirm(agent, session, world)

    result = await agent.submit_evidence(session, world.farm.id, photo("planted"))

    assert result["status"] == "released"
    assert (await agent.get(session, s["id"]))["paid_minor"] == 5000


async def test_status_text_summarises_progress_for_the_sponsor(agent, session, world, verifier):
    assert "no active" in await agent.status_text(session, world.sponsor.id)
    await start_and_confirm(agent, session, world)

    text = await agent.status_text(session, world.sponsor.id)

    assert "Adunni Cassava Plot: $20.00 of $100.00 released (1/4 stages)" in text
    assert "Next: Planting complete" in text


async def test_charge_reference_is_stable_and_unique_per_tranche(agent, session, world, verifier, monkeypatch):
    references: list[str] = []
    real_charge = paypal.charge_saved_method

    async def spy(**kwargs):
        references.append(kwargs["reference"])
        return await real_charge(**kwargs)

    monkeypatch.setattr(paypal, "charge_saved_method", spy)
    s = await start_and_confirm(agent, session, world)
    for stage in ("planted", "established", "harvest"):
        await agent.submit_evidence(session, world.farm.id, photo(stage))

    root = s["reference"]
    assert references == [f"{root}-m2-a0", f"{root}-m3-a0", f"{root}-m4-a0"]
    rows = (await session.execute(select(SponsorshipMilestone))).scalars().all()
    assert all(m.status == MilestoneStatus.paid for m in rows)
