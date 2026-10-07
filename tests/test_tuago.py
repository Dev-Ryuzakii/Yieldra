"""Tuago tool: mock mode, the exact requests sent in live mode, and webhook signatures."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.tools import tuago
from app.tools.tuago import TuagoError

API = "https://api.gettuago.com"


class FakeTuago:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], httpx.Response] = {}

    def on(self, method: str, path: str, data=None, status: int = 200, error: dict | None = None):
        body = error if error is not None else {"status": True, "message": "ok", "data": data}
        self.routes[(method, path)] = httpx.Response(status, json=body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes[(request.method, request.url.path)]


@pytest.fixture
def live(monkeypatch, live_setting) -> FakeTuago:
    live_setting(tuago_secret_key="sk_test_abc123", tuago_platform_fee_bps=250)
    fake = FakeTuago()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        tuago.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(fake.handler), **kw),
    )
    return fake


def test_key_decides_mock_test_or_live(live_setting):
    from app.config import settings

    assert not settings.tuago_live and settings.tuago_test_mode
    live_setting(tuago_secret_key="sk_test_x")
    assert settings.tuago_live and settings.tuago_test_mode
    live_setting(tuago_secret_key="sk_live_x")
    assert settings.tuago_live and not settings.tuago_test_mode


async def test_mock_checkout_lifecycle():
    checkout = await tuago.create_checkout(
        2_000_000, "kemi@example.com", "Sponsorship", "http://testserver/s/ref", subaccount="SUB1"
    )
    assert checkout["status"] == "PENDING" and checkout["amount_minor"] == 2_000_000
    assert checkout["checkout_url"].endswith(f"/pay/tuago/mock?session={checkout['session_id']}")

    details = await tuago.bank_transfer_details(checkout["session_id"])
    assert len(details["account_number"]) == 10 and details["amount_minor"] == 2_000_000

    assert not tuago.is_paid((await tuago.get_checkout(checkout["session_id"]))["status"])
    await tuago.simulate_payment(checkout["session_id"], "success")
    assert tuago.is_paid((await tuago.get_checkout(checkout["session_id"]))["status"])

    with pytest.raises(TuagoError):
        await tuago.get_checkout("MOCK-SESS-UNKNOWN")


async def test_create_subaccount_sends_bank_details_and_platform_cut(live):
    live.on("POST", "/v1/subaccounts", {
        "id": "sub_9x", "businessName": "Adunni Okafor", "bankCode": "058",
        "accountNumber": "0123456789", "accountName": "OKAFOR ADUNNI", "status": "ACTIVE",
    })

    sub = await tuago.create_subaccount("Adunni Okafor", "058", "0123456789")

    assert sub == {
        "subaccount_id": "sub_9x", "account_name": "OKAFOR ADUNNI",
        "bank_code": "058", "status": "ACTIVE",
    }
    request = live.requests[0]
    assert request.url == f"{API}/v1/subaccounts"
    assert request.headers["authorization"] == "Bearer sk_test_abc123"
    assert json.loads(request.content) == {
        "businessName": "Adunni Okafor",
        "bankCode": "058",
        "accountNumber": "0123456789",
        "splitType": "PERCENTAGE",
        "splitValue": 250,
        "feeBearer": "SUBACCOUNT",
    }


async def test_create_checkout_routes_through_the_subaccount(live):
    live.on("POST", "/v1/whatsapp/checkout/sessions", {
        "id": "wsp_sess_1", "mode": "hosted", "reference": "wsp_ref_1",
        "checkoutUrl": "https://pay.gettuago.com/c/wsp_sess_1",
        "whatsappUrl": "https://wa.me/234?text=pay", "amountMinor": "2000000",
        "currency": "NGN", "status": "PENDING", "expiresAt": "2026-10-07T12:00:00.000Z",
    })

    checkout = await tuago.create_checkout(
        2_000_000, "kemi@example.com", "Yieldra sponsorship: Adunni Cassava Plot",
        "http://x/s/ysp-1", subaccount="sub_9x", customer_name="Kemi",
    )

    assert checkout == {
        "session_id": "wsp_sess_1", "reference": "wsp_ref_1", "status": "PENDING",
        "amount_minor": 2_000_000, "currency": "NGN",
        "checkout_url": "https://pay.gettuago.com/c/wsp_sess_1",
        "whatsapp_url": "https://wa.me/234?text=pay",
        "expires_at": "2026-10-07T12:00:00.000Z",
    }
    assert json.loads(live.requests[0].content) == {
        "amount": 2_000_000,
        "customerEmail": "kemi@example.com",
        "description": "Yieldra sponsorship: Adunni Cassava Plot",
        "redirectUrl": "http://x/s/ysp-1",
        "mode": "hosted",
        "customerName": "Kemi",
        "subaccount": "sub_9x",
    }


async def test_bank_transfer_details_and_status(live):
    live.on("POST", "/v1/whatsapp/checkout/sessions/wsp_sess_1/pay/bank-transfer", {
        "bankName": "Wema Bank", "accountNumber": "9012345678", "accountName": "Tuago Gateway",
        "amountMinor": "2000000", "amount": "20000", "currency": "NGN",
        "reference": "wsp_ref_1", "expiresAt": "2026-10-07T12:30:00.000Z",
    })
    live.on("GET", "/v1/whatsapp/checkout/sessions/wsp_sess_1", {
        "id": "wsp_sess_1", "reference": "wsp_ref_1", "status": "SUCCESS", "amountMinor": "2000000",
    })

    details = await tuago.bank_transfer_details("wsp_sess_1")
    assert details["bank_name"] == "Wema Bank" and details["account_number"] == "9012345678"
    assert details["amount_minor"] == 2_000_000

    state = await tuago.get_checkout("wsp_sess_1")
    assert tuago.is_paid(state["status"]) and state["amount_minor"] == 2_000_000


async def test_errors_carry_tuagos_code(live):
    live.on("POST", "/v1/subaccounts", status=422, error={
        "status": False, "message": "Could not resolve account", "code": "validation_error",
    })

    with pytest.raises(TuagoError) as err:
        await tuago.create_subaccount("X", "058", "0000000000")
    assert err.value.code == "validation_error" and err.value.status_code == 422


async def test_simulation_is_refused_with_a_live_key(live_setting):
    live_setting(tuago_secret_key="sk_live_real")
    with pytest.raises(TuagoError) as err:
        await tuago.simulate_payment("wsp_sess_1")
    assert err.value.code == "test_only_live"


def test_webhook_signature_is_hmac_sha512_of_the_raw_body(live_setting):
    body = b'{"type":"charge.success","data":{"reference":"wsp_ref_1"}}'
    # Never accepted while the secret is unset.
    assert not tuago.verify_signature(body, "anything")

    live_setting(tuago_webhook_secret="whsec_test")
    good = hmac.new(b"whsec_test", body, hashlib.sha512).hexdigest()
    assert tuago.verify_signature(body, good)
    assert not tuago.verify_signature(body + b" ", good)
    assert not tuago.verify_signature(body, good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert not tuago.verify_signature(body, None)
