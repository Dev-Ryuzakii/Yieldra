"""Afribase email identity and role-scoped web dashboard."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from typing import Annotated
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models.farm import Farm
from app.models.investment import Investment
from app.models.offtake import OfftakeContract
from app.models.sponsorship import Sponsorship
from app.models.user import User, UserRole
from app.services.notify import WEB_PREFIX

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "yieldra_session"
_DEV_COOKIE_KEY = secrets.token_bytes(32)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=120)


def _cipher() -> Fernet:
    material = (_DEV_COOKIE_KEY if settings.is_development and settings._is_placeholder(settings.secret_key)
                else settings.secret_key.encode())
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "cross-origin request refused")


def _configured() -> None:
    if not settings.afribase_ready:
        raise HTTPException(503, "Afribase auth is not configured")
    if not settings.is_development and settings._is_placeholder(settings.secret_key):
        raise HTTPException(503, "set a unique SECRET_KEY before enabling auth")


async def _call(method: str, path: str, *, body: dict | None = None, token: str | None = None) -> dict:
    _configured()
    headers = {"apikey": settings.afribase_anon_key,
               "Authorization": f"Bearer {token or settings.afribase_anon_key}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            result = await client.request(method, f"{settings.afribase_url.rstrip('/')}/auth/v1/{path}", json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Afribase auth is temporarily unavailable") from exc
    try:
        data = result.json()
    except ValueError:
        data = {}
    if result.status_code >= 400:
        if result.status_code in (400, 401, 422):
            raise HTTPException(401, "Invalid credentials or unconfirmed email")
        raise HTTPException(502, "Afribase auth request failed")
    return data if isinstance(data, dict) else {}


def _write_cookie(response: Response, payload: dict) -> None:
    encrypted = _cipher().encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()
    response.set_cookie(COOKIE, encrypted, httponly=True, secure=not settings.is_development,
                        samesite="lax", max_age=60 * 60 * 24 * 7, path="/")


async def current_identity(request: Request, response: Response) -> dict:
    _configured()
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, "Sign in to continue")
    try:
        session = json.loads(_cipher().decrypt(raw.encode(), ttl=60 * 60 * 24 * 7))
    except (InvalidToken, ValueError, TypeError):
        raise HTTPException(401, "Session expired. Please sign in again") from None
    if not isinstance(session, dict) or not session.get("access_token"):
        raise HTTPException(401, "Invalid session")
    if int(session.get("expires_at", 0)) <= int(time.time()) + 30:
        if not session.get("refresh_token"):
            raise HTTPException(401, "Session expired. Please sign in again")
        refreshed = await _call("POST", "token?grant_type=refresh_token", body={"refresh_token": session["refresh_token"]})
        session = {key: refreshed[key] for key in ("access_token", "refresh_token") if key in refreshed} | {
            "expires_at": int(time.time()) + int(refreshed.get("expires_in", 3600))}
        _write_cookie(response, session)
    user = await _call("GET", "user", token=session["access_token"])
    if not user.get("id") or not user.get("email"):
        raise HTTPException(401, "Invalid Afribase identity")
    if not (user.get("email_confirmed_at") or user.get("confirmed_at")):
        raise HTTPException(403, "Confirm your email before using the dashboard")
    return user


async def _profile(identity: dict, db: AsyncSession) -> dict:
    email = str(identity["email"]).strip().lower()
    uid = str(identity["id"])
    existing = (await db.execute(select(User).where(User.afribase_uid == uid))).scalar_one_or_none()
    if not existing:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing and existing.afribase_uid and existing.afribase_uid != uid:
            raise HTTPException(409, "This email is linked to another identity")
        if existing:
            existing.afribase_uid = uid
        else:
            existing = User(name=(identity.get("user_metadata") or {}).get("name") or email.split("@")[0],
                            phone=f"{WEB_PREFIX}{uuid.uuid4().hex[:16]}", email=email,
                            afribase_uid=uid, role=UserRole.sponsor)
            db.add(existing)
        await db.flush()
    elif existing.email and existing.email.lower() != email:
        raise HTTPException(409, "Account email changed. Contact an operator to relink it")
    operators = {item.strip().lower() for item in settings.operator_emails.split(",") if item.strip()}
    return {"id": existing.id, "name": existing.name, "email": email,
            "role": "operator" if email in operators else existing.role.value}


@router.post("/register")
async def register(payload: Registration, request: Request, response: Response,
                   db: AsyncSession = Depends(get_session)) -> dict:
    _same_origin(request)
    data = await _call("POST", "signup", body={"email": str(payload.email).lower(), "password": payload.password,
                                               "data": {"name": payload.name.strip()}})
    if data.get("access_token"):
        user = await _call("GET", "user", token=data["access_token"])
        if user.get("email_confirmed_at") or user.get("confirmed_at"):
            cookie = {key: data[key] for key in ("access_token", "refresh_token") if key in data} | {
                "expires_at": int(time.time()) + int(data.get("expires_in", 3600))}
            _write_cookie(response, cookie)
            return {"status": "signed_in", "profile": await _profile(user, db)}
    return {"status": "check_email"}


@router.post("/login")
async def login(payload: Credentials, request: Request, response: Response,
                db: AsyncSession = Depends(get_session)) -> dict:
    _same_origin(request)
    data = await _call("POST", "token?grant_type=password", body={"email": str(payload.email).lower(), "password": payload.password})
    user = await _call("GET", "user", token=data.get("access_token"))
    if not (user.get("email_confirmed_at") or user.get("confirmed_at")):
        raise HTTPException(403, "Confirm your email before signing in")
    profile = await _profile(user, db)
    data = {key: data[key] for key in ("access_token", "refresh_token") if key in data} | {
        "expires_at": int(time.time()) + int(data.get("expires_in", 3600))}
    _write_cookie(response, data)
    return {"status": "signed_in", "profile": profile}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    _same_origin(request)
    raw = request.cookies.get(COOKIE)
    if raw:
        try:
            session = json.loads(_cipher().decrypt(raw.encode(), ttl=60 * 60 * 24 * 7))
            await _call("POST", "logout", token=session.get("access_token"))
        except (InvalidToken, ValueError, HTTPException):
            pass
    response.delete_cookie(COOKIE, path="/")
    return {"status": "signed_out"}


@router.get("/me")
async def me(identity: Annotated[dict, Depends(current_identity)],
             db: AsyncSession = Depends(get_session)) -> dict:
    return await _profile(identity, db)


@router.get("/session")
async def session_status(request: Request, response: Response,
                         db: AsyncSession = Depends(get_session)) -> dict:
    """Allow public pages to check sign-in state without a guest 401."""
    if not request.cookies.get(COOKIE):
        return {"authenticated": False}
    try:
        identity = await current_identity(request, response)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        response.delete_cookie(COOKIE, path="/")
        return {"authenticated": False}
    return {"authenticated": True, "profile": await _profile(identity, db)}


@router.get("/dashboard")
async def dashboard(identity: Annotated[dict, Depends(current_identity)],
                    db: AsyncSession = Depends(get_session)) -> dict:
    profile = await _profile(identity, db)
    uid = profile["id"]
    role = profile["role"]
    items: list[dict] = []
    if role in ("sponsor", "operator"):
        stmt = select(Sponsorship, Farm).join(Farm, Sponsorship.farm_id == Farm.id).order_by(Sponsorship.id.desc())
        if role != "operator":
            stmt = stmt.where(Sponsorship.sponsor_id == uid)
        items = [{"id": s.id, "title": f.name, "subtitle": f"{f.crop_type} · {f.location}",
                  "status": s.status.value, "amount": s.total_minor / 100, "currency": s.currency,
                  "href": f"/s/{s.reference}"} for s, f in (await db.execute(stmt.limit(30))).all()]
    elif role == "farmer":
        farms = (await db.execute(select(Farm).where(Farm.farmer_id == uid).order_by(Farm.id.desc()))).scalars()
        items = [{"id": f.id, "title": f.name, "subtitle": f"{f.crop_type} · {f.location}",
                  "status": f.status.value} for f in farms]
    elif role == "investor":
        rows = (await db.execute(select(Investment, Farm).join(Farm).where(Investment.investor_id == uid))).all()
        items = [{"id": i.id, "title": f.name, "subtitle": f.crop_type, "status": i.status.value,
                  "amount": i.amount_ngn / 100, "currency": "NGN"} for i, f in rows]
    elif role == "buyer":
        rows = (await db.execute(select(OfftakeContract).where(OfftakeContract.buyer_id == uid))).scalars()
        items = [{"id": c.id, "title": f"Contract #{c.id}", "subtitle": f"{c.quantity_kg:,.0f} kg",
                  "status": c.status.value, "amount": c.total_value / 100, "currency": "NGN"} for c in rows]
    elif role == "logistics":
        items = []
    return {"profile": profile, "items": items}
