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


async def test_paypal_is_the_only_checkout_rail(client, world):
    rejected = await checkout(client, world, rail="tuago", amount=100_000)
    assert rejected.status_code == 422
    rejected = await client.post("/sponsorships", json={
        "sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_ngn": 50_000,
    })
    assert rejected.status_code == 422
    assert (await client.get("/pay/tuago/mock?session=x")).status_code == 404
    assert (await client.post("/webhook/tuago", json={})).status_code == 404
    unavailable = await client.post("/investments", json={
        "farm_id": world.farm.id, "investor_id": world.sponsor.id,
        "amount_ngn": 10000, "payment_reference": "retired-rail",
    })
    assert unavailable.status_code == 410


async def test_paypal_capture_records_a_pending_farmer_payout(client, world, session):
    from sqlalchemy import select
    from app.models.payout import FarmerDisbursement

    started = await start(client, world)
    await client.get(path_of(started["approve_url"]))
    payout = (await session.execute(select(FarmerDisbursement))).scalars().one()
    assert payout.status == "pending_manual"
    assert payout.amount_kobo == 3_000_000
    assert payout.tuago_session_id is None
    assert (await client.get("/disbursements")).json()[0]["status"] == "pending_manual"


async def test_live_paypal_checkout_is_paused_without_farmer_settlement(client, world, live_setting):
    live_setting(paypal_env="live")
    result = await client.post("/sponsorships", json={
        "sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 100,
    })
    assert result.status_code == 409
    assert "farmer payouts" in result.json()["detail"]


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
    assert meta["paypal"] == "mock" and meta["model_ready"] is False
    assert meta["operator_key_required"] is False
    assert meta["farmer_payouts"] == "pending_manual"
    assert set(meta["limits"]) == {"paypal"}

    live_setting(paypal_client_id="id", paypal_client_secret="s", secret_key="real-secret", llm_api_key="sk-ant-real")
    meta = (await client.get("/meta")).json()
    assert meta["paypal"] == "sandbox" and meta["model_ready"] is True
    assert meta["operator_key_required"] is True


async def test_farm_overview_and_ledger_follow_a_sponsorship(client, world, sent, verifier):
    before = (await client.get("/sponsorships/farms/overview")).json()
    assert before == [{
        "id": world.farm.id, "name": "Adunni Cassava Plot", "crop_type": "cassava",
        "total_plots": 10, "available_plots": 10, "plot_price_usd": 40,
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
    assert planted["payout_status"] == "pending_manual" and planted["payout_naira"] == 45_000.0
    assert planted["farmer_has_bank"] is False


# -- plots and the website chat ----------------------------------------------
async def test_plots_price_the_sponsorship_and_are_taken_once_paid(client, world, session):
    farms = (await client.get("/sponsorships/farms/overview")).json()
    assert farms[0]["plot_price_usd"] == 40 and farms[0]["available_plots"] == 10

    started = await checkout(client, world, amount=None, plots=3)

    assert started.status_code == 201
    body = started.json()["sponsorship"]
    assert body["total"] == "$120.00" and body["plots"] == 3
    await session.refresh(world.farm)
    assert world.farm.available_plots == 10  # nothing is taken before PayPal capture
    await client.get(path_of(started.json()["approve_url"]))
    await session.refresh(world.farm)
    assert world.farm.available_plots == 7


async def test_cannot_sponsor_more_plots_than_are_open(client, world):
    too_many = await checkout(client, world, amount=None, plots=11)
    assert too_many.status_code == 409 and "10 plot" in too_many.json()["detail"]
    assert (await checkout(client, world, amount=None, plots=None)).status_code == 422


async def test_chat_answers_with_the_open_farms_in_context(client, world, monkeypatch):
    from app.config import settings
    from app.routers import chat

    seen = {}

    async def fake_run(messages, context=None):
        seen.update(messages=messages, context=context)
        return " A cassava plot is $40. "

    monkeypatch.setattr(type(settings), "llm_live", property(lambda self: True))
    monkeypatch.setattr(chat._agent, "run", fake_run)
    monkeypatch.setattr(chat, "_recent", chat.defaultdict(chat.deque))

    reply = await client.post("/chat", json={"messages": [{"role": "user", "content": "How much is a plot?"}]})

    assert reply.status_code == 200 and reply.json() == {"reply": "A cassava plot is $40."}
    farm = seen["context"]["open_farms"][0]
    assert farm["name"] == "Adunni Cassava Plot" and farm["price_per_plot_usd"] == 40 and farm["plots_left"] == 10
    assert (await client.post("/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})).status_code == 422


async def test_chat_is_rate_limited(client, world, monkeypatch):
    from app.config import settings
    from app.routers import chat

    async def fake_run(messages, context=None):
        return "ok"

    monkeypatch.setattr(type(settings), "llm_live", property(lambda self: True))
    monkeypatch.setattr(chat._agent, "run", fake_run)
    monkeypatch.setattr(chat, "_recent", chat.defaultdict(chat.deque))
    body = {"messages": [{"role": "user", "content": "hello"}]}

    codes = [(await client.post("/chat", json=body)).status_code for _ in range(chat._LIMIT + 1)]

    assert codes[:-1] == [200] * chat._LIMIT and codes[-1] == 429


async def test_telegram_sponsor_can_ask_for_open_farms_without_a_model(client, world, sent):
    await client.post("/webhook/telegram", json={"chat": "8080", "text": "sponsor"})

    await client.post("/webhook/telegram", json={"chat": "8080", "text": "list farm i can sponsor"})
    assert "Adunni Cassava Plot (cassava, Ogun, Nigeria): $40 per plot, 10 plot(s) left" in sent[-1][1]

    await client.post("/webhook/telegram", json={"chat": "8080", "text": "how am I doing?"})
    assert sent[-1][1] == "Yieldra: You have no active farm sponsorships yet."


async def test_telegram_sponsor_questions_go_to_the_assistant(client, world, sent, monkeypatch):
    from app.config import settings
    from app.routers import webhooks

    seen = {}

    async def fake_run(messages, context=None):
        seen.update(context=context)
        return "Adunni's cassava farm has 10 plots at $40."

    monkeypatch.setattr(type(settings), "llm_live", property(lambda self: True))
    monkeypatch.setattr(webhooks._sponsor_chat, "run", fake_run)
    await client.post("/webhook/telegram", json={"chat": "8181", "text": "sponsor"})

    await client.post("/webhook/telegram", json={"chat": "8181", "text": "which farm should I back?"})

    assert sent[-1][1] == "Adunni's cassava farm has 10 plots at $40."
    assert seen["context"]["my_sponsorships"] == "Yieldra: You have no active farm sponsorships yet."
    assert seen["context"]["open_farms"][0]["plots_left"] == 10
