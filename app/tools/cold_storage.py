"""Cold storage booking tools.

Facilities are real rows in the ``cold_storage_facilities`` table (populated via the
reference CRUD endpoints). Booking confirmation is recorded in the DB by the logistics
agent; there is no external cold-storage provider API to call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.agents.base import Tool
from app.database import async_session_factory
from app.services.reference import available_facilities
from app.utils.logger import get_logger

log = get_logger("yieldra.tools.cold_storage")


async def check_available_slots(location: str, date: str, quantity_kg: float) -> list[dict[str, Any]]:
    """Return cold storage facilities near ``location`` that can hold ``quantity_kg``."""
    async with async_session_factory() as session:
        facilities = await available_facilities(session, location, quantity_kg)
    slots = [
        {
            "facility_id": f.facility_code,
            "name": f.name,
            "location": f.location,
            "capacity_kg": float(f.capacity_kg),
            "temp_c": float(f.temperature_celsius),
            "date": date,
            "available": float(f.capacity_kg) >= quantity_kg,
        }
        for f in facilities
    ]
    log.info("cold_storage slots location=%s -> %d", location, len(slots))
    return slots


async def book_cold_storage(facility_id: str, harvest_id: str, date: str) -> dict[str, Any]:
    """Book a cold storage slot. Returns a booking confirmation."""
    booking_id = f"BK-{uuid.uuid4().hex[:8].upper()}"
    log.info("cold_storage booked %s for harvest=%s on %s", facility_id, harvest_id, date)
    return {
        "booking_id": booking_id,
        "facility_id": facility_id,
        "harvest_id": harvest_id,
        "date": date,
        "confirmed": True,
        "booked_at": datetime.now(timezone.utc).isoformat(),
    }


async def confirm_delivery(booking_id: str, proof_photo_url: str) -> dict[str, Any]:
    """Confirm crop delivery to a cold storage booking with photo proof."""
    log.info("cold_storage delivery confirmed booking=%s", booking_id)
    return {
        "booking_id": booking_id,
        "delivered": True,
        "proof_photo_url": proof_photo_url,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }


COLD_STORAGE_TOOLS: list[Tool] = [
    Tool(
        name="check_available_slots",
        description="Find cold storage facilities near a location that can hold a quantity of crops.",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "quantity_kg": {"type": "number"},
            },
            "required": ["location", "date", "quantity_kg"],
        },
        handler=check_available_slots,
    ),
    Tool(
        name="book_cold_storage",
        description="Book a cold storage slot at a facility for a harvest on a date.",
        parameters={
            "type": "object",
            "properties": {
                "facility_id": {"type": "string"},
                "harvest_id": {"type": "string"},
                "date": {"type": "string"},
            },
            "required": ["facility_id", "harvest_id", "date"],
        },
        handler=book_cold_storage,
    ),
]
