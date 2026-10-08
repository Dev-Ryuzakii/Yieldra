"""HTTP surface: sponsorship endpoints, the operator guard, and chat routing."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio

from app.main import app

from tests.conftest import photo


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def start(client, world, total_usd: float = 100) -> dict:
    resp = await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": total_usd},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def path_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


async def test_sponsor_journey_over_http(client, world, sent, verifier):
    started = await start(client, world)
    assert started["status"] == "pending_approval"
    sponsorship_id = started["sponsorship"]["id"]

    # "Approve on PayPal" -> PayPal redirects the sponsor back to our return URL,
    # which captures the payment and sends them on to their own page.
    back = await client.get(path_of(started["approve_url"]))
    assert back.status_code == 303
    assert back.headers["location"] == f"/s/{started['sponsorship']['reference']}?paid=1"
    page = await client.get(back.headers["location"])
    assert page.status_code == 200 and '<div id="root"></div>' in page.text

    detail = (await client.get(f"/sponsorships/{sponsorship_id}")).json()
    assert detail["status"] == "active" and detail["paid_minor"] == 2000
    assert "vault" not in str(detail).replace("has_saved_payment_method", "")

    # Farmer sends a photo by chat with the caption PROOF.
    hook = await client.post(
        "/webhook/telegram",
        json={"chat": world.farmer.phone, "text": "PROOF", "image_url": photo("planted")},
    )
    assert hook.json()["result"]["agent"] == "sponsorship"
    assert hook.json()["result"]["status"] == "released"

    detail = (await client.get(f"/sponsorships/{sponsorship_id}")).json()
    assert detail["paid_minor"] == 5000
    assert detail["next_milestone"]["key"] == "established"

    listed = (await client.get("/sponsorships", params={"sponsor_id": world.sponsor.id})).json()
    assert [s["id"] for s in listed] == [sponsorship_id]

    # Sponsor asks for status by chat.
    hook = await client.post("/webhook/telegram", json={"chat": world.sponsor.phone, "text": "status"})
    assert "$50.00 of $100.00 released (2/4 stages)" in hook.json()["result"]["content"]


async def test_reloading_the_return_page_is_harmless(client, world, sent):
    started = await start(client, world)
    url = path_of(started["approve_url"])

    assert (await client.get(url)).status_code == 303
    again = await client.get(url)

    assert again.status_code == 303
    detail = (await client.get(f"/sponsorships/{started['sponsorship']['id']}")).json()
    assert detail["paid_minor"] == 2000


async def test_return_with_unknown_order_shows_an_error_page(client, world):
    resp = await client.get("/sponsorships/paypal/return", params={"token": "NOPE"})
    assert resp.status_code == 404 and "not completed" in resp.text


async def test_cancel_page_cancels_the_pending_sponsorship(client, world):
    started = await start(client, world)
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]

    resp = await client.get("/sponsorships/paypal/cancel", params={"token": order_id})

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/s/{started['sponsorship']['reference']}"
    detail = (await client.get(f"/sponsorships/{started['sponsorship']['id']}")).json()
    assert detail["status"] == "cancelled"


@pytest.mark.parametrize("total_usd", [5, 20_000])
async def test_amount_limits_are_enforced(client, world, total_usd):
    resp = await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": total_usd},
    )
    assert resp.status_code == 422


async def test_unknown_farm_is_404(client, world):
    resp = await client.post(
        "/sponsorships", json={"sponsor_id": world.sponsor.id, "farm_id": 9999, "total_usd": 50}
    )
    assert resp.status_code == 404


async def test_money_moving_endpoints_need_the_operator_key(
    client, world, sent, verifier, live_setting
):
    started = await start(client, world)
    await client.get(path_of(started["approve_url"]))
    live_setting(secret_key="s3cret-operator-key")
    evidence_url = f"/sponsorships/farms/{world.farm.id}/evidence"
    body = {"photo_url": photo("planted")}

    assert (await client.post(evidence_url, json=body)).status_code == 401
    wrong = await client.post(evidence_url, json=body, headers={"X-Admin-Key": "guess"})
    assert wrong.status_code == 401
    for path in (
        f"/sponsorships/{started['sponsorship']['id']}/cancel",
        "/sponsorships/milestones/1/review",
        "/sponsorships/milestones/1/retry-payment",
        "/sponsorships/milestones/1/simulate-payment",
        f"/farmers/{world.farmer.id}/payout-account",
        "/disbursements/1/funding",
        "/disbursements/1/check",
        "/disbursements/1/simulate",
    ):
        assert (await client.post(path, json={"approve": True})).status_code == 401, path
    # Anything that lists every sponsor, tranche or payout is closed too.
    for path in ("/sponsorships", f"/sponsorships/{started['sponsorship']['id']}",
                 "/console/ledger", "/disbursements", f"/farmers/{world.farmer.id}/payout-account"):
        assert (await client.get(path)).status_code == 401, path
    assert verifier.calls == []
    # The sponsor's own page stays open to whoever holds its reference.
    own = await client.get(f"/sponsorships/ref/{started['sponsorship']['reference']}")
    assert own.status_code == 200

    ok = await client.post(evidence_url, json=body, headers={"X-Admin-Key": "s3cret-operator-key"})
    assert ok.status_code == 200 and ok.json()["status"] == "released"


async def test_review_endpoint_releases_a_held_milestone(client, world, sent, verifier):
    from tests.conftest import make_verdict

    started = await start(client, world)
    await client.get(path_of(started["approve_url"]))
    verifier.next = make_verdict(confidence=0.6)
    held = await client.post(
        f"/sponsorships/farms/{world.farm.id}/evidence", json={"photo_url": photo("blurry")}
    )
    assert held.json()["status"] == "needs_review"
    milestone_id = started["sponsorship"]["milestones"][1]["id"]

    resp = await client.post(
        f"/sponsorships/milestones/{milestone_id}/review", json={"approve": True}
    )

    assert resp.status_code == 200 and resp.json()["status"] == "released"
    again = await client.post(
        f"/sponsorships/milestones/{milestone_id}/review", json={"approve": True}
    )
    assert again.status_code == 409


async def test_photo_without_proof_caption_still_goes_to_crop_advice(
    client, world, sent, verifier, monkeypatch
):
    started = await start(client, world)
    await client.get(path_of(started["approve_url"]))
    asked: list[str] = []

    async def fake_respond(self, session, farmer_id, message, photo_url=None):
        asked.append(message)
        return {"status": "ok", "reply": "Looks like mosaic virus."}

    monkeypatch.setattr("app.agents.advisory_agent.AdvisoryAgent.respond_to_farmer", fake_respond)
    hook = await client.post(
        "/webhook/telegram",
        json={"chat": world.farmer.phone, "text": "my leaves are yellow", "image_url": photo("leaf")},
    )

    assert hook.json()["result"]["agent"] == "advisory"
    assert asked == ["my leaves are yellow"] and verifier.calls == []


async def test_new_chat_user_can_register_as_sponsor(client, world, sent):
    await client.post("/webhook/telegram", json={"chat": "7777", "text": "hello"})
    assert "SPONSOR" in sent[-1][1]

    hook = await client.post("/webhook/telegram", json={"chat": "7777", "text": "sponsor"})

    assert hook.json()["result"] == {"status": "registered", "role": "sponsor", "user_id": hook.json()["result"]["user_id"]}


# -- web sponsors ----------------------------------------------------------
async def checkout(client, world, **overrides) -> httpx.Response:
    body = {"farm_id": world.farm.id, "amount": 100, "rail": "paypal",
            "name": "Kemi Adebayo", "email": "Kemi@Example.com", **overrides}
    return await client.post("/sponsorships/checkout", json=body)


async def test_web_sponsor_is_created_by_email_and_reused(client, world, session):
    from sqlalchemy import select

    from app.models.user import User

    first = await checkout(client, world)
    second = await checkout(client, world, amount=50)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["sponsorship"]["sponsor_id"] == second.json()["sponsorship"]["sponsor_id"]
    assert first.json()["track_url"] == f"http://testserver/s/{first.json()['sponsorship']['reference']}"
    users = (await session.execute(select(User).where(User.email == "kemi@example.com"))).scalars().all()
    assert len(users) == 1 and users[0].role.value == "sponsor" and users[0].phone.startswith("web:")


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "two words@example.com"])
async def test_web_checkout_rejects_a_bad_email(client, world, email):
    assert (await checkout(client, world, email=email)).status_code == 422


async def test_sponsor_can_stop_from_their_own_page(client, world, sent, verifier):
    started = (await checkout(client, world)).json()
    await client.get(path_of(started["approve_url"]))
    ref = started["sponsorship"]["reference"]

    stopped = await client.post(f"/sponsorships/ref/{ref}/cancel")

    assert stopped.status_code == 200
    body = stopped.json()
    assert body["status"] == "cancelled" and body["paid"] == "$20.00"
    assert body["has_saved_payment_method"] is False
    # Nothing more is collected, even for a photo that would pass.
    evidence = await client.post(
        f"/sponsorships/farms/{world.farm.id}/evidence", json={"photo_url": photo("planted")}
    )
    assert evidence.json()["status"] == "nothing_pending"
    assert (await client.post("/sponsorships/ref/ysp-unknown/cancel")).status_code == 404


async def test_naira_sponsor_journey_over_http(client, world, banked, sent, verifier):
    started = await checkout(client, world, rail="tuago", amount=100_000, email="tolu@example.com")
    assert started.status_code == 201, started.text
    body = started.json()
    ref = body["sponsorship"]["reference"]
    assert body["sponsorship"]["total"] == "₦100,000.00"

    # In mock mode the Tuago link pays and returns the sponsor to their page.
    back = await client.get(path_of(body["approve_url"]))
    assert back.status_code == 303 and back.headers["location"] == f"http://testserver/s/{ref}"
    detail = (await client.get(f"/sponsorships/ref/{ref}")).json()
    assert detail["status"] == "active" and detail["paid"] == "₦20,000.00"

    hook = await client.post(
        "/webhook/telegram",
        json={"chat": world.farmer.phone, "text": "PROOF", "image_url": photo("planted")},
    )
    assert hook.json()["result"]["status"] == "payment_requested"
    detail = (await client.get(f"/sponsorships/ref/{ref}")).json()
    link = detail["next_milestone"]["payment_url"]
    assert detail["next_milestone"]["status"] == "awaiting_payment" and link

    # Coming back from paying triggers a re-check with Tuago.
    await client.get(path_of(link))
    refreshed = (await client.post(f"/sponsorships/ref/{ref}/refresh")).json()
    assert refreshed["paid"] == "₦50,000.00"
    assert refreshed["next_milestone"]["key"] == "established"


async def test_naira_checkout_is_refused_until_the_farmer_has_a_bank_account(client, world):
    resp = await checkout(client, world, rail="tuago", amount=100_000)
    assert resp.status_code == 409 and "bank details" in resp.json()["detail"]


async def test_mock_checkout_page_is_closed_once_a_real_tuago_key_is_set(client, live_setting):
    live_setting(tuago_secret_key="sk_test_real")
    assert (await client.get("/pay/tuago/mock", params={"session": "x"})).status_code == 404


async def test_api_accepts_exactly_one_currency(client, world):
    both = await client.post("/sponsorships", json={
        "sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 50, "total_ngn": 50_000,
    })
    neither = await client.post("/sponsorships", json={
        "sponsor_id": world.sponsor.id, "farm_id": world.farm.id,
    })
    assert both.status_code == 422 and neither.status_code == 422


# -- farmer payouts over HTTP and chat -------------------------------------
async def test_farmer_adds_bank_account_by_chat_and_waiting_money_is_released(
    client, world, sent, session
):
    from sqlalchemy import select

    from app.models.payout import FarmerDisbursement

    started = await start(client, world)
    await client.get(path_of(started["approve_url"]))
    assert (await session.execute(select(FarmerDisbursement))).scalars().one().status == (
        "needs_bank_details"
    )

    banks = await client.post("/webhook/telegram", json={"chat": world.farmer.phone, "text": "BANKS"})
    assert "058  Guaranty Trust Bank" in banks.json()["result"]["content"]

    wrong = await client.post(
        "/webhook/telegram", json={"chat": world.farmer.phone, "text": "BANK 058 12345"}
    )
    assert "could not save" in wrong.json()["result"]["content"]

    saved = await client.post(
        "/webhook/telegram", json={"chat": world.farmer.phone, "text": "bank 058 0123456789"}
    )
    reply = saved.json()["result"]["content"]
    assert "Guaranty Trust Bank ****6789" in reply and "now being sent" in reply
    assert "0123456789" not in reply

    listed = (await client.get("/disbursements")).json()
    assert listed[0]["status"] == "awaiting_funding" and listed[0]["amount"] == "₦30,000.00"
    assert listed[0]["pay_account_number"]

    # Operator funds it (simulated in test mode) and the farmer is told.
    paid = await client.post(f"/disbursements/{listed[0]['id']}/simulate")
    assert paid.json()["status"] == "paid"
    assert "₦30,000.00 is on its way to your Guaranty Trust Bank account ****6789" in sent[-1][1]


async def test_operator_sets_payout_account_and_bad_numbers_are_refused(client, world):
    bad = await client.post(
        f"/farmers/{world.farmer.id}/payout-account",
        json={"bank_code": "058", "account_number": "12345"},
    )
    good = await client.post(
        f"/farmers/{world.farmer.id}/payout-account",
        json={"bank_code": "044", "account_number": "0123456789"},
    )

    assert bad.status_code == 422
    assert good.status_code == 201 and good.json()["bank_name"] == "Access Bank"
    shown = (await client.get(f"/farmers/{world.farmer.id}/payout-account")).json()
    assert shown == {
        "user_id": world.farmer.id, "bank_code": "044", "bank_name": "Access Bank",
        "account_number_masked": "****6789", "account_name": "ADUNNI OKAFOR",
    }
    assert [b["code"] for b in (await client.get("/banks")).json()][:2] == ["044", "011"]


# -- pages and console -----------------------------------------------------
async def test_pages_and_their_assets_are_served(client):
    import re

    for path in ("/", "/s/ysp-anything", "/console"):
        resp = await client.get(path)
        assert resp.status_code == 200 and '<div id="root"></div>' in resp.text, path
        if path != "/":
            assert resp.headers["X-Robots-Tag"] == "noindex"
    html = (await client.get("/")).text
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
        response = await client.get(asset)
        assert response.status_code == 200, asset


async def test_meta_reports_what_the_deployment_is_connected_to(client, live_setting):
    meta = (await client.get("/meta")).json()
    assert meta["paypal"] == "mock" and meta["tuago"] == "mock" and meta["model_ready"] is False
    assert meta["operator_key_required"] is False
    assert meta["limits"]["tuago"] == {"currency": "NGN", "min": 5000.0, "max": 5000000.0}

    live_setting(paypal_client_id="id", paypal_client_secret="s", tuago_secret_key="sk_live_x",
                 secret_key="real-secret", llm_api_key="sk-ant-real")
    meta = (await client.get("/meta")).json()
    assert meta["paypal"] == "sandbox" and meta["tuago"] == "live" and meta["model_ready"] is True
    assert meta["operator_key_required"] is True


async def test_farm_overview_and_ledger_follow_a_sponsorship(client, world, sent, verifier):
    before = (await client.get("/sponsorships/farms/overview")).json()
    assert before == [{
        "id": world.farm.id, "name": "Adunni Cassava Plot", "crop_type": "cassava",
        "location": "Ogun", "farmer_first_name": "Adunni", "naira_ready": False,
        "sponsors": 0, "latest_stage": None, "latest_photo_url": None,
        "cover_image_url": None, "cover_image_alt": None, "cover_image_credit": None,
        "cover_image_source": None, "cover_image_license": None,
    }]

    started = await start(client, world)
    await client.get(path_of(started["approve_url"]))
    await client.post(
        f"/sponsorships/farms/{world.farm.id}/evidence", json={"photo_url": photo("planted")}
    )

    after = (await client.get("/sponsorships/farms/overview")).json()[0]
    assert after["sponsors"] == 1 and after["latest_stage"] == "Planting complete"
    assert after["latest_photo_url"].startswith("/static/evidence/")

    rows = (await client.get("/console/ledger")).json()["rows"]
    assert [r["status"] for r in rows] == ["paid", "paid", "awaiting_evidence", "locked"]
    planted = rows[1]
    assert planted["farm"] == "Adunni Cassava Plot" and planted["sponsor"] == "Kemi Diaspora"
    assert planted["amount"] == 30.0 and planted["currency"] == "USD" and planted["rail"] == "paypal"
    assert planted["confidence"] == pytest.approx(0.92)
    assert planted["payout_status"] == "needs_bank_details" and planted["payout_naira"] == 45_000.0
    assert planted["farmer_has_bank"] is False
