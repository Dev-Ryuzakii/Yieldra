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

    # "Approve on PayPal" -> PayPal redirects the sponsor back to our return URL.
    page = await client.get(path_of(started["approve_url"]))
    assert page.status_code == 200
    assert "Your sponsorship is active" in page.text
    assert "$20.00 of $100.00" in page.text and "Adunni Cassava Plot" in page.text

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

    assert (await client.get(url)).status_code == 200
    again = await client.get(url)

    assert again.status_code == 200
    detail = (await client.get(f"/sponsorships/{started['sponsorship']['id']}")).json()
    assert detail["paid_minor"] == 2000


async def test_return_with_unknown_order_shows_an_error_page(client, world):
    resp = await client.get("/sponsorships/paypal/return", params={"token": "NOPE"})
    assert resp.status_code == 404 and "not completed" in resp.text


async def test_cancel_page_cancels_the_pending_sponsorship(client, world):
    started = await start(client, world)
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]

    resp = await client.get("/sponsorships/paypal/cancel", params={"token": order_id})

    assert resp.status_code == 200 and "No payment was taken" in resp.text
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
        assert (await client.post(path, json={"approve": True})).status_code == 401
    assert verifier.calls == []

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
