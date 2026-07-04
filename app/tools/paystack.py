"""Paystack payment + payout tools.

Mocked until a real PAYSTACK_SECRET_KEY is configured (see settings.paystack_live);
real Paystack API calls are made once a live key is present.
All amounts are in kobo (Paystack's native unit, == our DB unit).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.agents.base import Tool
from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.tools.paystack")

_BASE = "https://api.paystack.co"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type": "application/json",
    }


async def verify_payment(reference: str) -> dict[str, Any]:
    """Verify a Paystack transaction by reference. Returns status + amount (kobo)."""
    if not settings.paystack_live:
        log.info("[MOCK paystack] verify_payment ref=%s", reference)
        return {
            "mock": True,
            "status": "success",
            "reference": reference,
            "amount": 5_000_000,  # ₦50,000 in kobo
            "currency": "NGN",
        }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_BASE}/transaction/verify/{reference}", headers=_headers()
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return {
            "status": data.get("status"),
            "reference": data.get("reference"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
        }


async def create_transfer_recipient(
    name: str, account_number: str, bank_code: str
) -> dict[str, Any]:
    """Create a Paystack transfer recipient; returns a recipient_code for payouts."""
    if not settings.paystack_live:
        log.info("[MOCK paystack] create_recipient %s %s", name, account_number)
        return {
            "mock": True,
            "recipient_code": f"RCP_mock_{account_number[-4:]}",
            "name": name,
        }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{_BASE}/transferrecipient",
            headers=_headers(),
            json={
                "type": "nuban",
                "name": name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": "NGN",
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


async def initiate_payout(recipient_code: str, amount: int, reason: str) -> dict[str, Any]:
    """Initiate a Paystack transfer (payout) to a recipient. ``amount`` in kobo."""
    if not settings.paystack_live:
        log.info("[MOCK paystack] payout %s amount=%s reason=%s", recipient_code, amount, reason)
        return {
            "mock": True,
            "status": "success",
            "transfer_code": f"TRF_mock_{recipient_code[-4:]}",
            "amount": amount,
            "reason": reason,
        }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{_BASE}/transfer",
            headers=_headers(),
            json={
                "source": "balance",
                "amount": amount,
                "recipient": recipient_code,
                "reason": reason,
            },
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


PAYSTACK_TOOLS: list[Tool] = [
    Tool(
        name="verify_payment",
        description="Verify a Paystack payment by transaction reference.",
        parameters={
            "type": "object",
            "properties": {"reference": {"type": "string"}},
            "required": ["reference"],
        },
        handler=verify_payment,
    ),
    Tool(
        name="initiate_payout",
        description="Send money to a recipient via Paystack transfer. Amount in kobo.",
        parameters={
            "type": "object",
            "properties": {
                "recipient_code": {"type": "string"},
                "amount": {"type": "integer", "description": "Amount in kobo"},
                "reason": {"type": "string"},
            },
            "required": ["recipient_code", "amount", "reason"],
        },
        handler=initiate_payout,
    ),
]
