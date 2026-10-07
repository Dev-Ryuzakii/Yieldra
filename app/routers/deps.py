"""Shared FastAPI dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.config import settings


def require_operator(x_admin_key: str | None = Header(default=None)) -> None:
    """Guard for endpoints that can move money, stand in for a farmer, or list everyone.

    Callers must send ``X-Admin-Key: <SECRET_KEY>``. While SECRET_KEY is still the
    placeholder in a development environment the check is skipped, so local runs work
    out of the box; set a real SECRET_KEY before exposing the API.
    """
    if settings.is_development and settings._is_placeholder(settings.secret_key):
        return
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.secret_key):
        raise HTTPException(status_code=401, detail="operator key required")
