"""Sponsorship endpoints — milestone-released farm sponsorship over PayPal."""

from __future__ import annotations

import uuid
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sponsorship_agent import SponsorshipAgent
from app.config import settings
from app.database import get_session
from app.models.farm import Farm
from app.models.payout import PayoutAccount
from app.models.sponsorship import (
    MilestoneStatus,
    Rail,
    Sponsorship,
    SponsorshipMilestone,
    SponsorshipStatus,
)
from app.models.user import User, UserRole
from app.routers.deps import require_operator
from app.schemas.sponsorship import (
    EvidenceSubmit,
    MilestoneReview,
    SponsorCheckout,
    SponsorshipCreate,
    SponsorshipRead,
    SponsorshipStarted,
)
from app.services import evidence_store
from app.services.notify import WEB_PREFIX
from app.tools.paypal import PayPalError

router = APIRouter(prefix="/sponsorships", tags=["sponsorships"])
agent = SponsorshipAgent()


def _raise_for(result: dict) -> None:
    status = result.get("status")
    if status in ("invalid_amount", "invalid_rail"):
        raise HTTPException(status_code=422, detail=result["message"])
    if status == "farmer_not_ready":
        raise HTTPException(status_code=409, detail=result["message"])
    if status == "error":
        message = result.get("message", "error")
        raise HTTPException(status_code=404 if "not found" in message else 409, detail=message)


async def _start(session: AsyncSession, sponsor_id: int, farm_id: int, total_minor: int, rail: str) -> dict:
    try:
        result = await agent.start(
            session, sponsor_id=sponsor_id, farm_id=farm_id, total_minor=total_minor, rail=rail
        )
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=f"PayPal: {exc.issue or exc}") from exc
    _raise_for(result)
    return result


