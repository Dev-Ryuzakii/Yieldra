"""PayPal webhooks are verified before changing sponsorship state."""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from app.main import app
from app.tools import paypal


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_retired_tuago_webhook_is_not_registered(client):
    assert (await client.post("/webhook/tuago", json={"type": "charge.success"})).status_code == 404


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
