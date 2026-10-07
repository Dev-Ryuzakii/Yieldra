"""Tuago tools — the naira leg of Yieldra.

Tuago (https://developer.gettuago.com) is a WhatsApp-native collection gateway on
Nigerian bank rails. It collects money; it has no API for sending it. Third parties
are paid through *split settlement*: a subaccount holds a vendor's verified bank
account, and their share of any collection routed through it settles to that bank.

Yieldra uses it for:
  * farmer payout accounts   banks list, account name enquiry, subaccounts
  * naira collections        checkout sessions (bank transfer inside WhatsApp)
  * confirmation             session status, charge verify, signed webhooks

Mocked until a real TUAGO_SECRET_KEY (sk_test_... or sk_live_...) is configured.
All amounts are integers in kobo.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

import httpx

from app.agents.base import Tool
from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.tools.tuago")

_TIMEOUT = 20
SIGNATURE_HEADER = "x-ollie-signature"
# Tuago documents PENDING for an open session; a settled one is reported with one of these.
PAID_STATUSES = {"SUCCESS", "SUCCESSFUL", "PAID", "COMPLETED"}
FAILED_STATUSES = {"FAILED", "ABANDONED", "REVERSED", "EXPIRED", "CANCELLED"}

# Mock-mode state.
_mock_sessions: dict[str, dict[str, Any]] = {}
_mock_subaccounts: dict[str, dict[str, Any]] = {}
_MOCK_BANKS = [
    {"name": "Access Bank", "code": "044"},
    {"name": "First Bank of Nigeria", "code": "011"},
    {"name": "Guaranty Trust Bank", "code": "058"},
    {"name": "United Bank for Africa", "code": "033"},
    {"name": "Zenith Bank", "code": "057"},
]


class TuagoError(RuntimeError):
    """A Tuago API call failed. ``code`` is Tuago's stable machine-readable reason."""

    def __init__(self, message: str, status_code: int = 0, code: str = "tuago_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def reset_state() -> None:
    """Forget mock sessions and subaccounts (used by tests)."""
    _mock_sessions.clear()
    _mock_subaccounts.clear()


def is_paid(status: str | None) -> bool:
    return (status or "").upper() in PAID_STATUSES


def is_failed(status: str | None) -> bool:
    return (status or "").upper() in FAILED_STATUSES


# -- low level -------------------------------------------------------------
async def _request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    """Authenticated Tuago call. Unwraps the ``{status, message, data}`` envelope."""
    headers = {
        "Authorization": f"Bearer {settings.tuago_secret_key}",
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.request(
            method,
            f"{settings.tuago_base_url.rstrip('/')}{path}",
            json=json_body,
            headers=headers,
        )
    try:
        envelope = resp.json() if resp.content else {}
    except ValueError:
        envelope = {}
    if resp.status_code >= 400 or envelope.get("status") is False:
        code = envelope.get("code") or "tuago_error"
        message = envelope.get("message") or f"Tuago request failed ({resp.status_code})"
        log.warning("tuago %s %s failed status=%s code=%s", method, path, resp.status_code, code)
        raise TuagoError(message, status_code=resp.status_code, code=str(code))
    return envelope.get("data", envelope)


def _minor(value: Any) -> int:
    """Tuago returns kobo amounts as strings."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _session_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": data.get("id"),
        "reference": data.get("reference"),
        "status": data.get("status"),
        "amount_minor": _minor(data.get("amountMinor")),
        "currency": data.get("currency") or "NGN",
        "checkout_url": data.get("checkoutUrl"),
        "whatsapp_url": data.get("whatsappUrl"),
        "expires_at": data.get("expiresAt"),
    }


# -- banks and payout accounts --------------------------------------------
async def list_banks() -> list[dict[str, str]]:
    """Banks Tuago can settle to: ``[{"name", "code"}]``."""
    if not settings.tuago_live:
        return list(_MOCK_BANKS)
    data = await _request("GET", "/v1/banks")
    return [{"name": b.get("name", ""), "code": str(b.get("code", ""))} for b in data or []]


async def resolve_account(bank_code: str, account_number: str) -> dict[str, Any]:
    """Name enquiry: who owns this account number at this bank."""
    if not settings.tuago_live:
        log.info("[MOCK tuago] resolve_account %s ****%s", bank_code, account_number[-4:])
        return {"mock": True, "account_name": "MOCK ACCOUNT HOLDER"}
    data = await _request(
        "POST", "/v1/banks/resolve", {"bankCode": bank_code, "accountNumber": account_number}
    )
    return {"account_name": data.get("accountName", "")}


async def create_subaccount(
    business_name: str, bank_code: str, account_number: str
) -> dict[str, Any]:
    """Register a bank account for split settlement. Tuago verifies the account name."""
    if not settings.tuago_live:
        sub_id = f"MOCK-SUB-{uuid.uuid4().hex[:10].upper()}"
        record = {
            "mock": True,
            "subaccount_id": sub_id,
            "account_name": business_name.upper(),
            "bank_code": bank_code,
            "status": "ACTIVE",
        }
        _mock_subaccounts[sub_id] = record
        log.info("[MOCK tuago] create_subaccount %s ****%s", sub_id, account_number[-4:])
        return record
    data = await _request(
        "POST",
        "/v1/subaccounts",
        {
            "businessName": business_name,
            "bankCode": bank_code,
            "accountNumber": account_number,
            # splitValue is the platform's cut; the rest settles to the subaccount.
            "splitType": "PERCENTAGE",
            "splitValue": settings.tuago_platform_fee_bps,
            "feeBearer": "SUBACCOUNT",
        },
    )
    return {
        "subaccount_id": data.get("id"),
        "account_name": data.get("accountName", ""),
        "bank_code": data.get("bankCode", bank_code),
        "status": data.get("status"),
    }


# -- collections -----------------------------------------------------------
async def create_checkout(
    amount_minor: int,
    customer_email: str,
    description: str,
    redirect_url: str,
    subaccount: str | None = None,
    customer_name: str | None = None,
    customer_phone: str | None = None,
) -> dict[str, Any]:
    """Create a hosted checkout the payer opens in WhatsApp or the browser.

    With ``subaccount`` the payment is split: that account's share settles to its bank.
    """
    if amount_minor <= 0:
        raise ValueError("amount must be positive")

    if not settings.tuago_live:
        session_id = f"MOCK-SESS-{uuid.uuid4().hex[:12].upper()}"
        reference = f"MOCK-REF-{uuid.uuid4().hex[:12].upper()}"
        base = settings.public_base_url.rstrip("/")
        _mock_sessions[session_id] = {
            "id": session_id,
            "reference": reference,
            "status": "PENDING",
            "amountMinor": str(amount_minor),
            "currency": "NGN",
            # No Tuago page in mock mode: opening the link pays and returns.
            "checkoutUrl": f"{base}/pay/tuago/mock?session={session_id}",
            "whatsappUrl": f"{base}/pay/tuago/mock?session={session_id}",
            "expiresAt": None,
            "redirectUrl": redirect_url,
            "subaccount": subaccount,
        }
        log.info("[MOCK tuago] create_checkout %s amount=%s", session_id, amount_minor)
        return {"mock": True, **_session_summary(_mock_sessions[session_id])}

    body: dict[str, Any] = {
        "amount": amount_minor,
        "customerEmail": customer_email,
        "description": description[:140],
        "redirectUrl": redirect_url,
        "mode": "hosted",
    }
    if customer_name:
        body["customerName"] = customer_name
    if customer_phone:
        body["customerPhone"] = customer_phone
    if subaccount:
        body["subaccount"] = subaccount
    return _session_summary(await _request("POST", "/v1/whatsapp/checkout/sessions", body))


async def bank_transfer_details(session_id: str) -> dict[str, Any]:
    """The virtual account to pay a session by bank transfer."""
    if not settings.tuago_live:
        session = _mock_sessions.get(session_id)
        if session is None:
            raise TuagoError("session not found", status_code=404, code="not_found")
        return {
            "mock": True,
            "bank_name": "Mock Bank",
            "account_number": "9"
            + f"{int(hashlib.sha256(session_id.encode()).hexdigest(), 16) % 10**9:09d}",
            "account_name": "Tuago Gateway",
            "amount_minor": _minor(session["amountMinor"]),
            "reference": session["reference"],
            "expires_at": None,
        }
    data = await _request("POST", f"/v1/whatsapp/checkout/sessions/{session_id}/pay/bank-transfer")
    return {
        "bank_name": data.get("bankName"),
        "account_number": data.get("accountNumber"),
        "account_name": data.get("accountName"),
        "amount_minor": _minor(data.get("amountMinor")),
        "reference": data.get("reference"),
        "expires_at": data.get("expiresAt"),
    }


async def get_checkout(session_id: str) -> dict[str, Any]:
    """Current state of a checkout session."""
    if not settings.tuago_live:
        session = _mock_sessions.get(session_id)
        if session is None:
            raise TuagoError("session not found", status_code=404, code="not_found")
        return {"mock": True, **_session_summary(session)}
    return _session_summary(await _request("GET", f"/v1/whatsapp/checkout/sessions/{session_id}"))


async def simulate_payment(session_id: str, outcome: str = "success") -> dict[str, Any]:
    """Sandbox only: resolve a session's payment without a real transfer."""
    if not settings.tuago_test_mode:
        raise TuagoError("simulation is not available with a live key", 403, "test_only_live")
    if not settings.tuago_live:
        session = _mock_sessions.get(session_id)
        if session is None:
            raise TuagoError("session not found", status_code=404, code="not_found")
        session["status"] = "SUCCESS" if outcome == "success" else "FAILED"
        return {"mock": True, "status": session["status"], "reference": session["reference"]}
    data = await _request(
        "POST", f"/v1/whatsapp/checkout/sessions/{session_id}/simulate", {"outcome": outcome}
    )
    return {"status": data.get("status"), "reference": data.get("reference")}


async def verify_charge(reference: str) -> dict[str, Any]:
    """Ask Tuago to confirm a charge by reference (naira investment payments)."""
    if not settings.tuago_live:
        log.info("[MOCK tuago] verify_charge ref=%s", reference)
        return {"mock": True, "status": "SUCCESS", "reference": reference, "amount_minor": 5_000_000}
    data = await _request("POST", f"/v1/charges/{reference}/verify")
    return {
        "status": data.get("status"),
        "reference": data.get("reference"),
        "amount_minor": _minor(data.get("amountMinor")),
        "payer_name": data.get("payerAccountName"),
        "payer_bank": data.get("payerBank"),
    }


def mock_redirect_url(session_id: str) -> str | None:
    """Where a mock checkout sends the payer back to (mock mode only)."""
    session = _mock_sessions.get(session_id)
    return session.get("redirectUrl") if session else None


# -- webhooks --------------------------------------------------------------
def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """``X-Ollie-Signature`` is HMAC-SHA512(webhook secret, raw body), hex-encoded."""
    secret = settings.tuago_webhook_secret
    if not signature or settings._is_placeholder(secret):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


# -- Tool specs for agent tool-calling ------------------------------------
# Read-only on purpose, like the PayPal tools: no model moves money.
TUAGO_TOOLS: list[Tool] = [
    Tool(
        name="list_banks",
        description="List Nigerian banks and their codes, for collecting payout details.",
        parameters={"type": "object", "properties": {}},
        handler=list_banks,
    ),
]
