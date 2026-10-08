"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, Response, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session

from app.config import settings


async def require_operator(request: Request, response: Response,
                           x_admin_key: str | None = Header(default=None),
                           db: AsyncSession = Depends(get_session)) -> None:
    """Guard for endpoints that can move money, stand in for a farmer, or list everyone.

    Callers must send ``X-Admin-Key: <SECRET_KEY>``. While SECRET_KEY is still the
    placeholder in a development environment the check is skipped, so local runs work
    out of the box; set a real SECRET_KEY before exposing the API.
    """
    if settings.is_development and settings._is_placeholder(settings.secret_key):
        return
    if x_admin_key and hmac.compare_digest(x_admin_key, settings.secret_key):
        return
    from app.routers.auth import _profile, current_identity
    try:
        identity = await current_identity(request, response)
        profile = await _profile(identity, db)
    except HTTPException:
        raise HTTPException(status_code=401, detail="operator sign-in required") from None
    if profile["role"] != "operator":
        raise HTTPException(status_code=403, detail="operator access required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="cross-origin request refused")
