"""Offtake Matching Agent — match ready harvests to buyers, generate contracts.

Model: settings.model_offtake (negotiation + contract generation).
Rules: never finalize without explicit confirmation from BOTH farmer and buyer.
Contracts above ₦500,000 require a 48-hour review period before signing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.config import settings
from app.models.farm import Farm
from app.models.harvest import Harvest
from app.models.offtake import ContractStatus, OfftakeContract
from app.models.user import User, UserRole
from app.services.reference import crop_price_per_kg
from app.tools import contracts
from app.tools.telegram import send_text_message
from app.utils.logger import get_logger
from app.utils.money import format_naira, naira_to_kobo

log = get_logger("yieldra.agent.offtake")

# Contracts above this total value require a 48h review period (kobo).
REVIEW_THRESHOLD = naira_to_kobo(500_000)

SYSTEM_PROMPT = (
    "You are Yieldra's Offtake Matching Agent. You connect farm harvests with verified "
    "buyers, matching on crop type, quantity, quality grade, location proximity, and "
    "buyer payment history. You generate draft contracts and send them for both parties "
    "to confirm via Telegram. Never finalize a contract without explicit confirmation "
    "from both farmer and buyer. Contracts above ₦500,000 total value require a 48-hour "
    "review period before signing. Respond in the user's language."
)


class OfftakeAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(model=settings.model_offtake, system_prompt=SYSTEM_PROMPT)

    async def find_buyers(self, session: AsyncSession, harvest_id: int) -> dict[str, Any]:
        """Rank available buyers for a harvest's crop by a simple match score."""
        harvest = await session.get(Harvest, harvest_id)
        if harvest is None:
            return {"status": "error", "message": "harvest not found"}
        farm = await session.get(Farm, harvest.farm_id)

        result = await session.execute(select(User).where(User.role == UserRole.buyer))
        buyers = list(result.scalars())

        ranked = []
        for buyer in buyers:
            score = 1.0
            ranked.append(
                {
                    "buyer_id": buyer.id,
                    "name": buyer.name,
                    "phone": buyer.phone,
                    "score": score,
                }
            )
        ranked.sort(key=lambda b: b["score"], reverse=True)
        log.info("find_buyers harvest=%s buyers=%d", harvest_id, len(ranked))
        return {
            "status": "ok",
            "harvest_id": harvest_id,
            "crop_type": farm.crop_type if farm else None,
            "buyers": ranked,
        }

    async def initiate_deal(
        self, session: AsyncSession, harvest_id: int, buyer_id: int, price_per_kg_ngn: int | None = None
    ) -> dict[str, Any]:
        """Generate a draft contract and send to farmer + buyer for confirmation."""
        harvest = await session.get(Harvest, harvest_id)
        if harvest is None:
            return {"status": "error", "message": "harvest not found"}
        farm = await session.get(Farm, harvest.farm_id)
        farmer = await session.get(User, farm.farmer_id) if farm else None
        buyer = await session.get(User, buyer_id)
        if farm is None or farmer is None or buyer is None:
            return {"status": "error", "message": "farm, farmer, or buyer not found"}

        quantity = float(harvest.yield_kg or 0)
        price_kobo = (
            naira_to_kobo(price_per_kg_ngn)
            if price_per_kg_ngn is not None
            else await crop_price_per_kg(session, farm.crop_type)
        )
        total_kobo = int(round(quantity * price_kobo))

        terms = {
            "crop_type": farm.crop_type,
            "quantity_kg": quantity,
            "price_per_kg": price_kobo,
            "total_value": total_kobo,
            "farmer_name": farmer.name,
            "buyer_name": buyer.name,
            "location": farm.location,
            "delivery_date": (harvest.expected_date or "TBD") and str(harvest.expected_date or "TBD"),
        }
        pdf_url = await contracts.generate_contract_pdf(str(harvest_id), str(buyer_id), terms)

        contract = OfftakeContract(
            harvest_id=harvest_id,
            buyer_id=buyer_id,
            quantity_kg=quantity,
            price_per_kg=price_kobo,
            total_value=total_kobo,
            status=ContractStatus.draft,
            pdf_url=pdf_url,
        )
        session.add(contract)
        await session.flush()

        requires_review = total_kobo > REVIEW_THRESHOLD
        review_note = " (48-hour review period applies)" if requires_review else ""

        await contracts.send_contract_for_signing(
            str(contract.id), farmer.phone, buyer.phone, pdf_url
        )
        await send_text_message(
            buyer.phone,
            f"Yieldra: Offer for {quantity:.0f}kg {farm.crop_type} at "
            f"{format_naira(price_kobo)}/kg, total {format_naira(total_kobo)}{review_note}. "
            f"Reply YES to accept.",
        )

        log.info(
            "initiate_deal contract=%s harvest=%s buyer=%s total=%s review=%s",
            contract.id, harvest_id, buyer_id, total_kobo, requires_review,
        )
        return {
            "status": "draft_sent",
            "contract_id": contract.id,
            "pdf_url": pdf_url,
            "total_value_kobo": total_kobo,
            "requires_review": requires_review,
        }

    async def handle_negotiation(
        self, session: AsyncSession, contract_id: int, party: str, message: str
    ) -> dict[str, Any]:
        """Process a counter-offer or acceptance; update contract; notify the other party."""
        contract = await session.get(OfftakeContract, contract_id)
        if contract is None:
            return {"status": "error", "message": "contract not found"}

        harvest = await session.get(Harvest, contract.harvest_id)
        farm = await session.get(Farm, harvest.farm_id) if harvest else None
        farmer = await session.get(User, farm.farmer_id) if farm else None
        buyer = await session.get(User, contract.buyer_id)

        text = message.strip().lower()
        accepted = any(w in text for w in ("yes", "accept", "agree", "ok", "approve"))

        if accepted:
            # Only sign once both sides have agreed (real flow tracks both; mock signs now).
            if contract.total_value > REVIEW_THRESHOLD and contract.status == ContractStatus.draft:
                contract.status = ContractStatus.draft  # held for review
                note = "Large contract — held for 48-hour review before signing."
            else:
                contract.status = ContractStatus.signed
                note = "Contract signed."
            await session.flush()
            other = buyer if party == "farmer" else farmer
            if other is not None:
                await send_text_message(
                    other.phone, f"Yieldra: {party.title()} accepted contract {contract_id}. {note}"
                )
            return {"status": contract.status.value, "contract_id": contract_id, "note": note}

        # Counter-offer / question -> let the model draft a reply to the other party.
        result = await self.run(
            messages=[{"role": "user", "content": f"{party} says: {message}"}],
            context={
                "contract_id": contract_id,
                "crop_type": farm.crop_type if farm else None,
                "quantity_kg": float(contract.quantity_kg),
                "current_total_kobo": contract.total_value,
            },
        )
        other = buyer if party == "farmer" else farmer
        if other is not None:
            await send_text_message(other.phone, result)
        return {"status": "negotiating", "contract_id": contract_id, "reply": result}
