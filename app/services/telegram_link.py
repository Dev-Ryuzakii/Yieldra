"""Link a web account to a Telegram chat with a short one-time code.

A signed-in user asks for a code on their dashboard and sends it to the bot
(typed, or through the bot's ``?start=`` deep link). The chat id then becomes
the user's ``phone``, so sponsorship updates reach them on Telegram and the bot
can answer questions about their own sponsorships.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.services.notify import WEB_PREFIX

PREFIX = "YLD-"
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L to misread
_LIFETIME = timedelta(minutes=30)
_CODE = re.compile(r"\bYLD-([A-Z0-9]{6})\b", re.IGNORECASE)


def is_linked(user: User) -> bool:
    return not user.phone.startswith(WEB_PREFIX)


def bot_url(code: str | None = None) -> str | None:
    name = settings.telegram_bot_username.strip().lstrip("@")
    if not name:
        return None
    return f"https://t.me/{name}" + (f"?start={code}" if code else "")


async def issue(session: AsyncSession, user: User) -> dict:
    """Give ``user`` a fresh code, replacing any earlier one."""
    code = PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(6))
    user.telegram_link_code = code
    user.telegram_link_expires_at = datetime.now(timezone.utc) + _LIFETIME
    await session.flush()
    return {"code": code, "expires_at": user.telegram_link_expires_at, "bot_url": bot_url(code)}


def find_code(text: str) -> str | None:
    match = _CODE.search(text or "")
    return f"{PREFIX}{match.group(1).upper()}" if match else None


async def link(session: AsyncSession, chat_id: str, code: str) -> dict:
    """Attach ``chat_id`` to the account that owns ``code``."""
    found = await session.execute(select(User).where(User.telegram_link_code == code))
    user = found.scalars().first()
    expires = user.telegram_link_expires_at if user else None
    if expires is not None and expires.tzinfo is None:  # SQLite drops the zone
        expires = expires.replace(tzinfo=timezone.utc)
    if user is None or expires is None or expires < datetime.now(timezone.utc):
        return {"status": "invalid_code"}

    if user.phone != chat_id:
        holder = (await session.execute(select(User).where(User.phone == chat_id))).scalars().first()
        if holder is not None:
            if holder.email:
                return {"status": "chat_taken"}
            # A chat-only account made by messaging the bot first: the web account
            # takes over the chat; the old record keeps its history under a new key.
            holder.phone = f"tg-moved:{holder.id}"
            await session.flush()
        user.phone = chat_id
    user.telegram_link_code = None
    user.telegram_link_expires_at = None
    await session.flush()
    return {"status": "linked", "user": user}
