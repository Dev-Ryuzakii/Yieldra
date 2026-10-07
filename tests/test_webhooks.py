"""Provider webhooks: Tuago (HMAC-signed) and PayPal (verified with PayPal)."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.main import app
from app.models.payout import FarmerDisbursement
from app.models.sponsorship import SponsorshipMilestone
from app.tools import paypal, tuago

SECRET = "whsec_test"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def signed(event: dict) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    return body, {"X-Ollie-Signature": signature, "Content-Type": "application/json"}


async def start_naira(client, world) -> dict:
    resp = await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_ngn": 100_000},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# -- Tuago -----------------------------------------------------------------
async def test_tuago_charge_success_activates_the_naira_sponsorship(
    client, world, banked, sent, live_setting, session
):
    live_setting(tuago_webhook_secret=SECRET)
    started = await start_naira(client, world)
    milestone = (await session.execute(
        select(SponsorshipMilestone).where(SponsorshipMilestone.tuago_session_id.is_not(None))
    )).scalars().one()
    await tuago.simulate_payment(milestone.tuago_session_id, "success")

    body, headers = signed({"type": "charge.success", "data": {
        "reference": milestone.payment_reference, "amount_minor": 2_000_000,
    }})
    resp = await client.post("/webhook/tuago", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json()["result"] == {
        "kind": "tranche", "milestone_id": milestone.id, "status": "paid",
        "sponsorship_id": started["sponsorship"]["id"],
    }
    detail = (await client.get(f"/sponsorships/ref/{started['sponsorship']['reference']}")).json()
    assert detail["status"] == "active" and detail["paid"] == "₦20,000.00"


async def test_tuago_webhook_cannot_mark_an_unpaid_checkout_paid(
    client, world, banked, live_setting, session
):
    """A correctly signed notice is still checked against Tuago before money is recorded."""
    live_setting(tuago_webhook_secret=SECRET)
    started = await start_naira(client, world)
    milestone = (await session.execute(
        select(SponsorshipMilestone).where(SponsorshipMilestone.tuago_session_id.is_not(None))
    )).scalars().one()

    body, headers = signed({"type": "charge.success", "data": {
        "reference": milestone.payment_reference,
    }})
    resp = await client.post("/webhook/tuago", content=body, headers=headers)

    assert resp.json()["result"]["status"] == "pending"
    detail = (await client.get(f"/sponsorships/ref/{started['sponsorship']['reference']}")).json()
    assert detail["status"] == "pending_approval" and detail["paid_minor"] == 0


@pytest.mark.parametrize("signature", [None, "deadbeef"])
async def test_tuago_webhook_rejects_a_bad_signature(client, live_setting, signature):
    live_setting(tuago_webhook_secret=SECRET)
    headers = {"X-Ollie-Signature": signature} if signature else {}
    resp = await client.post("/webhook/tuago", content=b'{"type":"charge.success"}', headers=headers)
    assert resp.status_code == 401


async def test_tuago_webhook_is_closed_until_a_secret_is_set(client):
    body, headers = signed({"type": "charge.success", "data": {"reference": "x"}})
    assert (await client.post("/webhook/tuago", content=body, headers=headers)).status_code == 401


async def test_tuago_webhook_settles_a_farmer_disbursement(
    client, world, banked, sent, live_setting, session
):
    live_setting(tuago_webhook_secret=SECRET)
    started = (await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 100},
    )).json()
    await client.get(started["approve_url"].replace("http://testserver", ""))
    payout = (await session.execute(select(FarmerDisbursement))).scalars().one()
    await tuago.simulate_payment(payout.tuago_session_id, "success")

    body, headers = signed({"type": "charge.success", "data": {
        "checkout_session_id": payout.tuago_session_id,
    }})
    resp = await client.post("/webhook/tuago", content=body, headers=headers)

    assert resp.json()["result"]["kind"] == "disbursement"
    assert resp.json()["result"]["status"] == "paid"


async def test_tuago_non_charge_events_are_acknowledged_and_ignored(client, live_setting):
    live_setting(tuago_webhook_secret=SECRET)
    body, headers = signed({"type": "settlement.success", "data": {}})
    resp = await client.post("/webhook/tuago", content=body, headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "ignored"


# -- PayPal ----------------------------------------------------------------
PAYPAL_HEADERS = {
    "PayPal-Auth-Algo": "SHA256withRSA",
    "PayPal-Cert-Url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PayPal-Transmission-Id": "tx-1",
    "PayPal-Transmission-Sig": "sig==",
    "PayPal-Transmission-Time": "2026-10-07T03:00:00Z",
    "Content-Type": "application/json",
}


@pytest.fixture
def paypal_verifies(monkeypatch):
    """Make PayPal's verification answer whatever the test sets on ``.ok``."""
    state = type("State", (), {"ok": True, "calls": 0})()

    async def fake_verify(headers, raw_body):
        state.calls += 1
        return state.ok

    monkeypatch.setattr(paypal, "verify_webhook", fake_verify)
    return state


