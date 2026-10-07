"""Sponsorship endpoints — PayPal-funded, milestone-released farm sponsorship."""

from __future__ import annotations

import hmac
from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sponsorship_agent import SponsorshipAgent
from app.config import settings
from app.database import get_session
from app.schemas.sponsorship import (
    EvidenceSubmit,
    MilestoneReview,
    SponsorshipCreate,
    SponsorshipRead,
    SponsorshipStarted,
)
from app.tools.paypal import PayPalError
from app.utils.money import format_usd, usd_to_cents

router = APIRouter(prefix="/sponsorships", tags=["sponsorships"])
_agent = SponsorshipAgent()


def require_operator(x_admin_key: str | None = Header(default=None)) -> None:
    """Guard for endpoints that can move money or stand in for the farmer.

    Callers must send ``X-Admin-Key: <SECRET_KEY>``. While SECRET_KEY is still the
    placeholder in a development environment the check is skipped, so local runs work
    out of the box; set a real SECRET_KEY before exposing the API.
    """
    if settings.is_development and settings._is_placeholder(settings.secret_key):
        return
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.secret_key):
        raise HTTPException(status_code=401, detail="operator key required")


def _raise_for(result: dict) -> None:
    if result.get("status") == "error":
        message = result.get("message", "error")
        raise HTTPException(status_code=404 if "not found" in message else 409, detail=message)


@router.post("", response_model=SponsorshipStarted, status_code=201)
async def start_sponsorship(
    payload: SponsorshipCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    """Create a sponsorship and return the PayPal approval link for its first tranche."""
    try:
        result = await _agent.start(
            session,
            sponsor_id=payload.sponsor_id,
            farm_id=payload.farm_id,
            total_minor=usd_to_cents(payload.total_usd),
        )
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=f"PayPal: {exc.issue or exc}") from exc
    if result.get("status") == "invalid_amount":
        raise HTTPException(status_code=422, detail=result["message"])
    _raise_for(result)
    return result


@router.get("", response_model=list[SponsorshipRead])
async def list_sponsorships(
    sponsor_id: int | None = Query(None),
    farm_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    return await _agent.list_all(session, sponsor_id=sponsor_id, farm_id=farm_id)


@router.get("/paypal/return", response_class=HTMLResponse)
async def paypal_return(
    token: str = Query(..., description="PayPal order id"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """PayPal sends the sponsor here after they approve. Captures the first tranche."""
    result = await _agent.confirm_payment(session, token)
    status = result.get("status")
    if status in ("active", "already_confirmed"):
        s = result["sponsorship"]
        nxt = s["next_milestone"]
        rows = "".join(
            f"<li><b>{escape(m['title'])}</b> — {escape(m['amount'])} "
            f"<span class='s'>{escape(m['status'].replace('_', ' '))}</span></li>"
            for m in s["milestones"]
        )
        body = (
            f"<h1>Thank you. Your sponsorship is active.</h1>"
            f"<p>{escape(format_usd(s['paid_minor']))} of {escape(s['total'])} is paid for "
            f"<b>{escape(s['farm_name'] or 'the farm')}</b>.</p>"
            f"<p>The rest is charged in stages, each only after the farmer's photo passes "
            f"verification.</p><ol>{rows}</ol>"
            + (f"<p>Next stage: {escape(nxt['title'])}.</p>" if nxt else "")
        )
        return HTMLResponse(_page("Sponsorship active", body))
    detail = result.get("message") or result.get("issue") or "The payment was not completed."
    return HTMLResponse(
        _page("Payment not completed", f"<h1>Payment not completed</h1><p>{escape(str(detail))}</p>"),
        status_code=402 if status == "payment_failed" else 404,
    )


@router.get("/paypal/cancel", response_class=HTMLResponse)
async def paypal_cancel(
    token: str = Query(..., description="PayPal order id"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """PayPal sends the sponsor here if they back out before approving."""
    await _agent.abandon(session, token)
    return HTMLResponse(
        _page("Sponsorship not started", "<h1>No payment was taken.</h1><p>You can start again any time.</p>")
    )


@router.get("/{sponsorship_id}", response_model=SponsorshipRead)
async def get_sponsorship(
    sponsorship_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await _agent.get(session, sponsorship_id)
    if result is None:
        raise HTTPException(status_code=404, detail="sponsorship not found")
    return result


@router.post("/{sponsorship_id}/cancel")
async def cancel_sponsorship(
    sponsorship_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Stop a sponsorship and delete the sponsor's saved PayPal account."""
    result = await _agent.cancel(session, sponsorship_id)
    _raise_for(result)
    return result


@router.post("/farms/{farm_id}/evidence")
async def submit_evidence(
    farm_id: int,
    payload: EvidenceSubmit,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Submit a farm photo on the farmer's behalf (farmers normally send it by chat)."""
    result = await _agent.submit_evidence(session, farm_id, payload.photo_url)
    _raise_for(result)
    return result


@router.post("/milestones/{milestone_id}/review")
async def review_milestone(
    milestone_id: int,
    payload: MilestoneReview,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Approve or reject a milestone that verification held for human review."""
    result = await _agent.review_milestone(session, milestone_id, payload.approve)
    _raise_for(result)
    return result


@router.post("/milestones/{milestone_id}/retry-payment")
async def retry_payment(
    milestone_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Retry the PayPal charge for a verified milestone whose charge failed."""
    result = await _agent.retry_payment(session, milestone_id)
    _raise_for(result)
    return result


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} · Yieldra</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:36rem;margin:3rem auto;"
        "padding:0 1rem;line-height:1.5;color:#1b2a1f}h1{font-size:1.4rem}"
        "li{margin:.4rem 0}.s{color:#5b6b5f;font-size:.9em}</style></head>"
        f"<body>{body}</body></html>"
    )
