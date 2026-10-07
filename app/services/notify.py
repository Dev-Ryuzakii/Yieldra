"""Chat notifications that can never break the flow that sends them.

A failed or slow message must not undo a payment or a database commit, so
``notify`` swallows every error. System messages are written in English and
translated into the user's preferred language when a model key is configured.
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.config import settings
from app.models.user import User
from app.tools.telegram import send_text_message
from app.utils.logger import get_logger

log = get_logger("yieldra.notify")

# Sponsors who signed up on the web have no chat id; their updates show on their page.
WEB_PREFIX = "web:"

_TRANSLATOR_PROMPT = (
    "You translate short service messages for Nigerian farmers and their sponsors. "
    "Keep names, amounts, bank details, links and capitalised command words exactly as "
    "written. Reply with the translation only. Respond in the user's language."
)


def has_chat(user: User | None) -> bool:
    return user is not None and not user.phone.startswith(WEB_PREFIX)


async def localise(text: str, language: str) -> str:
    """Translate ``text`` into ``language``; return it unchanged on any failure."""
    if language == "english" or not settings.llm_live:
        return text
    try:
        agent = BaseAgent(model=settings.model_advisory, system_prompt=_TRANSLATOR_PROMPT)
        translated = await agent.run(
            [{"role": "user", "content": f"Translate into {language}:\n\n{text}"}]
        )
    except Exception:  # noqa: BLE001
        return text
    return translated.strip() or text


async def notify(user: User | None, text: str, translate: bool = True) -> None:
    """Send a chat message. Never raises."""
    if not has_chat(user):
        return
    try:
        if translate:
            text = await localise(text, user.language_preference.value)
        await send_text_message(user.phone, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("notify failed user=%s: %s", user.id, type(exc).__name__)