async def test_paypal_approval_webhook_completes_a_sponsor_who_never_came_back(
    client, world, sent, paypal_verifies
):
    started = (await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 100},
    )).json()
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]

    event = {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": order_id}}
    resp = await client.post("/webhook/paypal", json=event, headers=PAYPAL_HEADERS)

    assert resp.json() == {"status": "ok", "type": "CHECKOUT.ORDER.APPROVED", "result": "active"}
    detail = (await client.get(f"/sponsorships/ref/{started['sponsorship']['reference']}")).json()
    assert detail["status"] == "active" and detail["paid"] == "$20.00"

    # PayPal retries webhooks; the second delivery must not capture again.
    again = await client.post("/webhook/paypal", json=event, headers=PAYPAL_HEADERS)
    assert again.json()["result"] == "already_confirmed"


async def test_paypal_vault_webhook_supplies_a_late_payment_token(
    client, world, sent, verifier, paypal_verifies, monkeypatch
):
    real_capture = paypal.capture_order

    async def capture_without_vault(order_id):
        return {**await real_capture(order_id), "vault_id": None}

    monkeypatch.setattr(paypal, "capture_order", capture_without_vault)
    started = (await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 100},
    )).json()
    await client.get(started["approve_url"].replace("http://testserver", ""))
    ref = started["sponsorship"]["reference"]
    assert (await client.get(f"/sponsorships/ref/{ref}")).json()["has_saved_payment_method"] is False

    resp = await client.post("/webhook/paypal", headers=PAYPAL_HEADERS, json={
        "event_type": "VAULT.PAYMENT-TOKEN.CREATED",
        "resource": {"id": "8kk8451636559962p", "metadata": {
            "order_id": started["sponsorship"]["milestones"][0]["paypal_order_id"],
        }},
    })

    assert resp.json()["result"] == "attached"
    assert (await client.get(f"/sponsorships/ref/{ref}")).json()["has_saved_payment_method"] is True


async def test_unverified_paypal_webhook_is_rejected_and_does_nothing(
    client, world, paypal_verifies
):
    paypal_verifies.ok = False
    started = (await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 100},
    )).json()
    order_id = started["sponsorship"]["milestones"][0]["paypal_order_id"]

    resp = await client.post("/webhook/paypal", headers=PAYPAL_HEADERS, json={
        "event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": order_id},
    })

    assert resp.status_code == 401
    detail = (await client.get(f"/sponsorships/ref/{started['sponsorship']['reference']}")).json()
    assert detail["status"] == "pending_approval"


async def test_paypal_refund_webhook_flags_the_tranche(client, world, sent, paypal_verifies, session):
    started = (await client.post(
        "/sponsorships",
        json={"sponsor_id": world.sponsor.id, "farm_id": world.farm.id, "total_usd": 100},
    )).json()
    await client.get(started["approve_url"].replace("http://testserver", ""))
    capture_id = (await client.get(
        f"/sponsorships/ref/{started['sponsorship']['reference']}"
    )).json()["milestones"][0]["paypal_capture_id"]

    resp = await client.post("/webhook/paypal", headers=PAYPAL_HEADERS, json={
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {"id": "REFUND1", "links": [
            {"rel": "self", "href": "https://api.paypal.com/v2/payments/refunds/REFUND1"},
            {"rel": "up", "href": f"https://api.paypal.com/v2/payments/captures/{capture_id}"},
        ]},
    })

    assert resp.json()["status"] == "ok"
    detail = (await client.get(f"/sponsorships/ref/{started['sponsorship']['reference']}")).json()
    assert detail["milestones"][0]["failure_reason"] == "PAYPAL_REFUNDED"


async def test_paypal_verification_request_embeds_the_raw_event(monkeypatch, live_setting):
    """verify_webhook must send PayPal the event bytes exactly as received."""
    live_setting(paypal_client_id="id", paypal_client_secret="secret", paypal_webhook_id="WH-123")
    paypal.reset_state()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        paypal.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    raw = b'{"event_type":"CHECKOUT.ORDER.APPROVED",  "resource":{"id":"O1","amt":"20.00"}}'

    assert await paypal.verify_webhook(PAYPAL_HEADERS, raw) is True

    request = seen[-1]
    assert request.url.path == "/v1/notifications/verify-webhook-signature"
    assert raw in request.content  # byte-for-byte, odd spacing included
    sent = json.loads(request.content)
    assert sent["webhook_id"] == "WH-123" and sent["transmission_id"] == "tx-1"
    assert sent["webhook_event"]["resource"]["id"] == "O1"


async def test_paypal_webhooks_stay_closed_without_a_webhook_id(live_setting):
    live_setting(paypal_client_id="id", paypal_client_secret="secret")
    assert await paypal.verify_webhook(PAYPAL_HEADERS, b"{}") is False
