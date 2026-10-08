"""Serve the built React frontend at its three public routes."""

from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.database import get_session
from app.services.milestones import limits_for

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
DIST_DIR = os.path.join(WEB_DIR, "dist")
router = APIRouter(tags=["pages"])


def _page(*, noindex: bool = False) -> FileResponse:
    headers = {"Cache-Control": "no-cache"}
    if noindex:
        headers["X-Robots-Tag"] = "noindex"
    return FileResponse(os.path.join(DIST_DIR, "index.html"), headers=headers)


@router.get("/", include_in_schema=False)
async def landing() -> FileResponse:
    return _page()


def _login_redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"/auth?next={quote(path, safe='')}", status_code=303)


def _forward_session(source: Response, target: Response) -> Response:
    for value in source.headers.getlist("set-cookie"):
        target.headers.append("set-cookie", value)
    return target


@router.get("/s/{reference}", include_in_schema=False)
async def tracking(reference: str, request: Request,
                   session: AsyncSession = Depends(get_session)) -> Response:
    if not settings.afribase_ready:
        return _page(noindex=True)
    from app.routers.sponsorships import authorized_reference
    response = Response()
    try:
        await authorized_reference(session, reference, request, response)
    except HTTPException as exc:
        if exc.status_code == 401:
            return _login_redirect(str(request.url.path))
        raise
    return _forward_session(response, _page(noindex=True))


@router.get("/console", include_in_schema=False)
async def console(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    if not settings.afribase_ready:
        return _page(noindex=True)
    from app.routers.auth import _profile, current_identity
    response = Response()
    try:
        identity = await current_identity(request, response)
    except HTTPException as exc:
        if exc.status_code == 401:
            return _login_redirect("/console")
        raise
    if (await _profile(identity, session))["role"] != "operator":
        raise HTTPException(403, "operator access required")
    return _forward_session(response, _page(noindex=True))


@router.get("/auth", include_in_schema=False)
async def auth_page() -> FileResponse:
    return _page(noindex=True)


@router.get("/dashboard", include_in_schema=False)
async def dashboard_page(request: Request) -> Response:
    if not settings.afribase_ready:
        return _page(noindex=True)
    from app.routers.auth import current_identity
    response = Response()
    try:
        await current_identity(request, response)
    except HTTPException as exc:
        if exc.status_code == 401:
            return _login_redirect("/dashboard")
        raise
    return _forward_session(response, _page(noindex=True))


def _mode(live: bool, real: str) -> str:
    return real if live else "mock"


@router.get("/meta")
async def meta() -> dict:
    """What this deployment is connected to, for the pages to label themselves honestly."""
    usd_low, usd_high = limits_for("USD")
    return {
        "name": "Yieldra",
        "tagline": "Sponsor a farm. Pay as it grows.",
        "version": __version__,
        "docs": "/docs",
        "paypal": _mode(settings.paypal_live, settings.paypal_env),
        "farmer_payouts": "pending_manual",
        "model_ready": settings.llm_live,
        "auth_ready": settings.afribase_ready,
        "operator_key_required": not (
            settings.is_development and settings._is_placeholder(settings.secret_key)
        ),
        "usd_ngn_rate": settings.usd_ngn_rate,
        "limits": {
            "paypal": {"currency": "USD", "min": usd_low / 100, "max": usd_high / 100},
        },
    }
