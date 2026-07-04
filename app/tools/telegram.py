"""Telegram Bot API send tools.

Mocked until a real TELEGRAM_BOT_TOKEN is configured (see settings.telegram_live);
real Bot API calls are made once it is present.

The ``chat`` argument is a Telegram chat id (stored on ``User.phone`` for Yieldra
users). The public function names match the generic messaging interface the agents
call, so each agent only needs to import from here.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.agents.base import Tool
from app.config import settings
from app.utils.logger import get_logger

log = get_logger("yieldra.tools.telegram")


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


async def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.telegram_live:
        log.info("[MOCK telegram] %s -> %s | %s", method, payload.get("chat_id"), payload)
        return {"mock": True, "ok": True, "method": method, "chat_id": payload.get("chat_id")}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_api_url(method), json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_text_message(phone: str, message: str) -> dict[str, Any]:
    """Send a plain text Telegram message. ``phone`` is the chat id."""
    return await _call(
        "sendMessage",
        {"chat_id": phone, "text": message[:4096]},
    )


async def send_image_message(phone: str, image_url: str, caption: str = "") -> dict[str, Any]:
    """Send a photo (by URL) with an optional caption. ``phone`` is the chat id."""
    return await _call(
        "sendPhoto",
        {"chat_id": phone, "photo": image_url, "caption": caption[:1024]},
    )


async def get_file_url(file_id: str) -> str | None:
    """Resolve a Telegram ``file_id`` to a downloadable URL (for the Vision agent).

    Returns ``None`` in mock mode or if the lookup fails.
    """
    if not settings.telegram_live:
        log.info("[MOCK telegram] get_file_url file_id=%s", file_id)
        return None
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(_api_url("getFile"), params={"file_id": file_id})
        resp.raise_for_status()
        file_path = resp.json().get("result", {}).get("file_path")
    if not file_path:
        return None
    return f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"


# -- Tool specs for agent tool-calling ------------------------------------
TELEGRAM_TOOLS: list[Tool] = [
    Tool(
        name="send_text_message",
        description="Send a plain text Telegram message to a chat id.",
        parameters={
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Recipient Telegram chat id"},
                "message": {"type": "string", "description": "Message body, max 3 sentences"},
            },
            "required": ["phone", "message"],
        },
        handler=send_text_message,
    ),
    Tool(
        name="send_image_message",
        description="Send an image (by URL) with a caption over Telegram.",
        parameters={
            "type": "object",
            "properties": {
                "phone": {"type": "string"},
                "image_url": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["phone", "image_url"],
        },
        handler=send_image_message,
    ),
]
