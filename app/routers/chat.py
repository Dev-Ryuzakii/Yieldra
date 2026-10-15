"""Website chat: sponsors ask the AI assistant about farms and how sponsoring works."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sponsor_chat_agent import SponsorChatAgent
from app.config import settings
from app.database import get_session
from app.utils.logger import get_logger

log = get_logger("yieldra.chat")
router = APIRouter(prefix="/chat", tags=["chat"])
_agent = SponsorChatAgent()

# Each visitor may send this many messages per window; the chat is public and
# every message costs a model call.
_LIMIT, _WINDOW_SECONDS = 15, 300
_recent: dict[str, deque[float]] = defaultdict(deque)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


def _allow(visitor: str) -> bool:
    now = time.monotonic()
    times = _recent[visitor]
    while times and now - times[0] > _WINDOW_SECONDS:
        times.popleft()
    if len(times) >= _LIMIT:
        return False
    times.append(now)
    return True


@router.post("")
async def chat(payload: ChatRequest, request: Request,
               session: AsyncSession = Depends(get_session)) -> dict:
    if payload.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="the last message must be from the user")
    if not settings.llm_live:
        raise HTTPException(status_code=503, detail="The assistant is offline right now.")
    visitor = (request.headers.get("cf-connecting-ip")
               or (request.client.host if request.client else "unknown"))
    if not _allow(visitor):
        raise HTTPException(status_code=429, detail="Too many messages. Please wait a few minutes.")
    try:
        reply = await _agent.reply(session, [m.model_dump() for m in payload.messages])
    except Exception:  # noqa: BLE001 — a model failure should read as a chat error, not a 500
        log.exception("sponsor chat failed")
        raise HTTPException(status_code=502, detail="The assistant could not answer. Try again.")
    return {"reply": reply.strip()}
