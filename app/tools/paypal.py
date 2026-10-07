"""PayPal payment tools — the international leg of Yieldra (sponsor payments).

Uses the PayPal REST APIs directly:
  * Orders v2            create / capture a one-off payment
  * Vault (with purchase) save the sponsor's PayPal account on the first payment
  * Orders v2 + vault_id  charge the saved account later, with no sponsor present
  * Payment Tokens v3     delete the saved account when a sponsorship is cancelled

Mocked until real PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are configured (see
``settings.paypal_live``). PAYPAL_ENV selects the sandbox or live API host.

All amounts are integers in minor units (US cents for USD), matching how the rest
of Yieldra stores money (kobo for NGN).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from app.agents.base import Tool
from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.tools.paypal")

_TIMEOUT = 30
# Seconds of validity to keep in hand before refreshing the OAuth token.
_TOKEN_MARGIN = 60

_token: dict[str, Any] = {"value": None, "expires_at": 0.0}

# Mock-mode state so create -> capture -> charge behave consistently in development.
_mock_orders: dict[str, dict[str, Any]] = {}


class PayPalError(RuntimeError):
    """A PayPal API call failed. ``issue`` is PayPal's machine-readable reason."""

    def __init__(self, message: str, status_code: int = 0, issue: str = "", debug_id: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.issue = issue
        self.debug_id = debug_id


def format_amount(minor_units: int) -> str:
    """Minor units -> PayPal decimal string, e.g. 2500 -> '25.00'."""
    if minor_units < 0:
        raise ValueError("amount must not be negative")
    return f"{minor_units // 100}.{minor_units % 100:02d}"


def parse_amount(value: str | None) -> int:
    """PayPal decimal string -> minor units, e.g. '25.00' -> 2500."""
    if not value:
        return 0
    whole, _, frac = str(value).partition(".")
    return int(whole or 0) * 100 + int((frac + "00")[:2])


def reset_state() -> None:
    """Forget the cached OAuth token and mock orders (used by tests)."""
    _token.update(value=None, expires_at=0.0)
    _mock_orders.clear()


# -- low level -------------------------------------------------------------
async def _access_token() -> str:
    """Client-credentials OAuth token, cached until shortly before it expires.

    No lock on purpose: two concurrent refreshes are harmless, and a module-level
    asyncio lock would break across the per-task event loops Celery uses.
    """
    if _token["value"] and time.time() < _token["expires_at"] - _TOKEN_MARGIN:
        return _token["value"]
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.paypal_api_base}/v1/oauth2/token",
            auth=(settings.paypal_client_id, settings.paypal_client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise PayPalError(
            "PayPal rejected the client credentials",
            status_code=resp.status_code,
            issue="AUTHENTICATION_FAILURE",
        )
    body = resp.json()
    _token["value"] = body["access_token"]
    _token["expires_at"] = time.time() + float(body.get("expires_in", 0))
    return _token["value"]


async def _request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Authenticated PayPal API call. Raises ``PayPalError`` on a non-2xx response."""
    headers = {
        "Authorization": f"Bearer {await _access_token()}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if request_id:
        # Idempotency key: retrying with the same id never charges twice.
        headers["PayPal-Request-Id"] = request_id
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.request(
            method, f"{settings.paypal_api_base}{path}", json=json_body, headers=headers
        )
    if resp.status_code >= 400:
        try:
            err = resp.json()
        except ValueError:
            err = {}
        details = err.get("details") or [{}]
        issue = details[0].get("issue") or err.get("name") or "PAYPAL_ERROR"
        message = details[0].get("description") or err.get("message") or resp.text[:200]
        log.warning(
            "paypal %s %s failed status=%s issue=%s debug_id=%s",
            method, path, resp.status_code, issue, err.get("debug_id"),
        )
        raise PayPalError(
            message, status_code=resp.status_code, issue=issue, debug_id=err.get("debug_id", "")
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def _purchase_unit(
    amount_minor: int, currency: str, reference: str, description: str
) -> dict[str, Any]:
    return {
        "reference_id": reference[:256],
        "custom_id": reference[:127],
        "description": description[:127],
        "amount": {"currency_code": currency, "value": format_amount(amount_minor)},
    }


def _summarise(order: dict[str, Any]) -> dict[str, Any]:
    """Flatten the parts of an order response Yieldra uses."""
    links = {link.get("rel"): link.get("href") for link in order.get("links", [])}
    unit = (order.get("purchase_units") or [{}])[0]
    captures = (unit.get("payments") or {}).get("captures") or []
    capture = captures[0] if captures else {}
    source = (order.get("payment_source") or {}).get("paypal") or {}
    vault = (source.get("attributes") or {}).get("vault") or {}
    amount = capture.get("amount") or unit.get("amount") or {}
    return {
        "order_id": order.get("id"),
        "status": order.get("status"),
        "approve_url": links.get("payer-action") or links.get("approve"),
        "reference": unit.get("custom_id") or unit.get("reference_id"),
        "capture_id": capture.get("id"),
        "capture_status": capture.get("status"),
        "amount_minor": parse_amount(amount.get("value")),
        "currency": amount.get("currency_code"),
        "vault_id": vault.get("id"),
        "vault_status": vault.get("status"),
        "payer_id": source.get("account_id") or (order.get("payer") or {}).get("payer_id"),
    }


# -- public API ------------------------------------------------------------
async def create_order(
    amount_minor: int,
    currency: str,
    reference: str,
    description: str,
    return_url: str,
    cancel_url: str,
    save_payment_method: bool = False,
) -> dict[str, Any]:
    """Create an order the payer must approve. Returns ``order_id`` and ``approve_url``.

    With ``save_payment_method`` the payer's PayPal account is vaulted when the order
    is captured, so later payments can be charged with ``charge_saved_method``.
    """
    if amount_minor <= 0:
        raise ValueError("amount must be positive")

    if not settings.paypal_live:
        order_id = f"MOCK-ORDER-{uuid.uuid4().hex[:12].upper()}"
        _mock_orders[order_id] = {
            "amount_minor": amount_minor,
            "currency": currency,
            "reference": reference,
            "save": save_payment_method,
            "status": "PAYER_ACTION_REQUIRED",
        }
        log.info("[MOCK paypal] create_order %s %s %s", order_id, format_amount(amount_minor), currency)
        sep = "&" if "?" in return_url else "?"
        return {
            "mock": True,
            "order_id": order_id,
            "status": "PAYER_ACTION_REQUIRED",
            # No PayPal page in mock mode: "approving" just follows the return URL.
            "approve_url": f"{return_url}{sep}token={order_id}&PayerID=MOCKPAYER",
            "reference": reference,
            "amount_minor": amount_minor,
            "currency": currency,
        }

    paypal_source: dict[str, Any] = {
        "experience_context": {
            "brand_name": "Yieldra",
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
            "return_url": return_url,
            "cancel_url": cancel_url,
        }
    }
    if save_payment_method:
        paypal_source["attributes"] = {
            "vault": {"store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT"}
        }
    order = await _request(
        "POST",
        "/v2/checkout/orders",
        {
            "intent": "CAPTURE",
            "purchase_units": [_purchase_unit(amount_minor, currency, reference, description)],
            "payment_source": {"paypal": paypal_source},
        },
        request_id=f"create-{reference}",
    )
    return _summarise(order)


async def capture_order(order_id: str) -> dict[str, Any]:
    """Capture an approved order. Returns capture details and, if saved, ``vault_id``."""
    if not settings.paypal_live:
        order = _mock_orders.get(order_id)
        if order is None:
            raise PayPalError("order not found", status_code=404, issue="RESOURCE_NOT_FOUND")
        order["status"] = "COMPLETED"
        log.info("[MOCK paypal] capture_order %s", order_id)
        return {
            "mock": True,
            "order_id": order_id,
            "status": "COMPLETED",
            "reference": order["reference"],
            "capture_id": f"MOCK-CAP-{order_id[-12:]}",
            "capture_status": "COMPLETED",
            "amount_minor": order["amount_minor"],
            "currency": order["currency"],
            "vault_id": f"MOCK-VAULT-{order_id[-12:]}" if order["save"] else None,
            "vault_status": "VAULTED" if order["save"] else None,
            "payer_id": "MOCKPAYER",
        }
    order = await _request(
        "POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id=f"capture-{order_id}"
    )
    return _summarise(order)


async def charge_saved_method(
    vault_id: str,
    amount_minor: int,
    currency: str,
    reference: str,
    description: str,
) -> dict[str, Any]:
    """Charge a saved PayPal account (merchant-initiated, no payer present).

    ``reference`` doubles as the idempotency key, so calling this twice for the same
    tranche can never charge the sponsor twice.
    """
    if amount_minor <= 0:
        raise ValueError("amount must be positive")

    if not settings.paypal_live:
        order_id = f"MOCK-ORDER-{uuid.uuid4().hex[:12].upper()}"
        log.info(
            "[MOCK paypal] charge_saved_method vault=%s %s %s ref=%s",
            vault_id, format_amount(amount_minor), currency, reference,
        )
        return {
            "mock": True,
            "order_id": order_id,
            "status": "COMPLETED",
            "reference": reference,
            "capture_id": f"MOCK-CAP-{order_id[-12:]}",
            "capture_status": "COMPLETED",
            "amount_minor": amount_minor,
            "currency": currency,
            "vault_id": vault_id,
        }

    order = await _request(
        "POST",
        "/v2/checkout/orders",
        {
            "intent": "CAPTURE",
            "purchase_units": [_purchase_unit(amount_minor, currency, reference, description)],
            "payment_source": {"paypal": {"vault_id": vault_id}},
        },
        request_id=f"charge-{reference}",
    )
    summary = _summarise(order)
    if summary["status"] == "APPROVED":
        # Some accounts return the order approved but not yet captured.
        summary = await capture_order(summary["order_id"])
    if summary["status"] != "COMPLETED" or summary.get("capture_status") not in (None, "COMPLETED"):
        raise PayPalError(
            f"saved-method charge not completed (order {summary['status']}, "
            f"capture {summary.get('capture_status')})",
            issue=summary.get("capture_status") or summary["status"] or "NOT_COMPLETED",
        )
    return summary


async def get_order(order_id: str) -> dict[str, Any]:
    """Look up an order's current status and amount."""
    if not settings.paypal_live:
        order = _mock_orders.get(order_id)
        if order is None:
            raise PayPalError("order not found", status_code=404, issue="RESOURCE_NOT_FOUND")
        return {
            "mock": True,
            "order_id": order_id,
            "status": order["status"],
            "reference": order["reference"],
            "amount_minor": order["amount_minor"],
            "currency": order["currency"],
        }
    return _summarise(await _request("GET", f"/v2/checkout/orders/{order_id}"))


async def delete_saved_method(vault_id: str) -> dict[str, Any]:
    """Delete a saved PayPal account so it can never be charged again."""
    if not settings.paypal_live:
        log.info("[MOCK paypal] delete_saved_method %s", vault_id)
        return {"mock": True, "deleted": True, "vault_id": vault_id}
    await _request("DELETE", f"/v3/vault/payment-tokens/{vault_id}")
    return {"deleted": True, "vault_id": vault_id}


# -- Tool specs for agent tool-calling ------------------------------------
# Read-only on purpose. Charging is done by SponsorshipAgent code behind the
# milestone-verification policy, never directly by a model.
PAYPAL_TOOLS: list[Tool] = [
    Tool(
        name="get_paypal_order",
        description="Look up the status and amount of a PayPal order by its order id.",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        handler=get_order,
    ),
]


# -- webhooks --------------------------------------------------------------
async def verify_webhook(headers: dict[str, str], raw_body: bytes) -> bool:
    """Ask PayPal whether a webhook delivery is genuine.

    Needs PAYPAL_WEBHOOK_ID (the id PayPal shows for the webhook registered on the
    app). The event is embedded byte-for-byte, because PayPal signs the raw body.
    """
    if not settings.paypal_live or settings._is_placeholder(settings.paypal_webhook_id):
        return False
    lowered = {k.lower(): v for k, v in headers.items()}
    fields = {
        "auth_algo": lowered.get("paypal-auth-algo"),
        "cert_url": lowered.get("paypal-cert-url"),
        "transmission_id": lowered.get("paypal-transmission-id"),
        "transmission_sig": lowered.get("paypal-transmission-sig"),
        "transmission_time": lowered.get("paypal-transmission-time"),
        "webhook_id": settings.paypal_webhook_id,
    }
    if not all(fields.values()):
        return False
    try:
        event_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return False
    body = json.dumps(fields)[:-1] + ',"webhook_event":' + event_text + "}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.paypal_api_base}/v1/notifications/verify-webhook-signature",
            content=body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {await _access_token()}",
                "Content-Type": "application/json",
            },
        )
    if resp.status_code != 200:
        log.warning("paypal webhook verification call failed status=%s", resp.status_code)
        return False
    return resp.json().get("verification_status") == "SUCCESS"
