"""Operator assignment of an existing farm to a registered farmer.

``Farm.farmer_id`` is the only ownership link. This module changes it with a
compare-and-set update, so two operators working from the same screen cannot
silently overwrite each other, and writes one audit row per real change.

Nothing here touches sponsorships, PayPal, farmer payout records or photo evidence.
Payout rows keep the farmer they were recorded for. A reassignment is refused while
the farm has photo evidence or a tranche in flight, because that work belongs to the
current farmer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.farm import Farm
from app.models.farm_assignment import FarmAssignmentEvent
from app.models.photo_check import PhotoCheck
from app.models.sponsorship import (
    MilestoneStatus,
    Sponsorship,
    SponsorshipMilestone,
    SponsorshipStatus,
)
from app.models.user import User, UserRole
from app.utils.logger import get_logger

log = get_logger("yieldra.farm_assignment")

REASON_MIN = 10
REASON_MAX = 500
_IN_FLIGHT_MILESTONES = (
    MilestoneStatus.needs_review,
    MilestoneStatus.awaiting_payment,
    MilestoneStatus.payment_failed,
)
_IN_FLIGHT_CHECKS = ("pending", "needs_review")
NO_FARMERS_HINT = (
    "No registered farmers yet. A farmer must create a farmer account and sign in to "
    "Yieldra once; their profile then appears here."
)


class AssignmentError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _operator_emails() -> set[str]:
    return {item.strip().lower() for item in settings.operator_emails.split(",") if item.strip()}


def is_eligible_farmer(user: User | None) -> bool:
    """A farmer row that has signed in to the web app and is not an operator login."""
    if user is None or user.role != UserRole.farmer or not user.afribase_uid:
        return False
    return (user.email or "").strip().lower() not in _operator_emails()


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:1]}•••@{domain}"


def _person(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.name, "registered": is_eligible_farmer(user)}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def photo_readiness(session: AsyncSession, farm_id: int) -> dict[str, Any]:
    """Whether the farm's farmer can send a milestone photo now, and why not if not.

    Read only. Uses the same rule as photo submission: an active sponsorship with a
    stage waiting for evidence.
    """
    open_row = (
        await session.execute(
            select(SponsorshipMilestone)
            .join(Sponsorship, SponsorshipMilestone.sponsorship_id == Sponsorship.id)
            .where(
                Sponsorship.farm_id == farm_id,
                Sponsorship.status == SponsorshipStatus.active,
                SponsorshipMilestone.status == MilestoneStatus.awaiting_evidence,
                SponsorshipMilestone.evidence_required.is_not(None),
            )
            .order_by(SponsorshipMilestone.sequence, SponsorshipMilestone.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if open_row is not None:
        return {
            "open_stage": {"milestone_id": open_row.id, "title": open_row.title},
            "note": f"Waiting for a photo of: {open_row.title}.",
        }
    statuses = set(
        (
            await session.scalars(select(Sponsorship.status).where(Sponsorship.farm_id == farm_id))
        ).all()
    )
    if not statuses:
        note = "No sponsorship yet, so no milestone photo is due."
    elif SponsorshipStatus.active in statuses:
        note = "No stage is waiting for a photo right now."
    elif SponsorshipStatus.pending_approval in statuses:
        note = "A sponsorship is waiting for the sponsor's PayPal approval. Photos open after that."
    else:
        note = "No active sponsorship, so no milestone photo is due."
    return {"open_stage": None, "note": note}


async def blockers(session: AsyncSession, farm_id: int) -> list[str]:
    """Reasons a reassignment is unsafe right now. Empty means it may proceed."""
    reasons: list[str] = []
    checks = await session.scalar(
        select(func.count(PhotoCheck.id)).where(
            PhotoCheck.farm_id == farm_id,
            PhotoCheck.status.in_(_IN_FLIGHT_CHECKS),
            PhotoCheck.superseded.is_(False),
        )
    )
    if checks:
        reasons.append(
            "A milestone photo from the current farmer is still being checked. "
            "Finish its review first."
        )
    tranches = await session.scalar(
        select(func.count(SponsorshipMilestone.id))
        .join(Sponsorship, SponsorshipMilestone.sponsorship_id == Sponsorship.id)
        .where(
            Sponsorship.farm_id == farm_id,
            SponsorshipMilestone.status.in_(_IN_FLIGHT_MILESTONES),
        )
    )
    if tranches:
        reasons.append(
            "A tranche on this farm is under review or waiting for payment. "
            "Settle it before moving the farm."
        )
    return reasons


async def _history(session: AsyncSession, farm_id: int, limit: int = 5) -> list[dict[str, Any]]:
    events = (
        await session.scalars(
            select(FarmAssignmentEvent)
            .where(FarmAssignmentEvent.farm_id == farm_id)
            .order_by(FarmAssignmentEvent.id.desc())
            .limit(limit)
        )
    ).all()
    ids = {e.previous_farmer_id for e in events} | {e.new_farmer_id for e in events} | {
        e.actor_id for e in events
    }
    ids.discard(None)
    names = {}
    if ids:
        names = {
            u.id: u.name for u in (await session.scalars(select(User).where(User.id.in_(ids)))).all()
        }
    return [serialise_event(e, names) for e in events]


def serialise_event(event: FarmAssignmentEvent, names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": event.id,
        "farm_id": event.farm_id,
        "previous_farmer": names.get(event.previous_farmer_id) if event.previous_farmer_id else None,
        "previous_farmer_id": event.previous_farmer_id,
        "new_farmer": names.get(event.new_farmer_id),
        "new_farmer_id": event.new_farmer_id,
        "actor": names.get(event.actor_id) if event.actor_id else None,
        "actor_via": event.actor_via,
        "reason": event.reason,
        "created_at": _iso(event.created_at),
    }


async def farm_summary(session: AsyncSession, farm: Farm, owner: User | None) -> dict[str, Any]:
    return {
        "id": farm.id,
        "name": farm.name,
        "crop_type": farm.crop_type,
        "location": farm.location,
        "status": farm.status.value,
        "farmer": _person(owner),
        "photo": await photo_readiness(session, farm.id),
        "blockers": await blockers(session, farm.id),
        "history": await _history(session, farm.id),
    }


async def overview(session: AsyncSession) -> dict[str, Any]:
    """Every farm with its current owner, and every farmer an operator may choose."""
    farms = (await session.scalars(select(Farm).order_by(Farm.name, Farm.id))).all()
    users = {
        u.id: u
        for u in (
            await session.scalars(select(User).where(User.id.in_({f.farmer_id for f in farms})))
        ).all()
    } if farms else {}
    candidates = (
        await session.scalars(
            select(User)
            .where(User.role == UserRole.farmer, User.afribase_uid.is_not(None))
            .order_by(User.name, User.id)
        )
    ).all()
    owned: dict[int, int] = {}
    for farm in farms:
        owned[farm.farmer_id] = owned.get(farm.farmer_id, 0) + 1
    farmers = [
        {
            "id": u.id,
            "name": u.name,
            "email_hint": _mask_email(u.email),
            "farm_count": owned.get(u.id, 0),
        }
        for u in candidates
        if is_eligible_farmer(u)
    ]
    return {
        "farms": [await farm_summary(session, f, users.get(f.farmer_id)) for f in farms],
        "farmers": farmers,
        "farmers_hint": None if farmers else NO_FARMERS_HINT,
    }


async def assign(
    session: AsyncSession,
    *,
    farm_id: int,
    farmer_id: int,
    expected_farmer_id: int,
    reason: str,
    confirm: bool,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """Point ``farm_id`` at ``farmer_id`` if the caller saw the current owner."""
    if actor.get("role") != "operator":
        raise AssignmentError(403, "operator access required")
    farm = (
        await session.execute(select(Farm).where(Farm.id == farm_id).with_for_update())
    ).scalar_one_or_none()
    if farm is None:
        raise AssignmentError(404, "Farm not found.")
    farmer = await session.get(User, farmer_id)
    if farmer is None:
        raise AssignmentError(404, "Farmer not found.")
    if not is_eligible_farmer(farmer):
        raise AssignmentError(
            422,
            "That account is not a registered farmer. Only farmer accounts that have "
            "signed in to Yieldra can be assigned a farm.",
        )

    if farm.farmer_id == farmer.id:
        # Already in the requested state. Nothing to write and nothing to audit.
        return {
            "status": "unchanged",
            "detail": f"{farm.name} is already assigned to {farmer.name}.",
            "farm": await farm_summary(session, farm, farmer),
            "event": None,
        }

    if farm.farmer_id != expected_farmer_id:
        raise AssignmentError(
            409,
            "This farm's owner changed since you loaded it. Refresh and review the "
            "current owner before trying again.",
        )
    if not confirm:
        raise AssignmentError(422, "Confirm the assignment before saving.")
    text = " ".join((reason or "").split())
    if not REASON_MIN <= len(text) <= REASON_MAX:
        raise AssignmentError(
            422, f"Give a reason of {REASON_MIN} to {REASON_MAX} characters for the audit log."
        )
    unsafe = await blockers(session, farm.id)
    if unsafe:
        raise AssignmentError(409, " ".join(unsafe))

    previous = await session.get(User, farm.farmer_id)
    result = await session.execute(
        update(Farm)
        .where(Farm.id == farm.id, Farm.farmer_id == expected_farmer_id)
        .values(farmer_id=farmer.id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AssignmentError(
            409,
            "Another operator changed this farm's owner at the same time. Refresh and try again.",
        )
    event = FarmAssignmentEvent(
        farm_id=farm.id,
        previous_farmer_id=previous.id if previous else None,
        new_farmer_id=farmer.id,
        actor_id=actor.get("id"),
        actor_via=str(actor.get("via") or "session"),
        reason=text,
        previous_owner_kind="registered" if is_eligible_farmer(previous) else "unregistered",
    )
    session.add(event)
    await session.commit()
    log.info(
        "farm %s assigned from user %s to user %s by %s",
        farm.id, event.previous_farmer_id, farmer.id, event.actor_via,
    )
    fresh = await session.get(Farm, farm.id, populate_existing=True)
    names = {farmer.id: farmer.name}
    if previous:
        names[previous.id] = previous.name
    if event.actor_id:
        actor_user = await session.get(User, event.actor_id)
        if actor_user:
            names[actor_user.id] = actor_user.name
    await session.refresh(event)
    return {
        "status": "assigned",
        "detail": f"{fresh.name} is now assigned to {farmer.name}.",
        "farm": await farm_summary(session, fresh, farmer),
        "event": serialise_event(event, names),
    }
