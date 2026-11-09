"""Operator assignment of an existing farm to a registered farmer.

Every request goes through the real Afribase cookie session with a faked Afribase
API, so role checks are the ones production uses. No payment provider is called.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.agents.sponsorship_agent import SponsorshipAgent
from app.database import async_session_factory
from app.main import app
from app.models.farm import Farm
from app.models.farm_assignment import FarmAssignmentEvent
from app.models.payout import FarmerDisbursement
from app.models.photo_check import PhotoCheck
from app.models.sponsorship import Sponsorship, SponsorshipMilestone
from app.models.user import User, UserRole
from app.routers import auth
from app.services import farm_assignment
from tests.test_sponsorship import start_and_confirm

REASON = "Farmer confirmed this plot by phone with the field officer."
OPERATOR = "ops@example.com"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        yield http


@pytest.fixture
def afribase(monkeypatch, live_setting):
    """Fake Afribase identities. Signing in with an email signs in as that identity."""
    live_setting(
        afribase_url="https://example.afribase.dev",
        afribase_anon_key="anon",
        secret_key="test-secret-for-assignment",
        operator_emails=OPERATOR,
    )
    identities = {
        email: {"id": f"{email.split('@')[0]}-uid", "email": email,
                "confirmed_at": "2026-01-01T00:00:00Z"}
        for email in (OPERATOR, "newfarmer@example.com", "second@example.com",
                      "sponsor@example.com", "ops2@example.com")
    }

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            if body["email"] not in identities:
                raise HTTPException(401, "Invalid credentials")
            return {"access_token": body["email"], "refresh_token": body["email"],
                    "expires_in": 3600}
        if path == "user":
            return identities[token]
        if path == "logout":
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    return identities


async def sign_in(client, email: str) -> None:
    client.cookies.clear()
    result = await client.post("/account/login", json={"email": email, "password": "test-password"})
    assert result.status_code == 200, result.text


async def registered_farmer(session, email: str, name: str) -> User:
    user = User(name=name, phone=f"web:{email}", email=email,
                afribase_uid=f"{email.split('@')[0]}-uid", role=UserRole.farmer)
    session.add(user)
    await session.commit()
    return user


async def assign(client, farm_id: int, farmer_id: int, expected: int, **extra):
    body = {"farmer_id": farmer_id, "expected_farmer_id": expected,
            "reason": REASON, "confirm": True} | extra
    return await client.post(f"/console/farms/{farm_id}/assignment", json=body)


async def owner(farm_id: int) -> int:
    async with async_session_factory() as fresh:
        return (await fresh.get(Farm, farm_id)).farmer_id


async def event_count() -> int:
    async with async_session_factory() as fresh:
        return await fresh.scalar(select(func.count(FarmAssignmentEvent.id)))


async def test_operator_assigns_a_farm_and_the_farmer_can_then_send_a_photo(
    client, session, world, afribase
):
    await start_and_confirm(SponsorshipAgent(), session, world)
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")

    # Before: the registered farmer has no farm and cannot reach this farm's photos.
    await sign_in(client, "newfarmer@example.com")
    before = (await client.get("/account/dashboard")).json()
    assert before["profile"]["role"] == "farmer"
    assert before["items"] == []
    assert (await client.get(f"/farms/{world.farm.id}/photo-checks")).status_code == 403

    await sign_in(client, OPERATOR)
    overview = (await client.get("/console/farm-assignments")).json()
    farm = next(item for item in overview["farms"] if item["id"] == world.farm.id)
    assert farm["farmer"] == {"id": world.farmer.id, "name": world.farmer.name,
                              "registered": False}
    assert farm["photo"]["open_stage"]["title"]
    assert farm["blockers"] == []
    assert overview["farmers"] == [{"id": farmer.id, "name": "Tolu Ade",
                                    "email_hint": "n•••@example.com", "farm_count": 0}]
    assert overview["farmers_hint"] is None

    result = await assign(client, world.farm.id, farmer.id, world.farmer.id)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "assigned"
    assert body["farm"]["farmer"]["id"] == farmer.id
    operator_row = (await session.execute(select(User).where(User.email == OPERATOR))).scalar_one()
    assert body["event"] | {"id": 0, "created_at": None} == {
        "id": 0, "farm_id": world.farm.id, "previous_farmer": world.farmer.name,
        "previous_farmer_id": world.farmer.id, "new_farmer": "Tolu Ade",
        "new_farmer_id": farmer.id, "actor": operator_row.name, "actor_via": "session",
        "reason": REASON, "created_at": None,
    }
    assert body["event"]["created_at"]
    assert await owner(world.farm.id) == farmer.id
    async with async_session_factory() as fresh:
        event = (await fresh.scalars(select(FarmAssignmentEvent))).one()
        assert (event.previous_farmer_id, event.new_farmer_id, event.actor_id) == (
            world.farmer.id, farmer.id, operator_row.id)
        assert event.previous_owner_kind == "unregistered"
    history = (await client.get("/console/farm-assignments")).json()
    assert history["farms"][0]["history"][0]["new_farmer"] == "Tolu Ade"

    # After: the farm shows on the farmer's dashboard and the photo form is open.
    await sign_in(client, "newfarmer@example.com")
    after = (await client.get("/account/dashboard")).json()
    assert [item["id"] for item in after["items"]] == [world.farm.id]
    assert after["items"][0]["photo_ready"] is True
    assert after["items"][0]["note"].startswith("Waiting for a photo of")
    checks = await client.get(f"/farms/{world.farm.id}/photo-checks")
    assert checks.status_code == 200
    assert checks.json()["open_stage"]["milestone_id"]
    # Sponsorship ownership and recorded payout obligations are untouched.
    async with async_session_factory() as fresh:
        sponsor_ids = (await fresh.scalars(select(Sponsorship.sponsor_id))).all()
        payout_farmers = (await fresh.scalars(select(FarmerDisbursement.farmer_id))).all()
    assert sponsor_ids == [world.sponsor.id]
    assert payout_farmers and set(payout_farmers) == {world.farmer.id}


async def test_assigned_farm_without_a_due_stage_explains_why(client, session, world, afribase):
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    await sign_in(client, OPERATOR)
    assert (await assign(client, world.farm.id, farmer.id, world.farmer.id)).status_code == 200
    await sign_in(client, "newfarmer@example.com")
    item = (await client.get("/account/dashboard")).json()["items"][0]
    assert item["photo_ready"] is False
    assert item["note"] == "No sponsorship yet, so no milestone photo is due."


async def test_non_operators_cannot_list_or_assign(client, session, world, afribase):
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    assert (await client.get("/console/farm-assignments")).status_code == 401
    assert (await assign(client, world.farm.id, farmer.id, world.farmer.id)).status_code == 401
    for email in ("newfarmer@example.com", "sponsor@example.com"):
        await sign_in(client, email)
        listing = await client.get("/console/farm-assignments")
        assert listing.status_code == 403
        assert "example.com" not in listing.text
        assert (await assign(client, world.farm.id, farmer.id, world.farmer.id)).status_code == 403
    assert await owner(world.farm.id) == world.farmer.id
    assert await event_count() == 0


async def test_cross_origin_post_is_refused(client, session, world, afribase):
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    await sign_in(client, OPERATOR)
    result = await client.post(
        f"/console/farms/{world.farm.id}/assignment",
        json={"farmer_id": farmer.id, "expected_farmer_id": world.farmer.id,
              "reason": REASON, "confirm": True},
        headers={"Origin": "https://evil.example"},
    )
    assert result.status_code == 403
    assert await owner(world.farm.id) == world.farmer.id


async def test_invalid_farm_farmer_role_and_input_are_rejected(client, session, world, afribase):
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    await sign_in(client, OPERATOR)
    operator_row = (await session.execute(select(User).where(User.email == OPERATOR))).scalar_one()
    await sign_in(client, "sponsor@example.com")
    sponsor_row = (await session.execute(
        select(User).where(User.email == "sponsor@example.com"))).scalar_one()
    await sign_in(client, OPERATOR)

    assert (await assign(client, 999, farmer.id, world.farmer.id)).status_code == 404
    assert (await assign(client, 0, farmer.id, world.farmer.id)).status_code == 404
    assert (await assign(client, world.farm.id, 999, world.farmer.id)).status_code == 404
    # Wrong roles, a farmer that never signed in, and the operator's own row.
    for target in (sponsor_row.id, world.sponsor.id, operator_row.id):
        result = await assign(client, world.farm.id, target, world.farmer.id)
        assert result.status_code == 422, (target, result.text)
    unregistered = User(name="Chat only", phone="3001", role=UserRole.farmer)
    session.add(unregistered)
    await session.commit()
    assert (await assign(client, world.farm.id, unregistered.id, world.farmer.id)).status_code == 422

    assert (await assign(client, world.farm.id, farmer.id, world.farmer.id,
                         confirm=False)).status_code == 422
    assert (await assign(client, world.farm.id, farmer.id, world.farmer.id,
                         reason="ok")).status_code == 422
    assert (await assign(client, world.farm.id, farmer.id, world.farmer.id,
                         role="operator")).status_code == 422
    assert (await client.post(f"/console/farms/{world.farm.id}/assignment",
                              json={"farmer_id": "x"})).status_code == 422
    assert await owner(world.farm.id) == world.farmer.id
    assert await event_count() == 0
    # The operator's role did not change.
    await session.refresh(operator_row)
    assert operator_row.role == UserRole.sponsor


async def test_repeating_an_assignment_is_idempotent(client, session, world, afribase):
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    await sign_in(client, OPERATOR)
    first = await assign(client, world.farm.id, farmer.id, world.farmer.id)
    second = await assign(client, world.farm.id, farmer.id, world.farmer.id)
    assert first.json()["status"] == "assigned"
    assert second.status_code == 200
    assert second.json()["status"] == "unchanged"
    assert second.json()["event"] is None
    assert await event_count() == 1


async def test_stale_reassignment_is_refused(client, session, world, afribase):
    first = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    second = await registered_farmer(session, "second@example.com", "Bisi Ola")
    await sign_in(client, OPERATOR)
    # Both operators loaded the page while the original farmer owned the farm.
    assert (await assign(client, world.farm.id, first.id, world.farmer.id)).status_code == 200
    late = await assign(client, world.farm.id, second.id, world.farmer.id)
    assert late.status_code == 409
    assert "changed since you loaded it" in late.json()["detail"]
    assert await owner(world.farm.id) == first.id

    # A deliberate reassignment from the current owner is allowed and audited.
    moved = await assign(client, world.farm.id, second.id, first.id)
    assert moved.status_code == 200
    assert moved.json()["event"]["previous_farmer"] == "Tolu Ade"
    async with async_session_factory() as fresh:
        last = (await fresh.scalars(
            select(FarmAssignmentEvent).order_by(FarmAssignmentEvent.id.desc()))).first()
        assert last.previous_owner_kind == "registered"
    assert await event_count() == 2


async def test_concurrent_change_between_read_and_write_is_refused(
    client, session, world, afribase, monkeypatch
):
    first = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    second = await registered_farmer(session, "second@example.com", "Bisi Ola")
    real_blockers = farm_assignment.blockers

    async def race(db, farm_id):
        # Another request wins after this one read the farm but before it wrote.
        async with async_session_factory() as other:
            farm = await other.get(Farm, farm_id)
            farm.farmer_id = second.id
            await other.commit()
        return await real_blockers(db, farm_id)

    monkeypatch.setattr(farm_assignment, "blockers", race)
    await sign_in(client, OPERATOR)
    result = await assign(client, world.farm.id, first.id, world.farmer.id)
    assert result.status_code == 409
    assert "same time" in result.json()["detail"]
    assert await owner(world.farm.id) == second.id
    assert await event_count() == 0


async def test_reassignment_is_blocked_while_evidence_or_a_tranche_is_in_flight(
    client, session, world, afribase
):
    sponsorship = await start_and_confirm(SponsorshipAgent(), session, world)
    farmer = await registered_farmer(session, "newfarmer@example.com", "Tolu Ade")
    milestone_id = next(m["id"] for m in sponsorship["milestones"]
                        if m["status"] == "awaiting_evidence")
    check = PhotoCheck(
        farm_id=world.farm.id, milestone_id=milestone_id, stage_key="planted",
        crop_type="cassava", milestone_title="Planted", evidence_required="rows",
        evidence_sha256="a" * 64, media_type="image/jpeg", stored_name="x.jpg",
        status="needs_review", superseded=False, prompt_version="test",
        submitted_by_role="farmer", submitted_by_id=world.farmer.id,
    )
    session.add(check)
    await session.commit()

    await sign_in(client, OPERATOR)
    overview = (await client.get("/console/farm-assignments")).json()
    assert overview["farms"][0]["blockers"]
    blocked = await assign(client, world.farm.id, farmer.id, world.farmer.id)
    assert blocked.status_code == 409
    assert "still being checked" in blocked.json()["detail"]

    check.status = "approved"
    milestone = await session.get(SponsorshipMilestone, milestone_id)
    milestone.status = "needs_review"
    await session.commit()
    blocked = await assign(client, world.farm.id, farmer.id, world.farmer.id)
    assert blocked.status_code == 409
    assert "under review or waiting for payment" in blocked.json()["detail"]
    assert await owner(world.farm.id) == world.farmer.id
    assert await event_count() == 0
    # The evidence still belongs to the farmer who sent it.
    await session.refresh(check)
    assert check.submitted_by_id == world.farmer.id


async def test_no_registered_farmers_gives_a_useful_hint(client, world, afribase):
    await sign_in(client, OPERATOR)
    overview = (await client.get("/console/farm-assignments")).json()
    assert overview["farmers"] == []
    assert "sign in" in overview["farmers_hint"]
    # The chat-only demo farmer who owns the farm is not offered as a choice.
    assert overview["farms"][0]["farmer"]["registered"] is False