# -- starting --------------------------------------------------------------
@router.post("", response_model=SponsorshipStarted, status_code=201)
async def start_sponsorship(
    payload: SponsorshipCreate, session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Start a PayPal sponsorship for an existing user."""
    if payload.total_usd is None:
        raise HTTPException(status_code=422, detail="give total_usd")
    return await _start(session, payload.sponsor_id, payload.farm_id, int(round(payload.total_usd * 100)), Rail.paypal.value)


@router.post("/checkout", response_model=SponsorshipStarted, status_code=201)
async def sponsor_checkout(
    payload: SponsorCheckout, request: Request, response: Response,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Start a sponsorship from the web for the signed-in sponsor."""
    email = payload.email.strip().lower()
    identity = None
    if settings.afribase_ready or request.cookies.get("yieldra_session"):
        from app.routers.auth import current_identity
        identity = await current_identity(request, response)
        if str(identity["email"]).lower() != email:
            raise HTTPException(status_code=403, detail="Use your signed-in email to sponsor a farm")
    found = await session.execute(select(User).where(User.email == email))
    sponsor = found.scalars().first()
    if sponsor is not None and sponsor.afribase_uid:
        if identity is None:
            from app.routers.auth import current_identity
            identity = await current_identity(request, response)
        if str(identity["id"]) != sponsor.afribase_uid or str(identity["email"]).lower() != email:
            raise HTTPException(status_code=403, detail="Sign in with this email to sponsor a farm")
    if sponsor is None:
        sponsor = User(
            name=payload.name.strip(),
            # Web sponsors have no chat id; this placeholder keeps the column unique.
            phone=f"{WEB_PREFIX}{uuid.uuid4().hex[:16]}",
            email=email,
            afribase_uid=str(identity["id"]) if identity else None,
            role=UserRole.sponsor,
        )
        session.add(sponsor)
        await session.flush()
    return await _start(
        session, sponsor.id, payload.farm_id, int(round(payload.amount * 100)), payload.rail
    )


# -- public reads ----------------------------------------------------------
@router.get("/farms/overview")
async def farms_overview(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Farms open to sponsors, with how far each has come."""
    farms = list((await session.execute(select(Farm).order_by(Farm.id))).scalars())
    out: list[dict] = []
    for farm in farms:
        farmer = await session.get(User, farm.farmer_id)
        ready = await session.execute(
            select(PayoutAccount.id).where(PayoutAccount.user_id == farm.farmer_id)
        )
        sponsors = await session.execute(
            select(func.count(Sponsorship.id)).where(
                Sponsorship.farm_id == farm.id,
                Sponsorship.status.in_([SponsorshipStatus.active, SponsorshipStatus.completed]),
            )
        )
        # The most recent verified stage, and its photo, across this farm's sponsorships.
        latest = await session.execute(
            select(SponsorshipMilestone)
            .join(Sponsorship, SponsorshipMilestone.sponsorship_id == Sponsorship.id)
            .where(
                Sponsorship.farm_id == farm.id,
                SponsorshipMilestone.status.in_(
                    [MilestoneStatus.paid, MilestoneStatus.awaiting_payment]
                ),
                SponsorshipMilestone.evidence_photo.is_not(None),
            )
            .order_by(SponsorshipMilestone.verified_at.desc())
            .limit(1)
        )
        stage = latest.scalars().first()
        out.append(
            {
                "id": farm.id,
                "name": farm.name,
                "crop_type": farm.crop_type,
                "location": farm.location,
                "cover_image_url": farm.cover_image_url,
                "cover_image_alt": farm.cover_image_alt,
                "cover_image_credit": farm.cover_image_credit,
                "cover_image_source": farm.cover_image_source,
                "cover_image_license": farm.cover_image_license,
                "farmer_first_name": farmer.name.split()[0] if farmer else None,
                "naira_ready": ready.first() is not None,
                "sponsors": sponsors.scalar_one(),
                "latest_stage": stage.title if stage else None,
                "latest_photo_url": evidence_store.url_for(stage.evidence_photo) if stage else None,
            }
        )
    return out


@router.get("", response_model=list[SponsorshipRead])
async def list_sponsorships(
    sponsor_id: int | None = Query(None),
    farm_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> list[dict]:
    return await agent.list_all(session, sponsor_id=sponsor_id, farm_id=farm_id)


# -- the sponsor's own page -------------------------------------------------
async def _by_reference(session: AsyncSession, reference: str) -> Sponsorship:
    sponsorship = await agent.by_reference(session, reference)
    if sponsorship is None:
        raise HTTPException(status_code=404, detail="sponsorship not found")
    return sponsorship


async def authorized_reference(session: AsyncSession, reference: str,
                               request: Request, response: Response) -> Sponsorship:
    sponsorship = await _by_reference(session, reference)
    if not settings.afribase_ready:
        return sponsorship
    from app.routers.auth import _profile, current_identity
    identity = await current_identity(request, response)
    profile = await _profile(identity, session)
    if profile["role"] != "operator" and sponsorship.sponsor_id != profile["id"]:
        raise HTTPException(status_code=403, detail="This sponsorship belongs to another account")
    return sponsorship


@router.get("/ref/{reference}", response_model=SponsorshipRead)
async def sponsorship_by_reference(
    reference: str, request: Request, response: Response,
    session: AsyncSession = Depends(get_session)
) -> dict:
    sponsorship = await authorized_reference(session, reference, request, response)
    return await agent.get(session, sponsorship.id)


@router.post("/ref/{reference}/refresh", response_model=SponsorshipRead)
async def refresh_by_reference(
    reference: str, request: Request, response: Response,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """Reload the current sponsorship state."""
    sponsorship = await authorized_reference(session, reference, request, response)
    return await agent.get(session, sponsorship.id)


@router.post("/ref/{reference}/cancel", response_model=SponsorshipRead)
async def cancel_by_reference(
    reference: str, request: Request, response: Response,
    session: AsyncSession = Depends(get_session)
) -> dict:
    """The sponsor stops their own sponsorship. Paid tranches stay paid."""
    sponsorship = await authorized_reference(session, reference, request, response)
    _raise_for(await agent.cancel(session, sponsorship.id))
    return await agent.get(session, sponsorship.id)


# -- provider returns ------------------------------------------------------
@router.get("/paypal/return")
async def paypal_return(
    token: str = Query(..., description="PayPal order id"),
    session: AsyncSession = Depends(get_session),
):
    """PayPal sends the sponsor here after they approve. Captures the first tranche."""
    result = await agent.confirm_payment(session, token)
    status = result.get("status")
    if status in ("active", "already_confirmed"):
        return RedirectResponse(f"/s/{result['sponsorship']['reference']}?paid=1", status_code=303)
    detail = result.get("message") or result.get("issue") or "The payment was not completed."
    return HTMLResponse(
        _page("Payment not completed", f"<h1>Payment not completed</h1><p>{escape(str(detail))}</p>"),
        status_code=402 if status == "payment_failed" else 404,
    )


@router.get("/paypal/cancel")
async def paypal_cancel(
    token: str = Query(..., description="PayPal order id"),
    session: AsyncSession = Depends(get_session),
):
    """PayPal sends the sponsor here if they back out before approving."""
    result = await agent.abandon(session, token)
    if result.get("reference"):
        return RedirectResponse(f"/s/{result['reference']}", status_code=303)
    return HTMLResponse(
        _page("Sponsorship not started", "<h1>No payment was taken.</h1>"), status_code=404
    )


# -- operator actions ------------------------------------------------------
@router.get("/{sponsorship_id}", response_model=SponsorshipRead)
async def get_sponsorship(
    sponsorship_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    result = await agent.get(session, sponsorship_id)
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
    result = await agent.cancel(session, sponsorship_id)
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
    result = await agent.submit_evidence(session, farm_id, payload.photo_url)
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
    result = await agent.review_milestone(session, milestone_id, payload.approve)
    _raise_for(result)
    return result


@router.post("/milestones/{milestone_id}/retry-payment")
async def retry_payment(
    milestone_id: int,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_operator),
) -> dict:
    """Retry collecting a verified milestone whose charge or payment request failed."""
    result = await agent.retry_payment(session, milestone_id)
    _raise_for(result)
    return result


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} · Yieldra</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:36rem;margin:3rem auto;"
        "padding:0 1rem;line-height:1.5;color:#1b2a1f}h1{font-size:1.4rem}</style></head>"
        f"<body>{body}<p><a href='/'>Back to Yieldra</a></p></body></html>"
    )
