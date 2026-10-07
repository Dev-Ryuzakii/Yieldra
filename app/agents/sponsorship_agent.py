"""Sponsorship Agent — pay-as-it-grows farm sponsorship over PayPal.

Flow:
  1. ``start``            sponsor picks a farm and an amount -> PayPal order for the
                          first tranche, approved once by the sponsor (saves their
                          PayPal account for later tranches).
  2. ``confirm_payment``  PayPal sends the sponsor back -> capture, activate.
  3. ``submit_evidence``  farmer sends a photo -> Milestone Verification Agent judges
                          it -> policy decides -> the next tranche is charged to the
                          saved PayPal account, held for review, or refused.
  4. ``review_milestone`` a person settles a verdict the policy would not auto-approve.

Rules this agent keeps:
  * The model never moves money. It judges photos and writes messages; charging is
    plain code behind ``milestone_agent.decide``.
  * Every charge carries an idempotency key, so a retry can never charge twice.
  * A successful charge is committed before anything else (messages, model calls)
    can fail.

Not built yet: paying the farmer the naira equivalent of each tranche (Paystack leg).
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent, fetch_image
from app.agents.milestone_agent import MilestoneAgent, decide
from app.config import settings
from app.models.farm import Farm
from app.models.sponsorship import (
    MilestoneStatus,
    Sponsorship,
    SponsorshipMilestone,
    SponsorshipStatus,
)
from app.models.user import User
from app.services.milestones import (
    MAX_TOTAL_MINOR,
    MIN_TOTAL_MINOR,
    plan_for_crop,
    split_amount,
)
from app.tools import paypal
from app.tools.paypal import PayPalError
from app.tools.telegram import send_text_message
from app.utils.logger import get_logger
from app.utils.money import format_usd

log = get_logger("yieldra.agent.sponsorship")

CURRENCY = "USD"

SYSTEM_PROMPT = (
    "You are Yieldra's Sponsorship Agent. You write short updates about a farm "
    "sponsorship for sponsors and farmers. Use only the facts in the context; never "
    "invent figures, dates or names. Never promise yields or financial returns. Keep "
    "every message to at most 3 short sentences. Respond in the user's language."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SponsorshipAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(model=settings.model_report, system_prompt=SYSTEM_PROMPT)
        self.verifier = MilestoneAgent()

    # -- 1. start ----------------------------------------------------------
    async def start(
        self, session: AsyncSession, sponsor_id: int, farm_id: int, total_minor: int
    ) -> dict[str, Any]:
        """Create a sponsorship and the PayPal order for its first tranche."""
        sponsor = await session.get(User, sponsor_id)
        farm = await session.get(Farm, farm_id)
        if sponsor is None or farm is None:
            return {"status": "error", "message": "sponsor or farm not found"}
        if not MIN_TOTAL_MINOR <= total_minor <= MAX_TOTAL_MINOR:
            return {
                "status": "invalid_amount",
                "message": (
                    f"total must be between {format_usd(MIN_TOTAL_MINOR)} and "
                    f"{format_usd(MAX_TOTAL_MINOR)}"
                ),
            }

        plan = plan_for_crop(farm.crop_type)
        amounts = split_amount(total_minor, [t.share_bps for t in plan])

        sponsorship = Sponsorship(
            reference=f"ysp-{uuid.uuid4().hex[:16]}",
            farm_id=farm.id,
            sponsor_id=sponsor.id,
            total_minor=total_minor,
            currency=CURRENCY,
            status=SponsorshipStatus.pending_approval,
        )
        session.add(sponsorship)
        await session.flush()

        milestones = [
            SponsorshipMilestone(
                sponsorship_id=sponsorship.id,
                sequence=index,
                key=template.key,
                title=template.title,
                evidence_required=template.evidence,
                share_bps=template.share_bps,
                amount_minor=amount,
                status=MilestoneStatus.locked,
            )
            for index, (template, amount) in enumerate(zip(plan, amounts), start=1)
        ]
        session.add_all(milestones)
        await session.flush()

        first = milestones[0]
        base = settings.public_base_url.rstrip("/")
        order = await paypal.create_order(
            amount_minor=first.amount_minor,
            currency=CURRENCY,
            reference=self._payment_reference(sponsorship, first),
            description=f"Yieldra sponsorship: {farm.name} (tranche 1 of {len(milestones)})",
            return_url=f"{base}/sponsorships/paypal/return",
            cancel_url=f"{base}/sponsorships/paypal/cancel",
            save_payment_method=True,
        )
        sponsorship.paypal_order_id = order["order_id"]
        first.paypal_order_id = order["order_id"]
        await session.flush()

        log.info(
            "sponsorship started id=%s farm=%s total=%s order=%s",
            sponsorship.id, farm.id, total_minor, order["order_id"],
        )
        return {
            "status": "pending_approval",
            "approve_url": order["approve_url"],
            "sponsorship": self.serialise(sponsorship, milestones, farm),
        }

    # -- 2. confirm --------------------------------------------------------
    async def confirm_payment(self, session: AsyncSession, order_id: str) -> dict[str, Any]:
        """Capture the approved first-tranche order and activate the sponsorship."""
        sponsorship = await self._by_order(session, order_id)
        if sponsorship is None:
            return {"status": "error", "message": "unknown PayPal order"}
        farm = await session.get(Farm, sponsorship.farm_id)
        milestones = await self._milestones(session, sponsorship.id)

        if sponsorship.status in (SponsorshipStatus.active, SponsorshipStatus.completed):
            # The sponsor reloaded the return page; nothing more to do.
            return {
                "status": "already_confirmed",
                "sponsorship": self.serialise(sponsorship, milestones, farm),
            }
        if sponsorship.status == SponsorshipStatus.cancelled:
            return {"status": "error", "message": "this sponsorship was cancelled"}

        first = milestones[0]
        try:
            capture = await paypal.capture_order(order_id)
        except PayPalError as exc:
            log.warning("first tranche capture failed order=%s issue=%s", order_id, exc.issue)
            return {"status": "payment_failed", "issue": exc.issue, "message": str(exc)}

        if capture.get("status") != "COMPLETED":
            return {"status": "payment_failed", "issue": capture.get("status") or "NOT_COMPLETED"}
        if (
            capture.get("amount_minor") != first.amount_minor
            or (capture.get("currency") or CURRENCY) != sponsorship.currency
        ):
            # Money moved but not the amount we asked for: stop and let a person look.
            log.error(
                "capture amount mismatch order=%s expected=%s got=%s %s",
                order_id, first.amount_minor, capture.get("amount_minor"), capture.get("currency"),
            )
            return {"status": "error", "message": "captured amount does not match the order"}

        first.status = MilestoneStatus.paid
        first.paypal_capture_id = capture.get("capture_id")
        first.paid_at = _now()
        sponsorship.paypal_vault_id = capture.get("vault_id")
        sponsorship.status = SponsorshipStatus.active
        nxt = milestones[1] if len(milestones) > 1 else None
        if nxt is not None:
            nxt.status = MilestoneStatus.awaiting_evidence
        # The sponsor has been charged: make that durable before anything else can fail.
        await session.commit()

        if not sponsorship.paypal_vault_id:
            log.warning(
                "sponsorship %s: PayPal did not return a saved payment method, so later "
                "tranches cannot be charged. Enable 'Save payment methods' (Vault) on the "
                "PayPal app.",
                sponsorship.id,
            )

        farmer = await session.get(User, farm.farmer_id) if farm else None
        sponsor = await session.get(User, sponsorship.sponsor_id)
        if farmer is not None and nxt is not None:
            await self._notify(
                farmer,
                f"Yieldra: A sponsor has funded {farm.name}. {format_usd(first.amount_minor)} "
                f"is released for inputs and land preparation. Next stage: {nxt.title}. When "
                f"it is done, send a photo with the caption PROOF to unlock "
                f"{format_usd(nxt.amount_minor)}.",
            )
        if sponsor is not None:
            await self._notify(
                sponsor,
                f"Yieldra: Thank you. {format_usd(first.amount_minor)} of your "
                f"{format_usd(sponsorship.total_minor)} sponsorship of {farm.name} is paid. "
                f"The rest is charged in stages, each only after the farmer's photo passes "
                f"verification.",
            )
        log.info("sponsorship activated id=%s vault=%s", sponsorship.id, bool(sponsorship.paypal_vault_id))
        return {
            "status": "active",
            "saved_payment_method": bool(sponsorship.paypal_vault_id),
            "sponsorship": self.serialise(sponsorship, milestones, farm),
        }

    async def abandon(self, session: AsyncSession, order_id: str) -> dict[str, Any]:
        """The sponsor backed out on PayPal's page before approving."""
        sponsorship = await self._by_order(session, order_id)
        if sponsorship is None:
            return {"status": "error", "message": "unknown PayPal order"}
        if sponsorship.status == SponsorshipStatus.pending_approval:
            sponsorship.status = SponsorshipStatus.cancelled
            await session.flush()
        return {"status": sponsorship.status.value, "sponsorship_id": sponsorship.id}

    # -- 3. evidence -------------------------------------------------------
    async def submit_evidence(
        self, session: AsyncSession, farm_id: int, photo_url: str
    ) -> dict[str, Any]:
        """Verify a farmer's photo and release the tranche(s) it unlocks."""
        farm = await session.get(Farm, farm_id)
        if farm is None:
            return {"status": "error", "message": "farm not found"}
        farmer = await session.get(User, farm.farmer_id)

        pending = await self._pending_milestones(session, farm_id)
        if not pending:
            await self._notify(
                farmer, "Yieldra: No sponsorship stage is waiting for a photo right now."
            )
            return {"status": "nothing_pending", "farm_id": farm_id}

        # One photo proves one stage: the earliest open one. Several sponsors of the
        # same farm can be waiting on that same stage.
        stage_key = pending[0][0].key
        group = [(m, s) for m, s in pending if m.key == stage_key]
        stage = group[0][0]

        fetched = await fetch_image(photo_url)
        if fetched is None:
            await self._notify(
                farmer, "Yieldra: We could not open that photo. Please send it again."
            )
            return {"status": "photo_unreadable", "farm_id": farm_id}
        data, media_type = fetched
        digest = hashlib.sha256(data).hexdigest()

        reused = await session.execute(
            select(SponsorshipMilestone.id)
            .where(SponsorshipMilestone.evidence_sha256 == digest)
            .limit(1)
        )
        if reused.first() is not None:
            await self._notify(
                farmer,
                "Yieldra: That photo has been used before. Please send a new photo of the "
                "farm taken today, with the caption PROOF.",
            )
            return {"status": "duplicate_photo", "farm_id": farm_id, "milestone": stage_key}

        data_url = f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
        try:
            verdict = await self.verifier.verify(
                photo_url=data_url,
                crop_type=farm.crop_type,
                milestone_title=stage.title,
                evidence_required=stage.evidence_required or stage.title,
                location=farm.location,
            )
        except Exception:  # noqa: BLE001 — a model outage must never release or lose a tranche
            log.exception("milestone verification failed farm=%s stage=%s", farm_id, stage_key)
            await self._notify(
                farmer,
                "Yieldra: We could not check your photo just now. Please send it again later.",
            )
            return {"status": "verification_unavailable", "farm_id": farm_id}

        decision = decide(verdict)
        now = _now()
        releases: list[dict[str, Any]] = []
        for milestone, _ in group:
            # Remember every photo we judged, pass or fail, so it cannot be resubmitted
            # until the model happens to say yes.
            milestone.evidence_sha256 = digest
            milestone.verdict_summary = verdict.summary
            milestone.verdict_confidence = verdict.confidence
            if decision != "reject":
                milestone.verified_at = now
            if decision == "review":
                milestone.status = MilestoneStatus.needs_review
        # Record the verdict for the whole group before any money moves.
        await session.commit()
        if decision == "release":
            for milestone, sponsorship in group:
                releases.append(
                    await self._release(
                        session, sponsorship, milestone, farm, verdict.observations
                    )
                )
            await session.commit()

        if decision == "reject":
            await self._notify(
                farmer,
                f"Yieldra: This photo does not yet show '{stage.title}'. We need to see: "
                f"{stage.evidence_required} Please send a clear, wide photo with the caption "
                f"PROOF.",
            )
        elif decision == "review":
            await self._notify(
                farmer,
                f"Yieldra: Thank you. Your photo for '{stage.title}' is being checked by our "
                f"team. We will message you soon.",
            )
        else:
            await self._notify_farmer_released(session, farmer, farm, stage, releases)

        log.info(
            "evidence farm=%s stage=%s decision=%s confidence=%.2f releases=%d",
            farm_id, stage_key, decision, verdict.confidence, len(releases),
        )
        if decision == "release":
            # Verified is not the same as paid: say so if every charge failed.
            any_paid = any(r["status"] == "paid" for r in releases)
            status = "released" if any_paid else "payment_failed"
        else:
            status = {"review": "needs_review", "reject": "rejected"}[decision]
        return {
            "status": status,
            "farm_id": farm_id,
            "milestone": stage_key,
            "verdict": verdict.model_dump(),
            "releases": releases,
        }

    # -- 4. human review / retries ----------------------------------------
    async def review_milestone(
        self, session: AsyncSession, milestone_id: int, approve: bool
    ) -> dict[str, Any]:
        """A person settles a milestone the policy held for review."""
        milestone = await session.get(SponsorshipMilestone, milestone_id)
        if milestone is None:
            return {"status": "error", "message": "milestone not found"}
        if milestone.status != MilestoneStatus.needs_review:
            return {"status": "error", "message": "milestone is not waiting for review"}
        sponsorship = await session.get(Sponsorship, milestone.sponsorship_id)
        if sponsorship is None or sponsorship.status != SponsorshipStatus.active:
            return {"status": "error", "message": "sponsorship is not active"}
        farm = await session.get(Farm, sponsorship.farm_id)
        farmer = await session.get(User, farm.farmer_id) if farm else None

        if not approve:
            milestone.status = MilestoneStatus.awaiting_evidence
            milestone.verified_at = None
            await session.commit()
            await self._notify(
                farmer,
                f"Yieldra: Your photo for '{milestone.title}' was not accepted. Please send "
                f"a new, clear photo with the caption PROOF.",
            )
            return {"status": "rejected", "milestone_id": milestone.id}

        release = await self._release(
            session, sponsorship, milestone, farm, milestone.verdict_summary or ""
        )
        await session.commit()
        await self._notify_farmer_released(session, farmer, farm, milestone, [release])
        return {"status": "released", "milestone_id": milestone.id, "releases": [release]}

    async def retry_payment(self, session: AsyncSession, milestone_id: int) -> dict[str, Any]:
        """Try the PayPal charge again for a verified milestone whose charge failed."""
        milestone = await session.get(SponsorshipMilestone, milestone_id)
        if milestone is None:
            return {"status": "error", "message": "milestone not found"}
        if milestone.status != MilestoneStatus.payment_failed:
            return {"status": "error", "message": "milestone has no failed payment to retry"}
        sponsorship = await session.get(Sponsorship, milestone.sponsorship_id)
        if sponsorship is None or sponsorship.status != SponsorshipStatus.active:
            return {"status": "error", "message": "sponsorship is not active"}
        farm = await session.get(Farm, sponsorship.farm_id)
        release = await self._release(
            session, sponsorship, milestone, farm, milestone.verdict_summary or ""
        )
        await session.commit()
        return {"status": release["status"], "milestone_id": milestone.id, "releases": [release]}

    async def cancel(self, session: AsyncSession, sponsorship_id: int) -> dict[str, Any]:
        """Stop a sponsorship. Paid tranches stay paid; nothing further can be charged."""
        sponsorship = await session.get(Sponsorship, sponsorship_id)
        if sponsorship is None:
            return {"status": "error", "message": "sponsorship not found"}
        if sponsorship.status in (SponsorshipStatus.completed, SponsorshipStatus.cancelled):
            return {"status": sponsorship.status.value, "sponsorship_id": sponsorship.id}

        await self._forget_saved_method(sponsorship)
        sponsorship.status = SponsorshipStatus.cancelled
        await session.commit()

        farm = await session.get(Farm, sponsorship.farm_id)
        sponsor = await session.get(User, sponsorship.sponsor_id)
        farmer = await session.get(User, farm.farmer_id) if farm else None
        farm_name = farm.name if farm else "the farm"
        await self._notify(
            sponsor,
            f"Yieldra: Your sponsorship of {farm_name} is cancelled. Your saved PayPal "
            f"account has been removed and you will not be charged again.",
        )
        await self._notify(
            farmer, f"Yieldra: A sponsor has ended their sponsorship of {farm_name}."
        )
        return {"status": "cancelled", "sponsorship_id": sponsorship.id}

    # -- read side ---------------------------------------------------------
    async def get(self, session: AsyncSession, sponsorship_id: int) -> dict[str, Any] | None:
        sponsorship = await session.get(Sponsorship, sponsorship_id)
        if sponsorship is None:
            return None
        farm = await session.get(Farm, sponsorship.farm_id)
        return self.serialise(sponsorship, await self._milestones(session, sponsorship.id), farm)

    async def list_all(
        self,
        session: AsyncSession,
        sponsor_id: int | None = None,
        farm_id: int | None = None,
    ) -> list[dict[str, Any]]:
        stmt = select(Sponsorship).order_by(Sponsorship.id.desc())
        if sponsor_id is not None:
            stmt = stmt.where(Sponsorship.sponsor_id == sponsor_id)
        if farm_id is not None:
            stmt = stmt.where(Sponsorship.farm_id == farm_id)
        out: list[dict[str, Any]] = []
        for sponsorship in (await session.execute(stmt)).scalars():
            farm = await session.get(Farm, sponsorship.farm_id)
            milestones = await self._milestones(session, sponsorship.id)
            out.append(self.serialise(sponsorship, milestones, farm))
        return out

    async def status_text(self, session: AsyncSession, sponsor_id: int) -> str:
        """Plain-text summary of a sponsor's sponsorships, for chat replies."""
        items = [
            s for s in await self.list_all(session, sponsor_id=sponsor_id)
            if s["status"] in ("active", "completed")
        ]
        if not items:
            return "Yieldra: You have no active farm sponsorships yet."
        lines = []
        for item in items:
            line = (
                f"{item['farm_name']}: {format_usd(item['paid_minor'])} of "
                f"{format_usd(item['total_minor'])} released "
                f"({item['milestones_paid']}/{len(item['milestones'])} stages)."
            )
            if item["next_milestone"]:
                line += f" Next: {item['next_milestone']['title']}."
            lines.append(line)
        return "Yieldra sponsorships\n" + "\n".join(lines)

    @staticmethod
    def serialise(
        sponsorship: Sponsorship,
        milestones: list[SponsorshipMilestone],
        farm: Farm | None,
    ) -> dict[str, Any]:
        """API shape. Deliberately omits the saved-payment token."""
        paid = [m for m in milestones if m.status == MilestoneStatus.paid]
        open_ = [m for m in milestones if m.status != MilestoneStatus.paid]

        def one(m: SponsorshipMilestone) -> dict[str, Any]:
            return {
                "id": m.id,
                "sequence": m.sequence,
                "key": m.key,
                "title": m.title,
                "evidence_required": m.evidence_required,
                "amount_minor": m.amount_minor,
                "amount": format_usd(m.amount_minor),
                "status": m.status.value,
                "verdict_summary": m.verdict_summary,
                "verdict_confidence": (
                    float(m.verdict_confidence) if m.verdict_confidence is not None else None
                ),
                "verified_at": m.verified_at,
                "paid_at": m.paid_at,
                "paypal_order_id": m.paypal_order_id,
                "paypal_capture_id": m.paypal_capture_id,
                "failure_reason": m.failure_reason,
            }

        return {
            "id": sponsorship.id,
            "reference": sponsorship.reference,
            "status": sponsorship.status.value,
            "farm_id": sponsorship.farm_id,
            "farm_name": farm.name if farm else None,
            "crop_type": farm.crop_type if farm else None,
            "sponsor_id": sponsorship.sponsor_id,
            "currency": sponsorship.currency,
            "total_minor": sponsorship.total_minor,
            "total": format_usd(sponsorship.total_minor),
            "paid_minor": sum(m.amount_minor for m in paid),
            "milestones_paid": len(paid),
            "next_milestone": one(open_[0]) if open_ else None,
            "has_saved_payment_method": bool(sponsorship.paypal_vault_id),
            "created_at": sponsorship.created_at,
            "milestones": [one(m) for m in milestones],
        }

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _payment_reference(sponsorship: Sponsorship, milestone: SponsorshipMilestone) -> str:
        """Idempotency root for one charge attempt on one tranche.

        The attempt number only advances after PayPal definitively refuses a charge,
        so a retry after a timeout reuses the key and cannot double-charge.
        """
        return f"{sponsorship.reference}-m{milestone.sequence}-a{milestone.payment_attempts}"

    async def _release(
        self,
        session: AsyncSession,
        sponsorship: Sponsorship,
        milestone: SponsorshipMilestone,
        farm: Farm | None,
        observations: str,
    ) -> dict[str, Any]:
        """Charge one verified tranche to the sponsor's saved PayPal account."""
        farm_name = farm.name if farm else "farm"
        sponsor = await session.get(User, sponsorship.sponsor_id)
        result: dict[str, Any] = {
            "sponsorship_id": sponsorship.id,
            "milestone_id": milestone.id,
            "amount_minor": milestone.amount_minor,
        }

        if not sponsorship.paypal_vault_id:
            milestone.status = MilestoneStatus.payment_failed
            milestone.failure_reason = "NO_SAVED_PAYMENT_METHOD"
            return {**result, "status": "payment_failed", "issue": milestone.failure_reason}

        try:
            charge = await paypal.charge_saved_method(
                vault_id=sponsorship.paypal_vault_id,
                amount_minor=milestone.amount_minor,
                currency=sponsorship.currency,
                reference=self._payment_reference(sponsorship, milestone),
                description=f"Yieldra sponsorship: {farm_name} ({milestone.title})",
            )
        except PayPalError as exc:
            # PayPal answered and refused: the next attempt needs a fresh key.
            milestone.status = MilestoneStatus.payment_failed
            milestone.failure_reason = (exc.issue or str(exc))[:160]
            milestone.payment_attempts += 1
            await session.commit()
            await self._notify(
                sponsor,
                f"Yieldra: {farm_name} reached '{milestone.title}', but we could not charge "
                f"your PayPal for {format_usd(milestone.amount_minor)}. No money has moved. "
                f"Please check your PayPal account.",
            )
            return {**result, "status": "payment_failed", "issue": milestone.failure_reason}
        except httpx.HTTPError as exc:
            # No answer from PayPal: the charge may or may not have happened. Keep the
            # same idempotency key so the retry is safe either way.
            milestone.status = MilestoneStatus.payment_failed
            milestone.failure_reason = f"NETWORK_{type(exc).__name__}"[:160]
            await session.commit()
            return {**result, "status": "payment_failed", "issue": milestone.failure_reason}

        milestone.status = MilestoneStatus.paid
        milestone.paypal_order_id = charge.get("order_id")
        milestone.paypal_capture_id = charge.get("capture_id")
        milestone.paid_at = _now()
        milestone.failure_reason = None

        milestones = await self._milestones(session, sponsorship.id)
        nxt = next((m for m in milestones if m.sequence == milestone.sequence + 1), None)
        if nxt is not None:
            if nxt.status == MilestoneStatus.locked:
                nxt.status = MilestoneStatus.awaiting_evidence
        else:
            sponsorship.status = SponsorshipStatus.completed
        # The sponsor has been charged: make that durable before anything else can fail.
        await session.commit()

        if nxt is None:
            # Nothing left to charge, so stop holding the sponsor's payment token.
            await self._forget_saved_method(sponsorship)
            await session.commit()

        await self._notify(
            sponsor,
            await self._sponsor_update(sponsor, farm_name, milestone, observations, nxt),
            localise=False,
        )
        log.info(
            "tranche released sponsorship=%s milestone=%s amount=%s order=%s",
            sponsorship.id, milestone.id, milestone.amount_minor, milestone.paypal_order_id,
        )
        return {
            **result,
            "status": "paid",
            "paypal_order_id": milestone.paypal_order_id,
            "paypal_capture_id": milestone.paypal_capture_id,
        }

    async def _forget_saved_method(self, sponsorship: Sponsorship) -> None:
        """Delete the sponsor's saved PayPal account at PayPal, best effort."""
        vault_id = sponsorship.paypal_vault_id
        if not vault_id:
            return
        try:
            await paypal.delete_saved_method(vault_id)
        except (PayPalError, httpx.HTTPError) as exc:
            # Keep going: we stop using the token either way.
            log.warning("could not delete saved method for sponsorship %s: %s", sponsorship.id, exc)
        sponsorship.paypal_vault_id = None

    async def _sponsor_update(
        self,
        sponsor: User | None,
        farm_name: str,
        milestone: SponsorshipMilestone,
        observations: str,
        nxt: SponsorshipMilestone | None,
    ) -> str:
        """Model-written progress update for the sponsor, with a plain fallback."""
        amount = format_usd(milestone.amount_minor)
        next_line = (
            f"Next stage: {nxt.title} ({format_usd(nxt.amount_minor)})."
            if nxt is not None
            else "This sponsorship is now complete. Thank you."
        )
        fallback = (
            f"Yieldra: {farm_name} reached '{milestone.title}'. {observations} "
            f"{amount} was charged to your PayPal. {next_line}"
        ).replace("  ", " ")
        if not settings.llm_live:
            return fallback
        language = sponsor.language_preference.value if sponsor else "english"
        try:
            text = await self.run(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Write the sponsor's progress update for this milestone as a "
                            f"short chat message in {language}. Say what the photo showed, "
                            "the amount charged, and what comes next."
                        ),
                    }
                ],
                context={
                    "farm": farm_name,
                    "milestone_reached": milestone.title,
                    "what_the_verified_photo_showed": observations,
                    "amount_charged_to_paypal": amount,
                    "next": next_line,
                },
            )
        except Exception:  # noqa: BLE001 — a model outage must not drop the update
            log.exception("sponsor update model call failed; using plain text")
            return fallback
        return text.strip() or fallback

    async def _notify_farmer_released(
        self,
        session: AsyncSession,
        farmer: User | None,
        farm: Farm | None,
        stage: SponsorshipMilestone,
        releases: list[dict[str, Any]],
    ) -> None:
        paid_minor = sum(r["amount_minor"] for r in releases if r["status"] == "paid")
        if paid_minor <= 0:
            await self._notify(
                farmer,
                f"Yieldra: Your photo for '{stage.title}' is verified. The sponsor's payment "
                f"is being processed; we will message you when it is released.",
            )
            return
        sponsorship = await session.get(Sponsorship, releases[0]["sponsorship_id"])
        milestones = await self._milestones(session, sponsorship.id) if sponsorship else []
        nxt = next((m for m in milestones if m.sequence == stage.sequence + 1), None)
        next_line = (
            f"Next stage: {nxt.title}. Send a photo with the caption PROOF when it is done."
            if nxt is not None
            else "This sponsorship is complete. Well done."
        )
        await self._notify(
            farmer,
            f"Yieldra: Verified. '{stage.title}' is confirmed for "
            f"{farm.name if farm else 'your farm'} and {format_usd(paid_minor)} is released. "
            f"{next_line}",
        )

    async def _notify(self, user: User | None, text: str, localise: bool = True) -> None:
        """Send a chat message. Never raises: messaging must not undo a payment."""
        if user is None:
            return
        try:
            if localise:
                text = await self._localise(text, user.language_preference.value)
            await send_text_message(user.phone, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("notify failed user=%s: %s", user.id, type(exc).__name__)

    async def _localise(self, text: str, language: str) -> str:
        """Translate a system message into the user's language; English on any failure."""
        if language == "english" or not settings.llm_live:
            return text
        try:
            translated = await self.run(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Translate this message into {language} for a Nigerian farmer. "
                            "Keep names, amounts and the word PROOF exactly as written. "
                            f"Reply with the translation only.\n\n{text}"
                        ),
                    }
                ]
            )
        except Exception:  # noqa: BLE001
            return text
        return translated.strip() or text

    @staticmethod
    async def _by_order(session: AsyncSession, order_id: str) -> Sponsorship | None:
        result = await session.execute(
            select(Sponsorship).where(Sponsorship.paypal_order_id == order_id)
        )
        return result.scalars().first()

    @staticmethod
    async def _milestones(session: AsyncSession, sponsorship_id: int) -> list[SponsorshipMilestone]:
        result = await session.execute(
            select(SponsorshipMilestone)
            .where(SponsorshipMilestone.sponsorship_id == sponsorship_id)
            .order_by(SponsorshipMilestone.sequence)
        )
        return list(result.scalars())

    @staticmethod
    async def _pending_milestones(
        session: AsyncSession, farm_id: int
    ) -> list[tuple[SponsorshipMilestone, Sponsorship]]:
        """Open photo stages for a farm's active sponsorships, earliest stage first.

        Rows are locked so two photos arriving together cannot both release a tranche.
        """
        result = await session.execute(
            select(SponsorshipMilestone, Sponsorship)
            .join(Sponsorship, SponsorshipMilestone.sponsorship_id == Sponsorship.id)
            .where(
                Sponsorship.farm_id == farm_id,
                Sponsorship.status == SponsorshipStatus.active,
                SponsorshipMilestone.status == MilestoneStatus.awaiting_evidence,
            )
            .order_by(SponsorshipMilestone.sequence, SponsorshipMilestone.id)
            .with_for_update(of=SponsorshipMilestone)
        )
        return [(row[0], row[1]) for row in result.all()]
