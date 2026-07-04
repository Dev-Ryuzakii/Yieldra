"""Telegram long-polling loop.

Calls ``getUpdates`` with a long timeout and feeds each inbound message into the
shared ``handle_inbound`` routing handler. This needs no public URL, so it is the
default way to receive messages in development and on a single-instance deploy.

Started from the FastAPI lifespan (app/main.py) when ``telegram_use_polling`` is on
and a real bot token is configured.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings
from app.database import async_session_factory
from app.routers.webhooks import _parse_inbound, handle_inbound
from app.utils.logger import get_logger

log = get_logger("yieldra.telegram.poller")

_LONG_POLL_TIMEOUT = 30  # seconds Telegram holds the request open


async def _process_update(update: dict[str, Any]) -> None:
    parsed = await _parse_inbound(update)
    if parsed is None:
        return
    async with async_session_factory() as session:
        try:
            await handle_inbound(
                session,
                chat_id=parsed["chat_id"],
                text=parsed["text"],
                photo_url=parsed["photo_url"],
                name=parsed.get("name"),
            )
            await session.commit()
        except Exception:  # noqa: BLE001 — one bad update must not kill the loop
            await session.rollback()
            log.exception("failed to process telegram update")


async def poll_forever() -> None:
    """Continuously poll Telegram for updates until cancelled."""
    if not settings.telegram_live:
        log.info("telegram poller disabled (no real bot token)")
        return

    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    offset: int | None = None
    log.info("telegram poller started")
    async with httpx.AsyncClient(timeout=_LONG_POLL_TIMEOUT + 10) as client:
        while True:
            try:
                params: dict[str, Any] = {"timeout": _LONG_POLL_TIMEOUT}
                if offset is not None:
                    params["offset"] = offset
                resp = await client.get(f"{base}/getUpdates", params=params)
                resp.raise_for_status()
                updates = resp.json().get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    await _process_update(update)
            except asyncio.CancelledError:
                log.info("telegram poller stopping")
                raise
            except Exception:  # noqa: BLE001 — network blips: back off and retry
                log.exception("telegram getUpdates failed; retrying in 3s")
                await asyncio.sleep(3)
