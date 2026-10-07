"""The web pages: landing, a sponsor's tracking page, and the operator console.

They are static files that call the JSON API, so there is no build step.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.database import get_session
from app.services import naira_payments
from app.services.milestones import limits_for
from app.tools import tuago
from app.tools.tuago import TuagoError

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
router = APIRouter(tags=["pages"])


def _page(name: str) -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, name), headers={"Cache-Control": "no-cache"})


@router.get("/", include_in_schema=False)
async def landing() -> FileResponse:
    return _page("index.html")


@router.get("/s/{reference}", include_in_schema=False)
async def tracking(reference: str) -> FileResponse:
    return _page("track.html")


@router.get("/console", include_in_schema=False)
async def console() -> FileResponse:
    return _page("console.html")


def _mode(live: bool, real: str) -> str:
    return real if live else "mock"


@router.get("/meta")
async def meta() -> dict:
    """What this deployment is connected to, for the pages to label themselves honestly."""
    usd_low, usd_high = limits_for("USD")
    ngn_low, ngn_high = limits_for("NGN")
    return {
        "name": "Yieldra",
        "tagline": "Sponsor a farm. Pay as it grows.",
        "version": __version__,
        "docs": "/docs",
        "paypal": _mode(settings.paypal_live, settings.paypal_env),
        "tuago": _mode(settings.tuago_live, "test" if settings.tuago_test_mode else "live"),
        "model_ready": settings.llm_live,
        "operator_key_required": not (
            settings.is_development and settings._is_placeholder(settings.secret_key)
        ),
        "usd_ngn_rate": settings.usd_ngn_rate,
        "limits": {
            "paypal": {"currency": "USD", "min": usd_low / 100, "max": usd_high / 100},
            "tuago": {"currency": "NGN", "min": ngn_low / 100, "max": ngn_high / 100},
        },
    }


@router.get("/pay/tuago/mock", include_in_schema=False)
async def mock_tuago_checkout(
    session_id: str = Query(..., alias="session"),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Stands in for Tuago's checkout page while no Tuago key is configured."""
    if settings.tuago_live:
        raise HTTPException(status_code=404, detail="not available")
    from app.routers.sponsorships import agent

    try:
        await tuago.simulate_payment(session_id, "success")
    except TuagoError as exc:
        raise HTTPException(status_code=404, detail="unknown checkout") from exc
    await naira_payments.settle(session, agent, session_id=session_id)
    return RedirectResponse(tuago.mock_redirect_url(session_id) or "/", status_code=303)
