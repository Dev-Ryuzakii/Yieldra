"""PayPal tool: mock mode, and the exact requests sent to PayPal in live mode."""

from __future__ import annotations

import json

import httpx
import pytest

from app.tools import paypal
from app.tools.paypal import PayPalError

API = "https://api-m.sandbox.paypal.com"


class FakePayPal:
    """Stands in for PayPal's REST API and records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], httpx.Response] = {}

    def on(self, method: str, path: str, status: int = 200, body: dict | None = None):
        content = json.dumps(body).encode() if body is not None else b""
        self.routes[(method, path)] = httpx.Response(status, content=content)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        return self.routes[(request.method, request.url.path)]

    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/v1/oauth2/token"]


@pytest.fixture
def live(monkeypatch, live_setting) -> FakePayPal:
    """Switch the tool to live mode, talking to a fake PayPal."""
    live_setting(paypal_client_id="client-id", paypal_client_secret="client-secret")
    fake = FakePayPal()
    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(fake.handler), **kwargs)

    monkeypatch.setattr(paypal.httpx, "AsyncClient", client)
    return fake


def test_amount_formatting_round_trips():
    assert paypal.format_amount(2500) == "25.00"
    assert paypal.format_amount(5) == "0.05"
    assert paypal.format_amount(1999) == "19.99"
    assert paypal.parse_amount("25.00") == 2500
    assert paypal.parse_amount("19.9") == 1990
    assert paypal.parse_amount("7") == 700
    assert paypal.parse_amount(None) == 0
    with pytest.raises(ValueError):
        paypal.format_amount(-1)


async def test_mock_mode_order_lifecycle():
    order = await paypal.create_order(
        2000, "USD", "ref-1", "desc", "http://x/return", "http://x/cancel",
        save_payment_method=True,
    )
    assert order["mock"] and order["status"] == "PAYER_ACTION_REQUIRED"
    # In mock mode "approving" is just following the return URL.
    assert order["approve_url"] == f"http://x/return?token={order['order_id']}&PayerID=MOCKPAYER"

    captured = await paypal.capture_order(order["order_id"])
    assert captured["status"] == "COMPLETED"
    assert captured["amount_minor"] == 2000 and captured["currency"] == "USD"
    assert captured["vault_id"]

    charge = await paypal.charge_saved_method(captured["vault_id"], 3000, "USD", "ref-2", "d")
    assert charge["status"] == "COMPLETED" and charge["amount_minor"] == 3000

    with pytest.raises(PayPalError):
        await paypal.capture_order("MOCK-ORDER-UNKNOWN")


async def test_create_order_asks_paypal_to_save_the_payment_method(live):
    live.on("POST", "/v2/checkout/orders", body={
        "id": "5O190127TN364715T",
        "status": "PAYER_ACTION_REQUIRED",
        "links": [
            {"rel": "self", "href": f"{API}/v2/checkout/orders/5O190127TN364715T"},
            {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T"},
        ],
    })

    order = await paypal.create_order(
        2000, "USD", "ysp-abc-m1-a0", "Yieldra sponsorship", "http://x/return", "http://x/cancel",
        save_payment_method=True,
    )

    assert order["order_id"] == "5O190127TN364715T"
    assert order["approve_url"].endswith("token=5O190127TN364715T")

    token_req, create_req = live.requests
    assert token_req.url == f"{API}/v1/oauth2/token"
    assert token_req.headers["authorization"].startswith("Basic ")
    assert token_req.content == b"grant_type=client_credentials"

    assert create_req.headers["authorization"] == "Bearer tok-1"
    assert create_req.headers["paypal-request-id"] == "create-ysp-abc-m1-a0"
    body = json.loads(create_req.content)
    assert body["intent"] == "CAPTURE"
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "20.00"}
    assert body["purchase_units"][0]["custom_id"] == "ysp-abc-m1-a0"
    source = body["payment_source"]["paypal"]
    assert source["attributes"]["vault"] == {
        "store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT",
    }
    assert source["experience_context"]["return_url"] == "http://x/return"
    assert source["experience_context"]["cancel_url"] == "http://x/cancel"


async def test_capture_returns_capture_and_vault_ids(live):
    live.on("POST", "/v2/checkout/orders/ORDER1/capture", status=201, body={
        "id": "ORDER1",
        "status": "COMPLETED",
        "payment_source": {"paypal": {
            "account_id": "PAYER9",
            "attributes": {"vault": {"id": "9ab12345cd678901e", "status": "VAULTED"}},
        }},
        "purchase_units": [{
            "reference_id": "ref", "custom_id": "ref",
            "payments": {"captures": [{
                "id": "CAP1", "status": "COMPLETED",
                "amount": {"currency_code": "USD", "value": "20.00"},
            }]},
        }],
    })

    captured = await paypal.capture_order("ORDER1")

    assert captured["status"] == "COMPLETED"
    assert captured["capture_id"] == "CAP1"
    assert captured["amount_minor"] == 2000 and captured["currency"] == "USD"
    assert captured["vault_id"] == "9ab12345cd678901e"
    assert live.api_requests()[0].headers["paypal-request-id"] == "capture-ORDER1"


async def test_charge_saved_method_uses_vault_id_and_idempotency_key(live):
    live.on("POST", "/v2/checkout/orders", status=201, body={
        "id": "ORDER2",
        "status": "COMPLETED",
        "purchase_units": [{"custom_id": "ysp-abc-m2-a0", "payments": {"captures": [{
            "id": "CAP2", "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "30.00"},
        }]}}],
    })

    charge = await paypal.charge_saved_method(
        "9ab12345cd678901e", 3000, "USD", "ysp-abc-m2-a0", "Planting complete"
    )

    assert charge["capture_id"] == "CAP2" and charge["amount_minor"] == 3000
    request = live.api_requests()[0]
    assert request.headers["paypal-request-id"] == "charge-ysp-abc-m2-a0"
    body = json.loads(request.content)
    assert body["payment_source"] == {"paypal": {"vault_id": "9ab12345cd678901e"}}
    assert body["purchase_units"][0]["amount"]["value"] == "30.00"


async def test_declined_charge_raises_with_paypals_reason(live):
    live.on("POST", "/v2/checkout/orders", status=422, body={
        "name": "UNPROCESSABLE_ENTITY",
        "message": "The requested action could not be performed.",
        "debug_id": "dbg123",
        "details": [{"issue": "INSTRUMENT_DECLINED", "description": "The instrument was declined."}],
    })

    with pytest.raises(PayPalError) as err:
        await paypal.charge_saved_method("vault", 3000, "USD", "ref", "d")

    assert err.value.issue == "INSTRUMENT_DECLINED"
    assert err.value.status_code == 422
    assert err.value.debug_id == "dbg123"


async def test_charge_that_is_not_completed_is_treated_as_failed(live):
    live.on("POST", "/v2/checkout/orders", status=201, body={
        "id": "ORDER3",
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{
            "id": "CAP3", "status": "PENDING",
            "amount": {"currency_code": "USD", "value": "30.00"},
        }]}}],
    })

    with pytest.raises(PayPalError) as err:
        await paypal.charge_saved_method("vault", 3000, "USD", "ref", "d")
    assert err.value.issue == "PENDING"


async def test_oauth_token_is_reused_between_calls(live):
    live.on("GET", "/v2/checkout/orders/ORDER1", body={"id": "ORDER1", "status": "APPROVED"})

    await paypal.get_order("ORDER1")
    await paypal.get_order("ORDER1")

    token_calls = [r for r in live.requests if r.url.path == "/v1/oauth2/token"]
    assert len(token_calls) == 1


async def test_delete_saved_method_calls_the_vault_api(live):
    live.on("DELETE", "/v3/vault/payment-tokens/vault-1", status=204)

    assert await paypal.delete_saved_method("vault-1") == {"deleted": True, "vault_id": "vault-1"}
    assert live.api_requests()[0].method == "DELETE"


async def test_bad_credentials_raise_a_clear_error(monkeypatch, live_setting):
    live_setting(paypal_client_id="bad", paypal_client_secret="bad")
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "invalid_client"}))
    monkeypatch.setattr(paypal.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))

    with pytest.raises(PayPalError) as err:
        await paypal.get_order("ORDER1")
    assert err.value.issue == "AUTHENTICATION_FAILURE"
