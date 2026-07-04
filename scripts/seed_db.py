"""Seed realistic demo data for the Yieldra hackathon demo.

Idempotent: wipes the domain tables and re-inserts a known fixture set so the
demo always starts from the same state. Run after ``alembic upgrade head``:

    python scripts/seed_db.py

The ``phone`` field doubles as the messaging address (Telegram chat id for real
users). Seeded demo users use placeholder +234803000XXXX values and only work in
mock mode; for real Telegram tests, onboard users by messaging the bot instead.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete

from app.database import async_session_factory, engine
from app.models.farm import Farm, FarmPlot, FarmStatus, PlotStatus
from app.models.harvest import ColdStorageBooking, Harvest, HarvestStatus
from app.models.investment import Investment, InvestmentStatus
from app.models.offtake import ContractStatus, OfftakeContract
from app.models.reference import ColdStorageFacility, CropParameter
from app.models.user import Language, User, UserRole
from app.utils.money import naira_to_kobo

# Order matters: children before parents (FK constraints).
_TABLES_IN_DELETE_ORDER = [
    OfftakeContract,
    ColdStorageBooking,
    Harvest,
    Investment,
    FarmPlot,
    Farm,
    User,
    ColdStorageFacility,
    CropParameter,
]


async def _wipe(session) -> None:
    for model in _TABLES_IN_DELETE_ORDER:
        await session.execute(delete(model))


async def _seed(session) -> None:
    # -- Reference data ---------------------------------------------------
    session.add_all(
        [
            CropParameter(
                crop_type="cassava",
                price_per_kg=naira_to_kobo(150),
                return_multiplier=1.25,
                harvest_months=10,
                spacing_cm=100,
                plant_window="Apr-May",
            ),
            CropParameter(
                crop_type="maize",
                price_per_kg=naira_to_kobo(250),
                return_multiplier=1.30,
                harvest_months=4,
                spacing_cm=75,
                plant_window="Mar-Apr",
            ),
            CropParameter(
                crop_type="tomatoes",
                price_per_kg=naira_to_kobo(400),
                return_multiplier=1.40,
                harvest_months=3,
                spacing_cm=60,
                plant_window="Sep-Oct",
            ),
        ]
    )
    session.add_all(
        [
            ColdStorageFacility(
                facility_code="CS-OGUN-01",
                name="Abeokuta Cold Hub",
                location="Ogun",
                capacity_kg=50_000,
                temperature_celsius=4.0,
            ),
            ColdStorageFacility(
                facility_code="CS-BENUE-01",
                name="Makurdi Grain Store",
                location="Benue",
                capacity_kg=120_000,
                temperature_celsius=10.0,
            ),
            ColdStorageFacility(
                facility_code="CS-KANO-01",
                name="Kano Fresh Cold Room",
                location="Kano",
                capacity_kg=30_000,
                temperature_celsius=2.0,
            ),
        ]
    )

    # -- Farmers ----------------------------------------------------------
    adunni = User(name="Adunni Okafor", phone="+2348030000001", role=UserRole.farmer,
                  language_preference=Language.yoruba)
    musa = User(name="Musa Ibrahim", phone="+2348030000002", role=UserRole.farmer,
                language_preference=Language.hausa)
    grace = User(name="Grace Eze", phone="+2348030000003", role=UserRole.farmer,
                 language_preference=Language.english)
    session.add_all([adunni, musa, grace])

    # -- Investors --------------------------------------------------------
    investors = [
        User(name="Tunde Bakare", phone="+2348031001001", role=UserRole.investor,
             language_preference=Language.english),
        User(name="Chidi Nwosu", phone="+2348031001002", role=UserRole.investor,
             language_preference=Language.english),
        User(name="Aisha Bello", phone="+2348031001003", role=UserRole.investor,
             language_preference=Language.hausa),
        User(name="Funke Adeyemi", phone="+2348031001004", role=UserRole.investor,
             language_preference=Language.yoruba),
        User(name="Emeka Obi", phone="+2348031001005", role=UserRole.investor,
             language_preference=Language.english),
    ]
    session.add_all(investors)

    # -- Buyers -----------------------------------------------------------
    restaurant = User(name="Lagos Kitchen Ltd", phone="+2348032002001", role=UserRole.buyer,
                      language_preference=Language.english)
    exporter = User(name="AfriExport Co", phone="+2348032002002", role=UserRole.buyer,
                    language_preference=Language.english)
    session.add_all([restaurant, exporter])

    await session.flush()  # assign IDs

    # -- Farms ------------------------------------------------------------
    cassava = Farm(name="Adunni Cassava Plot", farmer_id=adunni.id, location="Ogun",
                   crop_type="cassava", total_plots=10, available_plots=5,
                   status=FarmStatus.growing)
    maize = Farm(name="Musa Maize Field", farmer_id=musa.id, location="Benue",
                 crop_type="maize", total_plots=8, available_plots=0,
                 status=FarmStatus.completed)
    tomatoes = Farm(name="Grace Tomato Garden", farmer_id=grace.id, location="Kano",
                    crop_type="tomatoes", total_plots=12, available_plots=8,
                    status=FarmStatus.listed)
    session.add_all([cassava, maize, tomatoes])
    await session.flush()

    # -- Investments + plots ---------------------------------------------
    # Cassava farm (growing) — 3 investors, returns still pending.
    _invest(session, investors[0], cassava, 300_000, "cassava", InvestmentStatus.active)
    _invest(session, investors[1], cassava, 200_000, "cassava", InvestmentStatus.active)
    _invest(session, investors[3], cassava, 150_000, "cassava", InvestmentStatus.active)

    # Maize farm (completed harvest, sold) — 2 investors awaiting payout.
    _invest(session, investors[2], maize, 250_000, "maize", InvestmentStatus.active)
    _invest(session, investors[4], maize, 250_000, "maize", InvestmentStatus.active)

    await session.flush()

    # -- Harvests ---------------------------------------------------------
    # In-progress harvest on the cassava farm (logistics agent can demo live).
    cassava_harvest = Harvest(
        farm_id=cassava.id,
        expected_date=date.today() + timedelta(days=5),
        yield_kg=8_000,
        status=HarvestStatus.ready,
    )
    # Completed + sold harvest on the maize farm (payout distributor can demo).
    maize_harvest = Harvest(
        farm_id=maize.id,
        expected_date=date.today() - timedelta(days=10),
        actual_date=date.today() - timedelta(days=3),
        yield_kg=12_000,
        status=HarvestStatus.sold,
    )
    session.add_all([cassava_harvest, maize_harvest])
    await session.flush()

    # Cold storage booking for the sold maize harvest.
    session.add(
        ColdStorageBooking(
            harvest_id=maize_harvest.id,
            facility_name="CS-BENUE-01",
            booked_at=datetime.now(timezone.utc) - timedelta(days=4),
            confirmed=True,
            temperature_celsius=10.0,
        )
    )

    # Signed offtake contract for the sold maize harvest (drives the payout).
    price_kobo = naira_to_kobo(250)
    qty = 12_000
    session.add(
        OfftakeContract(
            harvest_id=maize_harvest.id,
            buyer_id=exporter.id,
            quantity_kg=qty,
            price_per_kg=price_kobo,
            total_value=price_kobo * qty,
            status=ContractStatus.signed,
            pdf_url=None,
        )
    )


def _invest(session, investor: User, farm: Farm, amount_ngn: int, crop: str,
            status: InvestmentStatus) -> None:
    """Create a matching Investment + FarmPlot for one investor."""
    amount_kobo = naira_to_kobo(amount_ngn)
    multipliers = {"cassava": 1.25, "maize": 1.30, "tomatoes": 1.40}
    expected = int(round(amount_kobo * multipliers.get(crop, 1.20)))
    share_pct = round(100.0 / max(farm.total_plots, 1), 3)
    session.add(
        Investment(
            farm_id=farm.id,
            investor_id=investor.id,
            amount_ngn=amount_kobo,
            shares=share_pct,
            expected_return_ngn=expected,
            actual_return_ngn=0,
            payment_reference=f"seed_{investor.id}_{farm.id}",
            status=status,
        )
    )
    session.add(
        FarmPlot(
            farm_id=farm.id,
            investor_id=investor.id,
            share_percentage=share_pct,
            amount_invested=amount_kobo,
            status=PlotStatus.owned,
        )
    )


async def main() -> None:
    async with async_session_factory() as session:
        await _wipe(session)
        await _seed(session)
        await session.commit()
    await engine.dispose()
    print("Seed complete: 3 farmers, 5 investors, 2 buyers, 3 farms, 2 harvests.")


if __name__ == "__main__":
    asyncio.run(main())
