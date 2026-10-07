"""Telegram webhook + shared inbound handler.

Inbound messages reach the system two ways, both funnelling into ``handle_inbound``:
  * long polling (app/telegram_poller.py) — default, no public URL needed
  * ``POST /webhook/telegram`` — for a real Telegram webhook on a public host

Yieldra users are keyed by ``User.phone``, which stores the Telegram chat id.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.advisory_agent import AdvisoryAgent
from app.agents.logistics_agent import LogisticsAgent
from app.agents.report_agent import ReportAgent
from app.agents.sponsorship_agent import SponsorshipAgent
from app.config import settings
from app.database import get_session
from app.models.farm import Farm
from app.models.harvest import Harvest, HarvestStatus
from app.models.user import Language, User, UserRole
from app.tools.telegram import get_file_url, send_text_message
from app.utils.logger import get_logger

log = get_logger("yieldra.webhook")
router = APIRouter(prefix="/webhook", tags=["webhook"])

_advisory = AdvisoryAgent()
_logistics = LogisticsAgent()
_report = ReportAgent()
_sponsorship = SponsorshipAgent()

# Keywords that indicate a logistics update rather than an advisory question.
_LOGISTICS_KEYWORDS = {"ready", "harvest", "truck", "pickup", "storage", "deliver", "delivered"}

# A photo captioned with one of these is milestone evidence for a sponsorship,
# not a crop-health question.
_EVIDENCE_KEYWORDS = {"proof", "milestone", "evidence"}

# Onboarding: map a role reply to a UserRole.
_ROLE_WORDS = {
    "farmer": UserRole.farmer,
    "investor": UserRole.investor,
    "sponsor": UserRole.sponsor,
    "buyer": UserRole.buyer,
}


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive a Telegram update (or the simplified simulator payload) and route it."""
    # If a webhook secret is configured, enforce it (real Telegram webhooks only).
    if (
        settings.telegram_live
        and not settings._is_placeholder(settings.telegram_webhook_secret)
        and x_telegram_bot_api_secret_token is not None
        and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        return {"status": "forbidden"}

    body = await request.json()
    parsed = await _parse_inbound(body)
    if parsed is None:
        return {"status": "ignored"}

    result = await handle_inbound(
        session,
        chat_id=parsed["chat_id"],
        text=parsed["text"],
        photo_url=parsed["photo_url"],
        name=parsed.get("name"),
    )
    return {"status": "ok", "result": result}


# -- shared inbound handler ------------------------------------------------
async def handle_inbound(
    session: AsyncSession,
    chat_id: str,
    text: str,
    photo_url: str | None,
    name: str | None = None,
) -> dict[str, Any]:
    """Find/onboard the user for ``chat_id`` then route to the right agent."""
    user = await _find_user(session, chat_id)

    if user is None:
        return await _onboard(session, chat_id, text, name)

    try:
        return await _route(session, user, text, photo_url)
    except Exception:  # noqa: BLE001 — a failing agent must not drop the message
        log.exception("routing failed for chat_id=%s role=%s", chat_id, user.role.value)
        await send_text_message(
            user.phone,
            "Yieldra is having trouble right now. Please try again in a moment.",
        )
        return {"status": "error", "agent": user.role.value}


async def _onboard(
    session: AsyncSession, chat_id: str, text: str, name: str | None
) -> dict[str, Any]:
    """Unknown chat: if they replied with a role, create the user; else prompt."""
    role = _ROLE_WORDS.get(text.strip().lower())
    if role is None:
        await send_text_message(
            chat_id,
            "Welcome to Yieldra! Reply with your role to get started: "
            "FARMER, SPONSOR, INVESTOR, or BUYER.",
        )
        return {"status": "onboarding", "chat_id": chat_id}

    user = User(
        name=name or "Telegram User",
        phone=chat_id,
        role=role,
        language_preference=Language.english,
    )
    session.add(user)
    await session.flush()
    await send_text_message(
        chat_id, f"You're registered as a {role.value}. How can Yieldra help today?"
    )
    log.info("onboarded user chat_id=%s role=%s", chat_id, role.value)
    return {"status": "registered", "role": role.value, "user_id": user.id}


# -- routing ---------------------------------------------------------------
async def _route(
    session: AsyncSession, user: User, text: str, photo_url: str | None
) -> dict[str, Any]:
    if user.role == UserRole.farmer:
        if photo_url is not None and _is_evidence(text):
            farm = await _farm_for_farmer(session, user.id)
            if farm is not None:
                result = await _sponsorship.submit_evidence(session, farm.id, photo_url)
                return {"agent": "sponsorship", **result}
        if photo_url is None and _is_logistics_update(text):
            harvest = await _active_harvest_for_farmer(session, user.id)
            if harvest is not None:
                result = await _logistics.handle_harvest_update(
                    session, harvest.id, text, photo_url
                )
                return {"agent": "logistics", "content": result.content}
        result = await _advisory.respond_to_farmer(session, user.id, text, photo_url)
        return {"agent": "advisory", **result}

    if user.role == UserRole.investor:
        # Portfolio Report Agent: generate + send the investor's current report.
        report = await _report.generate_investor_report(session, user.id)
        if report:
            await send_text_message(user.phone, report)
        return {"agent": "report", "content": report}

    if user.role == UserRole.sponsor:
        summary = await _sponsorship.status_text(session, user.id)
        await send_text_message(user.phone, summary)
        return {"agent": "sponsorship", "content": summary}

    if user.role == UserRole.buyer:
        await send_text_message(
            user.phone,
            "Yieldra: Got it. Our Offtake Agent will follow up on your offer.",
        )
        return {"agent": "buyer_ack"}

    await send_text_message(user.phone, "Yieldra received your message.")
    return {"agent": "none"}


def _is_evidence(text: str) -> bool:
    tokens = {t.strip(".,!?:").lower() for t in text.split()}
    return bool(tokens & _EVIDENCE_KEYWORDS)


async def _farm_for_farmer(session: AsyncSession, farmer_id: int) -> Farm | None:
    result = await session.execute(
        select(Farm).where(Farm.farmer_id == farmer_id).order_by(Farm.id).limit(1)
    )
    return result.scalars().first()


def _is_logistics_update(text: str) -> bool:
    tokens = {t.strip(".,!?").lower() for t in text.split()}
    return bool(tokens & _LOGISTICS_KEYWORDS)


async def _find_user(session: AsyncSession, chat_id: str) -> User | None:
    result = await session.execute(select(User).where(User.phone == chat_id))
    return result.scalars().first()


async def _active_harvest_for_farmer(session: AsyncSession, farmer_id: int) -> Harvest | None:
    result = await session.execute(
        select(Harvest)
        .join(Farm, Harvest.farm_id == Farm.id)
        .where(
            Farm.farmer_id == farmer_id,
            Harvest.status.in_([HarvestStatus.pending, HarvestStatus.ready, HarvestStatus.in_transit]),
        )
        .limit(1)
    )
    return result.scalars().first()


# -- payload parsing -------------------------------------------------------
async def _parse_inbound(body: dict[str, Any]) -> dict[str, Any] | None:
    """Extract {chat_id, text, photo_url, name} from a Telegram update or simulator payload.

    Supports the Telegram Bot API update shape and a simplified
    ``{"chat": ..., "text": ..., "image_url": ...}`` shape used by the simulator.
    """
    # Simplified simulator payload.
    if "chat" in body and ("text" in body or "image_url" in body):
        return {
            "chat_id": str(body["chat"]),
            "text": str(body.get("text", "")),
            "photo_url": body.get("image_url"),
            "name": body.get("name"),
        }

    # Telegram Bot API update.
    message = body.get("message") or body.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    text = message.get("text") or message.get("caption", "") or ""
    name = chat.get("first_name") or message.get("from", {}).get("first_name")

    photo_url = None
    photos = message.get("photo")
    if photos:
        # Telegram sends several sizes; the last is the largest.
        file_id = photos[-1].get("file_id")
        if file_id:
            photo_url = await get_file_url(file_id)

    return {"chat_id": str(chat_id), "text": text, "photo_url": photo_url, "name": name}
