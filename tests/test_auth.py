"""Afribase-backed sessions link verified identities without crossing role boundaries."""

import httpx
import pytest

from app.main import app
from app.models.sponsorship import Sponsorship
from app.routers import auth


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


@pytest.fixture
def fake_afribase(monkeypatch, live_setting):
    live_setting(afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    users = {
        "farmer@example.com": {"id": "farmer-uid", "email": "farmer@example.com", "confirmed_at": "2026-01-01T00:00:00Z"},
        "sponsor@example.com": {"id": "sponsor-uid", "email": "sponsor@example.com", "confirmed_at": "2026-01-01T00:00:00Z"},
        "other@example.com": {"id": "other-uid", "email": "other@example.com", "confirmed_at": "2026-01-01T00:00:00Z"},
    }

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            email = body["email"]
            if email not in users or body["password"] != "correct-password":
                from fastapi import HTTPException
                raise HTTPException(401, "Invalid credentials")
            return {"access_token": email, "refresh_token": email, "expires_in": 3600}
        if path == "user":
            return users[token]
        if path == "signup":
            return {"id": "new-uid", "email": body["email"]}
        if path == "logout":
            return {}
        raise AssertionError(path)
    monkeypatch.setattr(auth, "_call", call)
    return users


async def test_login_uses_verified_email_and_role_scoped_dashboard(client, session, world, fake_afribase):
    world.farmer.email = "farmer@example.com"
    world.sponsor.email = "sponsor@example.com"
    session.add(Sponsorship(reference="ONLY-SPONSOR", farm_id=world.farm.id,
                            sponsor_id=world.sponsor.id, total_minor=10000))
    await session.commit()

    assert (await client.get("/auth/session")).json() == {"authenticated": False}
    login = await client.post("/auth/login", json={"email": "farmer@example.com", "password": "correct-password"})
    assert login.status_code == 200
    assert login.json()["profile"]["role"] == "farmer"
    assert "httponly" in login.headers["set-cookie"].lower()
    assert (await client.get("/auth/session")).json()["profile"]["role"] == "farmer"
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/auth/session")).json()["authenticated"] is True
    farmer = (await client.get("/auth/dashboard")).json()
    assert farmer["profile"]["role"] == "farmer"
    assert [item["title"] for item in farmer["items"]] == [world.farm.name]
    assert "ONLY-SPONSOR" not in str(farmer)

    await client.post("/auth/logout")
    assert (await client.get("/auth/dashboard")).status_code == 401
    login = await client.post("/auth/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert login.status_code == 200
    sponsor = (await client.get("/auth/dashboard")).json()
    assert sponsor["profile"]["role"] == "sponsor"
    assert [item["href"] for item in sponsor["items"]] == ["/s/ONLY-SPONSOR"]

    await client.post("/auth/logout")
    await client.post("/auth/login", json={"email": "other@example.com", "password": "correct-password"})
    other = (await client.get("/auth/dashboard")).json()
    assert other["profile"]["role"] == "sponsor"
    assert other["items"] == []


async def test_private_pages_and_reference_api_require_the_owner(client, session, world,
                                                                  fake_afribase):
    world.sponsor.email = "sponsor@example.com"
    session.add(Sponsorship(reference="OWNER-ONLY", farm_id=world.farm.id,
                            sponsor_id=world.sponsor.id, total_minor=10000))
    await session.commit()

    assert (await client.get("/dashboard", follow_redirects=False)).headers["location"] == "/auth?next=%2Fdashboard"
    assert (await client.get("/console", follow_redirects=False)).headers["location"] == "/auth?next=%2Fconsole"
    assert (await client.get("/s/OWNER-ONLY", follow_redirects=False)).headers["location"] == "/auth?next=%2Fs%2FOWNER-ONLY"
    assert (await client.get("/sponsorships/ref/OWNER-ONLY")).status_code == 401
    assert (await client.post("/sponsorships/ref/OWNER-ONLY/cancel")).status_code == 401

    await client.post("/auth/login", json={"email": "other@example.com", "password": "correct-password"})
    assert (await client.get("/dashboard")).status_code == 200
    assert (await client.get("/console")).status_code == 403
    assert (await client.get("/s/OWNER-ONLY")).status_code == 403
    assert (await client.get("/sponsorships/ref/OWNER-ONLY")).status_code == 403
    assert (await client.post("/sponsorships/ref/OWNER-ONLY/cancel")).status_code == 403

    await client.post("/auth/logout")
    await client.post("/auth/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert (await client.get("/s/OWNER-ONLY")).status_code == 200
    assert (await client.get("/sponsorships/ref/OWNER-ONLY")).status_code == 200


async def test_checkout_needs_verified_sign_in(client, world, fake_afribase):
    payload = {"farm_id": world.farm.id, "amount": 100, "rail": "paypal",
               "name": "Guest", "email": "sponsor@example.com"}
    assert (await client.post("/sponsorships/checkout", json=payload)).status_code == 401
    await client.post("/auth/login", json={"email": "other@example.com", "password": "correct-password"})
    assert (await client.post("/sponsorships/checkout", json=payload)).status_code == 403


async def test_unverified_email_cannot_link_or_sign_in(client, fake_afribase, session, world):
    fake_afribase["farmer@example.com"]["confirmed_at"] = None
    world.farmer.email = "farmer@example.com"
    await session.commit()
    result = await client.post("/auth/login", json={"email": "farmer@example.com", "password": "correct-password"})
    assert result.status_code == 403
    assert "yieldra_session" not in client.cookies
    assert world.farmer.afribase_uid is None


async def test_operator_allowlist_is_server_controlled(client, fake_afribase, live_setting):
    live_setting(operator_emails="other@example.com", secret_key="test-secret")
    await client.post("/auth/login", json={"email": "other@example.com", "password": "correct-password"})
    me = (await client.get("/auth/me")).json()
    assert me["role"] == "operator"
    assert (await client.get("/console/ledger")).status_code == 200
    await client.post("/auth/logout")
    await client.post("/auth/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert (await client.get("/console/ledger")).status_code == 403


async def test_registration_needs_confirmation(client, fake_afribase):
    result = await client.post("/auth/register", json={"name": "Ada Okafor", "email": "ada@example.com", "password": "long-password"})
    assert result.status_code == 200
    assert result.json() == {"status": "check_email"}
    assert "yieldra_session" not in client.cookies


async def test_allowlisted_operator_can_register_and_enter_console(client, fake_afribase,
                                                                    live_setting, monkeypatch):
    live_setting(operator_emails="other@example.com", secret_key="test-secret")

    async def call(method, path, *, body=None, token=None):
        if path == "signup":
            assert body["email"] == "other@example.com"
            return {"access_token": "operator-token", "refresh_token": "operator-refresh",
                    "expires_in": 3600}
        if path == "user":
            assert token == "operator-token"
            return fake_afribase["other@example.com"]
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    result = await client.post("/auth/register", json={"name": "Test Operator",
                                                       "email": "other@example.com",
                                                       "password": "long-password"})
    assert result.status_code == 200
    assert result.json()["status"] == "signed_in"
    assert result.json()["profile"]["role"] == "operator"
    assert "httponly" in result.headers["set-cookie"].lower()
    assert (await client.get("/console/ledger")).status_code == 200


async def test_operator_can_assign_existing_users_email(client, world):
    response = await client.put(f"/users/{world.farmer.id}/identity",
                                json={"email": "farmer@example.com"})
    assert response.status_code == 200
    assert response.json()["email"] == "farmer@example.com"
    duplicate = await client.put(f"/users/{world.sponsor.id}/identity",
                                 json={"email": "farmer@example.com"})
    assert duplicate.status_code == 409
