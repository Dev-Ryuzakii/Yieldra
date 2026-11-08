"""Farmer photo upload, Cencori analysis, and mandatory human review.

The provider result is a recommendation. It cannot charge a sponsor or pay a farmer.
A newer photo supersedes an analysis that is still running, so the old result cannot
attach to the replacement.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.farm import Farm
from app.models.photo_check import PhotoCheck, PhotoCheckReview
from app.models.sponsorship import (
    MilestoneStatus,
    Sponsorship,
    SponsorshipMilestone,
    SponsorshipStatus,
)
from app.services import private_evidence
from app.services.cencori_vision import VisionError, vision_client
from app.services.image_bytes import validate_image
from app.services.photo_findings import (
    PHOTO_LIMITATIONS,
    PROMPT_VERSION,
    FindingsError,
    build_prompt,
    parse_findings,
    recommend,
    uncertainty_for,
)
from app.utils.logger import get_logger

log = get_logger("yieldra.photo_check")

_REVIEW_ACTIONS = {
    "approve": "approved",
    "reject": "rejected",
    "request_new_photo": "new_photo_requested",
}
_TERMINAL = set(_REVIEW_ACTIONS.values())
DISCLAIMER = (
    "AI-assisted; human review required. This result does not charge a sponsor or pay a farmer."
)


class PhotoCheckError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def submit_photo(
    session: AsyncSession,
    *,
    farm_id: int,
    actor: dict[str, Any],
    image: bytes,
    milestone_id: int | None = None,
) -> dict[str, Any]:
    farm = await _farm_for_actor(session, farm_id, actor)
    try:
        media_type = validate_image(image)
    except ValueError as exc:
        raise PhotoCheckError(422, str(exc)) from exc

    milestone, _sponsorship = await _open_milestone(session, farm.id, milestone_id)
    digest = hashlib.sha256(image).hexdigest()
    existing = await _by_digest(session, milestone.id, digest)
    if existing is not None:
        if existing.superseded or existing.status == "superseded":
            raise PhotoCheckError(409, "That photo was replaced and will not be analyzed again.")
        if existing.status == "pending" and _claim_is_stale(existing):
            if await _claim(session, existing):
                prompt = _prompt(farm, milestone)
                return await _finish(session, existing.id, digest, image, media_type, prompt)
            fresh = await session.get(PhotoCheck, existing.id)
            if fresh is not None:
                return await serialise(session, fresh)
        return await serialise(session, existing)

    await _reject_reused_photo(session, farm.id, milestone.id, digest)
    await _supersede_pending(session, milestone.id, digest)
    filename = private_evidence.save(image, media_type, digest)
    now = _now()
    inserted = await session.execute(
        _insert_for(session)(PhotoCheck)
        .values(
            farm_id=farm.id,
            milestone_id=milestone.id,
            stage_key=milestone.key,
            crop_type=farm.crop_type,
            milestone_title=milestone.title,
            evidence_required=milestone.evidence_required or "",
            evidence_sha256=digest,
            media_type=media_type,
            stored_name=filename,
            status="pending",
            superseded=False,
            provider="cencori",
            prompt_version=PROMPT_VERSION,
            claimed_at=now,
            submitted_by_role=str(actor.get("role") or ""),
            submitted_by_id=actor.get("id"),
        )
        .on_conflict_do_nothing(index_elements=["milestone_id", "evidence_sha256"])
        .returning(PhotoCheck.id)
    )
    new_id = inserted.scalar_one_or_none()
    await session.commit()
    if new_id is None:
        winner = await _by_digest(session, milestone.id, digest)
        if winner is None:
            raise PhotoCheckError(409, "That photo is already being checked.")
        return await serialise(session, winner)
    log.info("photo check pending id=%s farm=%s stage=%s", new_id, farm.id, milestone.key)
    return await _finish(session, new_id, digest, image, media_type, _prompt(farm, milestone))


async def list_for_farm(
    session: AsyncSession, farm_id: int, actor: dict[str, Any]
) -> dict[str, Any]:
    farm = await _farm_for_actor(session, farm_id, actor, write=False)
    stage = await _describe_open_stage(session, farm.id)
    rows = (
        await session.scalars(
            select(PhotoCheck).where(PhotoCheck.farm_id == farm.id).order_by(PhotoCheck.id.desc())
        )
    ).all()
    return {
        "farm_id": farm.id,
        "farm_name": farm.name,
        "crop_type": farm.crop_type,
        "open_stage": stage,
        "checks": [await serialise(session, row) for row in rows],
        **_provider_meta(),
    }


async def queue_for_operator(session: AsyncSession) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(PhotoCheck, Farm)
            .join(Farm, PhotoCheck.farm_id == Farm.id)
            .order_by(PhotoCheck.id.desc())
            .limit(100)
        )
    ).all()
    checks = []
    for check, farm in rows:
        item = await serialise(session, check)
        item["farm_name"] = farm.name
        checks.append(item)
    return {"checks": checks, **_provider_meta(), "legacy_auto_charge": True}


async def get_check(session: AsyncSession, check_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    check = await session.get(PhotoCheck, check_id)
    if check is None:
        raise PhotoCheckError(404, "Photo check not found.")
    await _farm_for_actor(session, check.farm_id, actor, write=False)
    item = await serialise(session, check)
    farm = await session.get(Farm, check.farm_id)
    item["farm_name"] = farm.name if farm else None
    return item


async def read_photo(
    session: AsyncSession, check_id: int, actor: dict[str, Any]
) -> tuple[bytes, str]:
    check = await session.get(PhotoCheck, check_id)
    if check is None:
        raise PhotoCheckError(404, "Photo check not found.")
    await _farm_for_actor(session, check.farm_id, actor, write=False)
    try:
        return private_evidence.read(check.stored_name), check.media_type
    except (FileNotFoundError, OSError) as exc:
        raise PhotoCheckError(404, "The photo file is no longer available.") from exc


async def review_check(
    session: AsyncSession,
    check_id: int,
    actor: dict[str, Any],
    action: str,
    rationale: str,
) -> dict[str, Any]:
    if actor.get("role") != "operator":
        raise PhotoCheckError(403, "Only an operator can review a photo check.")
    if action not in _REVIEW_ACTIONS:
        raise PhotoCheckError(422, "Review action must be approve, reject, or request_new_photo.")
    text = " ".join((rationale or "").split())
    if not 15 <= len(text) <= 1000:
        raise PhotoCheckError(422, "Enter a review rationale of at least 15 characters.")

    check = (
        await session.execute(
            select(PhotoCheck).where(PhotoCheck.id == check_id).with_for_update()
        )
    ).scalar_one_or_none()
    if check is None:
        raise PhotoCheckError(404, "Photo check not found.")
    if check.superseded or check.status == "superseded":
        raise PhotoCheckError(409, "This analysis is stale because a newer photo replaced it.")
    if check.status == "pending":
        raise PhotoCheckError(409, "This photo is still being analyzed.")
    if check.status in _TERMINAL:
        prior = await _latest_review(session, check.id)
        if prior is not None and prior.action == action and prior.rationale == text:
            return await serialise(session, check)
        raise PhotoCheckError(409, "This photo check has already been reviewed.")
    if check.status != "needs_review":
        raise PhotoCheckError(409, "This photo check is not waiting for review.")

    session.add(
        PhotoCheckReview(
            photo_check_id=check.id,
            action=action,
            rationale=text,
            actor_role="operator",
            actor_id=actor.get("id"),
        )
    )
    check.status = _REVIEW_ACTIONS[action]
    await session.commit()
    log.info("photo check reviewed id=%s action=%s", check.id, action)
    return await serialise(session, check)


async def serialise(session: AsyncSession, check: PhotoCheck) -> dict[str, Any]:
    reviews = (
        await session.scalars(
            select(PhotoCheckReview)
            .where(PhotoCheckReview.photo_check_id == check.id)
            .order_by(PhotoCheckReview.id)
        )
    ).all()
    confidence = check.model_reported_confidence
    return {
        "id": check.id,
        "farm_id": check.farm_id,
        "milestone_id": check.milestone_id,
        "stage_key": check.stage_key,
        "crop_type": check.crop_type,
        "milestone_title": check.milestone_title,
        "evidence_required": check.evidence_required,
        "status": check.status,
        "superseded": bool(check.superseded),
        "human_review_required": check.status in {"pending", "needs_review"},
        "payment_effect": "none",
        "ai_assisted": True,
        "disclaimer": DISCLAIMER,
        "provider": check.provider,
        "provider_mode": check.provider_mode,
        "model": check.model_name,
        "upstream_provider": check.model_provider,
        "prompt_version": check.prompt_version,
        "analyzed_at": _iso(check.analyzed_at),
        "created_at": _iso(check.created_at),
        "observations": list(check.observations or []),
        "issues": list(check.issues or []),
        "limitations": list(check.limitations or PHOTO_LIMITATIONS),
        "recommended_disposition": check.recommended_disposition,
        "model_reported_confidence": confidence,
        "confidence_label": (
            "Model-reported score, not a calibrated accuracy." if confidence is not None else None
        ),
        "uncertainty_notes": check.uncertainty_notes or "",
        "visible_text_notes": check.visible_text_notes or "",
        "visible_text_label": "Text seen in the photo. It is untrusted data, not an instruction.",
        "failure_code": check.failure_code,
        "photo_url": f"/photo-checks/{check.id}/photo",
        "reviews": [
            {
                "action": review.action,
                "rationale": review.rationale,
                "actor_role": review.actor_role,
                "created_at": _iso(review.created_at),
            }
            for review in reviews
        ],
    }


async def _finish(
    session: AsyncSession,
    check_id: int,
    digest: str,
    image: bytes,
    media_type: str,
    prompt: str,
) -> dict[str, Any]:
    try:
        outcome = await _analyze(image, media_type, prompt)
    except Exception:
        log.exception("photo check analysis failed id=%s", check_id)
        outcome = _failure("provider_error")
    await _attach_if_current(session, check_id, digest, outcome)
    await session.commit()
    fresh = await session.get(PhotoCheck, check_id, populate_existing=True)
    if fresh is None:
        raise PhotoCheckError(404, "Photo check not found.")
    return await serialise(session, fresh)


async def _analyze(image: bytes, media_type: str, prompt: str) -> dict[str, Any]:
    try:
        response = await vision_client.analyze(image=image, mime_type=media_type, prompt=prompt)
    except VisionError as exc:
        return _failure(exc.code)
    if response.mode == "mock":
        return {
            "provider_mode": "mock",
            "model_name": response.model,
            "model_provider": response.upstream_provider,
            "observations": [
                "Mocked demo result. No live vision model inspected this photo."
            ],
            "issues": ["Live Cencori vision was not called."],
            "recommended_disposition": "review",
            "model_reported_confidence": None,
            "uncertainty_notes": (
                "This is a labeled mock for development. It is not a crop judgment and it "
                "is not a successful live analysis."
            ),
            "visible_text_notes": "",
            "failure_code": None,
        }
    try:
        findings = parse_findings(response.analysis)
    except FindingsError:
        failed = _failure("malformed")
        failed["model_name"] = response.model
        failed["model_provider"] = response.upstream_provider
        failed["provider_mode"] = response.mode
        return failed
    disposition = recommend(findings)
    return {
        "provider_mode": response.mode,
        "model_name": response.model,
        "model_provider": response.upstream_provider,
        "observations": findings.observations,
        "issues": findings.issues,
        "recommended_disposition": disposition,
        "model_reported_confidence": findings.model_reported_confidence,
        "uncertainty_notes": uncertainty_for(findings, disposition),
        "visible_text_notes": findings.visible_text_notes,
        "failure_code": None,
    }


def _failure(code: str) -> dict[str, Any]:
    messages = {
        "missing_key": "Live AI analysis is blocked because CENCORI_API_KEY is not configured.",
        "timeout": "The vision provider did not answer in time. Nothing was approved.",
        "provider_error": "The vision provider returned an error. Nothing was approved.",
        "malformed": (
            "The vision provider returned a response that did not match the required schema. "
            "Nothing was approved."
        ),
    }
    mode = "blocked" if code == "missing_key" else "live"
    return {
        "provider_mode": mode,
        "model_name": None,
        "model_provider": None,
        "observations": [],
        "issues": [messages.get(code, "The photo could not be analyzed. Nothing was approved.")],
        "recommended_disposition": "review",
        "model_reported_confidence": None,
        "uncertainty_notes": "The analysis did not finish. A person can review the attempt.",
        "visible_text_notes": "",
        "failure_code": code,
    }


async def _attach_if_current(
    session: AsyncSession, check_id: int, digest: str, outcome: dict[str, Any]
) -> None:
    now = _now()
    result = await session.execute(
        update(PhotoCheck)
        .where(
            PhotoCheck.id == check_id,
            PhotoCheck.evidence_sha256 == digest,
            PhotoCheck.status == "pending",
            PhotoCheck.superseded.is_(False),
        )
        .values(
            status="needs_review",
            provider_mode=outcome["provider_mode"],
            model_name=outcome["model_name"],
            model_provider=outcome["model_provider"],
            analyzed_at=now,
            observations=outcome["observations"],
            issues=outcome["issues"],
            limitations=list(PHOTO_LIMITATIONS),
            recommended_disposition=outcome["recommended_disposition"],
            model_reported_confidence=outcome["model_reported_confidence"],
            uncertainty_notes=outcome["uncertainty_notes"],
            visible_text_notes=outcome["visible_text_notes"],
            failure_code=outcome["failure_code"],
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        log.info(
            "photo check %s not attached; the photo was replaced or already finished", check_id
        )
        return
    log.info(
        "photo check attached id=%s mode=%s disposition=%s failure=%s",
        check_id,
        outcome["provider_mode"],
        outcome["recommended_disposition"],
        outcome["failure_code"],
    )


async def _farm_for_actor(
    session: AsyncSession, farm_id: int, actor: dict[str, Any], write: bool = True
) -> Farm:
    farm = await session.get(Farm, farm_id)
    if farm is None:
        raise PhotoCheckError(404, "Farm not found.")
    role = actor.get("role")
    if role == "operator":
        return farm
    if role != "farmer" or actor.get("id") != farm.farmer_id:
        raise PhotoCheckError(403, "You can only access photos for your own farm.")
    if write and role != "farmer":
        raise PhotoCheckError(403, "Only the farmer or an operator can submit a photo.")
    return farm


async def _open_milestone(
    session: AsyncSession, farm_id: int, milestone_id: int | None
) -> tuple[SponsorshipMilestone, Sponsorship]:
    stmt = (
        select(SponsorshipMilestone, Sponsorship)
        .join(Sponsorship, SponsorshipMilestone.sponsorship_id == Sponsorship.id)
        .where(
            Sponsorship.farm_id == farm_id,
            Sponsorship.status == SponsorshipStatus.active,
            SponsorshipMilestone.status == MilestoneStatus.awaiting_evidence,
            SponsorshipMilestone.evidence_required.is_not(None),
        )
        .order_by(SponsorshipMilestone.sequence, SponsorshipMilestone.id)
        .with_for_update(of=SponsorshipMilestone)
    )
    if milestone_id is not None:
        stmt = stmt.where(SponsorshipMilestone.id == milestone_id)
    rows = (await session.execute(stmt)).all()
    if rows:
        return rows[0][0], rows[0][1]
    if milestone_id is None:
        raise PhotoCheckError(409, "No sponsorship stage is waiting for a photo.")
    other = await session.get(SponsorshipMilestone, milestone_id)
    if other is None:
        raise PhotoCheckError(404, "Milestone not found.")
    sponsorship = await session.get(Sponsorship, other.sponsorship_id)
    if sponsorship is None or sponsorship.farm_id != farm_id:
        raise PhotoCheckError(404, "Milestone not found.")
    if not other.evidence_required:
        raise PhotoCheckError(409, "This stage does not use a photo.")
    raise PhotoCheckError(409, "This stage is not waiting for a photo.")


async def _describe_open_stage(session: AsyncSession, farm_id: int) -> dict[str, Any] | None:
    try:
        milestone, _sponsorship = await _open_milestone(session, farm_id, None)
    except PhotoCheckError:
        return None
    return {
        "milestone_id": milestone.id,
        "key": milestone.key,
        "title": milestone.title,
        "evidence_required": milestone.evidence_required,
    }


async def _by_digest(session: AsyncSession, milestone_id: int, digest: str) -> PhotoCheck | None:
    return (
        await session.execute(
            select(PhotoCheck).where(
                PhotoCheck.milestone_id == milestone_id,
                PhotoCheck.evidence_sha256 == digest,
            )
        )
    ).scalar_one_or_none()


async def _reject_reused_photo(
    session: AsyncSession, farm_id: int, milestone_id: int, digest: str
) -> None:
    other = (
        await session.execute(
            select(PhotoCheck.id).where(
                PhotoCheck.evidence_sha256 == digest,
                or_(PhotoCheck.farm_id != farm_id, PhotoCheck.milestone_id != milestone_id),
            ).limit(1)
        )
    ).first()
    legacy = (
        await session.execute(
            select(SponsorshipMilestone.id)
            .where(SponsorshipMilestone.evidence_sha256 == digest)
            .limit(1)
        )
    ).first()
    if other is not None or legacy is not None:
        raise PhotoCheckError(409, "That photo has already been submitted. Send a new photo.")


async def _supersede_pending(session: AsyncSession, milestone_id: int, digest: str) -> None:
    """Drop an in-flight check only when a different photo replaced it."""
    await session.execute(
        update(PhotoCheck)
        .where(
            PhotoCheck.milestone_id == milestone_id,
            PhotoCheck.status == "pending",
            PhotoCheck.superseded.is_(False),
            PhotoCheck.evidence_sha256 != digest,
        )
        .values(status="superseded", superseded=True)
        .execution_options(synchronize_session=False)
    )


async def _claim(session: AsyncSession, check: PhotoCheck) -> bool:
    seen = check.claimed_at
    now = _now()
    stmt = update(PhotoCheck).where(
        PhotoCheck.id == check.id,
        PhotoCheck.status == "pending",
        PhotoCheck.superseded.is_(False),
    )
    if seen is None:
        stmt = stmt.where(PhotoCheck.claimed_at.is_(None))
    else:
        stmt = stmt.where(PhotoCheck.claimed_at == seen)
    result = await session.execute(stmt.values(claimed_at=now))
    await session.commit()
    return result.rowcount == 1


def _claim_is_stale(check: PhotoCheck) -> bool:
    claimed = _aware(check.claimed_at or check.created_at)
    window = timedelta(seconds=settings.cencori_vision_timeout_seconds + 15)
    return _now() - claimed >= window


def _prompt(farm: Farm, milestone: SponsorshipMilestone) -> str:
    return build_prompt(
        crop_type=farm.crop_type,
        milestone_title=milestone.title,
        stage_key=milestone.key,
        evidence_required=milestone.evidence_required or "",
        location=farm.location,
    )


async def _latest_review(session: AsyncSession, check_id: int) -> PhotoCheckReview | None:
    return (
        await session.scalars(
            select(PhotoCheckReview)
            .where(PhotoCheckReview.photo_check_id == check_id)
            .order_by(PhotoCheckReview.id.desc())
        )
    ).first()


def _provider_meta() -> dict[str, Any]:
    return {
        "provider": "cencori",
        "provider_mode": settings.photo_check_mode,
        "model": settings.cencori_vision_model,
        "route": "POST /v1/vision",
        "human_review_required": True,
        "authorizes_payment": False,
        "legacy_auto_charge": True,
    }


def _insert_for(session: AsyncSession):
    name = session.get_bind().dialect.name
    return postgres_insert if name == "postgresql" else sqlite_insert


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).isoformat()
