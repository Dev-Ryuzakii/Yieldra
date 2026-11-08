"""Afribase-backed sessions link verified identities without crossing role boundaries."""

import httpx
import pytest
from sqlalchemy import select

from app.main import app
from app.models.sponsorship import Sponsorship
from app.models.user import User, UserRole
from app.routers import auth

FARMER_UID = "d85b5168-b6dd-443b-8189-6009ecd1c80a"


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
        if path.startswith("signup"):
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

    assert (await client.get("/account/session")).json() == {"authenticated": False}
    login = await client.post("/account/login", json={"email": "farmer@example.com", "password": "correct-password"})
    assert login.status_code == 200
    assert login.json()["profile"]["role"] == "farmer"
    assert "httponly" in login.headers["set-cookie"].lower()
    assert (await client.get("/account/session")).json()["profile"]["role"] == "farmer"
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/account/session")).json()["authenticated"] is True
    farmer = (await client.get("/account/dashboard")).json()
    assert farmer["profile"]["role"] == "farmer"
    assert [item["title"] for item in farmer["items"]] == [world.farm.name]
    assert "ONLY-SPONSOR" not in str(farmer)

    await client.post("/account/logout")
    assert (await client.get("/account/dashboard")).status_code == 401
    login = await client.post("/account/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert login.status_code == 200
    sponsor = (await client.get("/account/dashboard")).json()
    assert sponsor["profile"]["role"] == "sponsor"
    assert [item["href"] for item in sponsor["items"]] == ["/s/ONLY-SPONSOR"]

    await client.post("/account/logout")
    await client.post("/account/login", json={"email": "other@example.com", "password": "correct-password"})
    other = (await client.get("/account/dashboard")).json()
    assert other["profile"]["role"] == "sponsor"
    assert other["items"] == []


async def test_private_pages_and_reference_api_require_the_owner(client, session, world,
                                                                  fake_afribase):
    world.sponsor.email = "sponsor@example.com"
    session.add(Sponsorship(reference="OWNER-ONLY", farm_id=world.farm.id,
                            sponsor_id=world.sponsor.id, total_minor=10000))
    await session.commit()

    assert (await client.get("/dashboard", follow_redirects=False)).headers["location"] == "/signin?next=%2Fdashboard"
    assert (await client.get("/console", follow_redirects=False)).headers["location"] == "/signin?next=%2Fconsole"
    assert (await client.get("/s/OWNER-ONLY", follow_redirects=False)).headers["location"] == "/signin?next=%2Fs%2FOWNER-ONLY"
    assert (await client.get("/sponsorships/ref/OWNER-ONLY")).status_code == 401
    assert (await client.post("/sponsorships/ref/OWNER-ONLY/cancel")).status_code == 401

    await client.post("/account/login", json={"email": "other@example.com", "password": "correct-password"})
    assert (await client.get("/dashboard")).status_code == 200
    assert (await client.get("/console")).status_code == 403
    assert (await client.get("/s/OWNER-ONLY")).status_code == 403
    assert (await client.get("/sponsorships/ref/OWNER-ONLY")).status_code == 403
    assert (await client.post("/sponsorships/ref/OWNER-ONLY/cancel")).status_code == 403

    await client.post("/account/logout")
    await client.post("/account/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert (await client.get("/s/OWNER-ONLY")).status_code == 200
    assert (await client.get("/sponsorships/ref/OWNER-ONLY")).status_code == 200


async def test_checkout_needs_verified_sign_in(client, world, fake_afribase):
    payload = {"farm_id": world.farm.id, "amount": 100, "rail": "paypal",
               "name": "Guest", "email": "sponsor@example.com"}
    assert (await client.post("/sponsorships/checkout", json=payload)).status_code == 401
    await client.post("/account/login", json={"email": "other@example.com", "password": "correct-password"})
    assert (await client.post("/sponsorships/checkout", json=payload)).status_code == 403


async def test_unverified_email_cannot_link_or_sign_in(client, fake_afribase, session, world):
    fake_afribase["farmer@example.com"]["confirmed_at"] = None
    world.farmer.email = "farmer@example.com"
    await session.commit()
    result = await client.post("/account/login", json={"email": "farmer@example.com", "password": "correct-password"})
    assert result.status_code == 403
    assert "yieldra_session" not in client.cookies
    assert world.farmer.afribase_uid is None


async def test_operator_allowlist_is_server_controlled(client, fake_afribase, live_setting):
    live_setting(operator_emails="other@example.com", secret_key="test-secret")
    await client.post("/account/login", json={"email": "other@example.com", "password": "correct-password"})
    me = (await client.get("/account/me")).json()
    assert me["role"] == "operator"
    assert (await client.get("/console/ledger")).status_code == 200
    await client.post("/account/logout")
    await client.post("/account/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert (await client.get("/console/ledger")).status_code == 403


async def test_farmer_signup_is_stored_as_farmer_and_cannot_request_operator(client, fake_afribase, monkeypatch):
    captured: dict = {}

    async def call_with_metadata(method, path, *, body=None, token=None):
        if path.startswith("signup"):
            captured["path"] = path
            captured["data"] = body["data"]
            return {"access_token": "farmer-token", "refresh_token": "farmer-refresh", "expires_in": 3600}
        if path == "user":
            return {"id": "new-farmer", "email": "ada.farm@example.com", "confirmed_at": "2026-01-01T00:00:00Z",
                    "user_metadata": {"name": "Ada Farm", "yieldra_role": captured["data"]["yieldra_role"]}}
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call_with_metadata)
    result = await client.post("/account/register", json={
        "name": "Ada Farm", "email": "ada.farm@example.com", "password": "long-password", "account": "farmer",
    })
    assert result.status_code == 200
    assert result.json()["profile"]["role"] == "farmer"
    assert captured["data"] == {"name": "Ada Farm", "yieldra_role": "farmer"}
    assert "redirect_to=" in captured["path"]
    refused = await client.post("/account/register", json={
        "name": "Ada Farm", "email": "ada.farm@example.com", "password": "long-password", "account": "operator",
    })
    assert refused.status_code == 422
    extra = await client.post("/account/register", json={
        "name": "Ada Farm", "email": "ada.farm@example.com", "password": "long-password", "role": "operator",
    })
    assert extra.status_code == 422


async def test_existing_farmer_keeps_role_when_metadata_says_sponsor(client, session, world, fake_afribase):
    world.farmer.email = "farmer@example.com"
    fake_afribase["farmer@example.com"]["user_metadata"] = {"yieldra_role": "sponsor"}
    await session.commit()
    login = await client.post("/account/login", json={"email": "farmer@example.com", "password": "correct-password"})
    assert login.status_code == 200
    assert login.json()["profile"]["role"] == "farmer"


async def test_registration_needs_confirmation(client, fake_afribase):
    result = await client.post("/account/register", json={"name": "Ada Okafor", "email": "ada@example.com", "password": "long-password"})
    assert result.status_code == 200
    assert result.json() == {"status": "check_email"}
    assert "yieldra_session" not in client.cookies


async def test_allowlisted_operator_can_register_and_enter_console(client, fake_afribase,
                                                                    live_setting, monkeypatch):
    live_setting(operator_emails="other@example.com", secret_key="test-secret")

    async def call(method, path, *, body=None, token=None):
        if path.startswith("signup"):
            assert body["email"] == "other@example.com"
            assert body["data"]["yieldra_role"] == "sponsor"
            assert "redirect_to=" in path and path.endswith("%2Fsignin")
            return {"access_token": "operator-token", "refresh_token": "operator-refresh",
                    "expires_in": 3600}
        if path == "user":
            assert token == "operator-token"
            return fake_afribase["other@example.com"]
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    result = await client.post("/account/register", json={"name": "Test Operator",
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


async def test_dashboard_code_links_telegram_chat_once(client, fake_afribase, world, sent, live_setting):
    live_setting(telegram_bot_username="YieldraBot")
    await client.post("/account/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    assert (await client.get("/account/dashboard")).json()["telegram"] == {
        "linked": False, "bot_url": "https://t.me/YieldraBot"}

    issued = (await client.post("/account/telegram-link")).json()
    code = issued["code"]
    assert code.startswith("YLD-") and len(code) == 10
    assert issued["bot_url"] == f"https://t.me/YieldraBot?start={code}"

    # Someone who messaged the bot first has a chat-only account; the web account takes over the chat.
    await client.post("/webhook/telegram", json={"chat": "5555", "text": "sponsor"})
    linked = await client.post("/webhook/telegram", json={"chat": "5555", "text": f"/start {code.lower()}"})

    assert linked.json()["result"]["status"] == "linked"
    assert "Linked!" in sent[-2][1] and sent[-1][1] == "Yieldra: You have no active farm sponsorships yet."
    assert (await client.get("/account/dashboard")).json()["telegram"]["linked"] is True
    again = await client.post("/webhook/telegram", json={"chat": "9999", "text": code})
    assert again.json()["result"]["status"] == "invalid_code"


async def test_a_chat_linked_to_one_web_account_is_not_taken_by_another(client, fake_afribase, world, sent):
    await client.post("/account/login", json={"email": "sponsor@example.com", "password": "correct-password"})
    first = (await client.post("/account/telegram-link")).json()["code"]
    await client.post("/webhook/telegram", json={"chat": "7070", "text": first})
    await client.post("/account/logout")
    await client.post("/account/login", json={"email": "other@example.com", "password": "correct-password"})
    second = (await client.post("/account/telegram-link")).json()["code"]

    taken = await client.post("/webhook/telegram", json={"chat": "7070", "text": second})

    assert taken.json()["result"]["status"] == "chat_taken"


def _farmer_identity(**metadata):
    return {
        "id": FARMER_UID,
        "email": "role-sync@example.com",
        "confirmed_at": "2026-01-01T00:00:00Z",
        "user_metadata": metadata,
    }


async def test_confirmed_farmer_metadata_corrects_a_linked_sponsor_row(
    client, session, monkeypatch, live_setting
):
    live_setting(afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    """The reported account: Afribase says farmer, the linked Yieldra row is still sponsor."""
    session.add(User(
        name="New Farmer", phone="sync-farmer-uid", email="role-sync@example.com",
        afribase_uid=FARMER_UID, role=UserRole.sponsor,
    ))
    await session.commit()
    identity = _farmer_identity(name="New Farmer", yieldra_role="farmer")

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            return {"access_token": "farmer-token", "refresh_token": "farmer-refresh", "expires_in": 3600}
        if path == "user":
            return identity
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    login = await client.post("/account/login", json={
        "email": "role-sync@example.com", "password": "correct-password",
    })
    assert login.json()["profile"]["role"] == "farmer"
    dashboard = (await client.get("/account/dashboard")).json()
    assert dashboard["profile"]["role"] == "farmer"
    assert dashboard["items"] == []
    session.expire_all()
    stored = (await session.execute(select(User).where(User.afribase_uid == FARMER_UID))).scalar_one()
    assert stored.role == UserRole.farmer


async def test_stale_session_picks_up_farmer_metadata_without_signing_in_again(
    client, session, monkeypatch, live_setting
):
    live_setting(afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    identity = _farmer_identity()

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            return {"access_token": "farmer-token", "refresh_token": "farmer-refresh", "expires_in": 3600}
        if path == "user":
            return identity
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    first = await client.post("/account/login", json={
        "email": "role-sync@example.com", "password": "correct-password",
    })
    assert first.json()["profile"]["role"] == "sponsor"
    identity["user_metadata"] = {"yieldra_role": "farmer"}
    dashboard = (await client.get("/account/dashboard")).json()
    assert dashboard["profile"]["role"] == "farmer"
    me = (await client.get("/account/me")).json()
    assert me["role"] == "farmer"


async def test_sponsor_who_owns_a_sponsorship_is_not_rewritten(
    client, session, world, monkeypatch, live_setting
):
    live_setting(afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    world.sponsor.email = "sponsor@example.com"
    world.sponsor.afribase_uid = "sponsor-uid"
    session.add(Sponsorship(
        reference="KEEP-OWNER", farm_id=world.farm.id, sponsor_id=world.sponsor.id, total_minor=10000,
    ))
    await session.commit()
    owner_id = world.sponsor.id

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            return {"access_token": "sponsor-token", "refresh_token": "sponsor-refresh", "expires_in": 3600}
        if path == "user":
            return {"id": "sponsor-uid", "email": "sponsor@example.com", "confirmed_at": "2026-01-01T00:00:00Z",
                    "user_metadata": {"yieldra_role": "farmer"}}
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    login = await client.post("/account/login", json={
        "email": "sponsor@example.com", "password": "correct-password",
    })
    assert login.json()["profile"]["role"] == "sponsor"
    session.expire_all()
    stored = await session.get(User, owner_id)
    sponsorship = (await session.execute(
        select(Sponsorship).where(Sponsorship.reference == "KEEP-OWNER")
    )).scalar_one()
    assert stored.role == UserRole.sponsor
    assert sponsorship.sponsor_id == owner_id


async def test_metadata_cannot_grant_or_rewrite_operator(client, session, live_setting, monkeypatch):
    live_setting(operator_emails="role-sync@example.com", secret_key="test-secret",
                 afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    session.add(User(
        name="Operator", phone="sync-operator", email="role-sync@example.com",
        afribase_uid=FARMER_UID, role=UserRole.sponsor,
    ))
    await session.commit()

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            return {"access_token": "op-token", "refresh_token": "op-refresh", "expires_in": 3600}
        if path == "user":
            return _farmer_identity(yieldra_role="operator")
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    login = await client.post("/account/login", json={
        "email": "role-sync@example.com", "password": "correct-password",
    })
    assert login.json()["profile"]["role"] == "operator"
    session.expire_all()
    stored = (await session.execute(select(User).where(User.afribase_uid == FARMER_UID))).scalar_one()
    assert stored.role == UserRole.sponsor


async def test_assigned_investor_is_not_changed_by_farmer_metadata(
    client, session, world, monkeypatch, live_setting
):
    live_setting(afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    world.sponsor.email = "investor@example.com"
    world.sponsor.role = UserRole.investor
    world.sponsor.afribase_uid = "investor-uid"
    await session.commit()

    async def call(method, path, *, body=None, token=None):
        if path == "token?grant_type=password":
            return {"access_token": "inv-token", "refresh_token": "inv-refresh", "expires_in": 3600}
        if path == "user":
            return {"id": "investor-uid", "email": "investor@example.com", "confirmed_at": "2026-01-01T00:00:00Z",
                    "user_metadata": {"yieldra_role": "farmer"}}
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    login = await client.post("/account/login", json={
        "email": "investor@example.com", "password": "correct-password",
    })
    assert login.json()["profile"]["role"] == "investor"


async def test_signup_uses_role_from_the_signup_user_when_the_follow_up_read_omits_it(
    client, session, monkeypatch, live_setting
):
    live_setting(afribase_url="https://example.afribase.dev", afribase_anon_key="anon")
    async def call(method, path, *, body=None, token=None):
        if path.startswith("signup"):
            return {
                "access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600,
                "user": {"id": FARMER_UID, "email": "role-sync@example.com",
                         "user_metadata": {"name": "Ada Farm", "yieldra_role": body["data"]["yieldra_role"]}},
            }
        if path == "user":
            return {"id": FARMER_UID, "email": "role-sync@example.com", "confirmed_at": "2026-01-01T00:00:00Z"}
        raise AssertionError(path)

    monkeypatch.setattr(auth, "_call", call)
    result = await client.post("/account/register", json={
        "name": "Ada Farm", "email": "role-sync@example.com", "password": "long-password", "account": "farmer",
    })
    assert result.json()["profile"]["role"] == "farmer"
    session.expire_all()
    stored = (await session.execute(select(User).where(User.afribase_uid == FARMER_UID))).scalar_one()
    assert stored.role == UserRole.farmer
