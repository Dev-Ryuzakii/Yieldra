"""Authenticated milestone photo checks. Review does not move money."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.services.image_bytes import MAX_IMAGE_BYTES
from app.services.photo_checks import (
    PhotoCheckError,
    get_check,
    list_for_farm,
    queue_for_operator,
    read_photo,
    review_check,
    submit_photo,
)

router = APIRouter(tags=["photo-checks"])


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(approve|reject|request_new_photo)$")
    rationale: str = Field(min_length=15, max_length=1000)


def _http(exc: PhotoCheckError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _key_ok(header: str | None) -> bool:
    if not header or settings._is_placeholder(settings.secret_key):
        return False
    try:
        return hmac.compare_digest(header, settings.secret_key)
    except ValueError:
        return False


async def resolve_actor(request: Request, response: Response, db: AsyncSession) -> dict:
    """Operator key, development placeholder, or the signed-in Afribase user."""
    if settings.is_development and settings._is_placeholder(settings.secret_key):
        return {"id": None, "role": "operator", "email": None}
    if _key_ok(request.headers.get("x-admin-key")):
        return {"id": None, "role": "operator", "email": None}
    if not settings.afribase_ready:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    from app.routers.auth import _profile, current_identity

    identity = await current_identity(request, response)
    profile = await _profile(identity, db)
    return {"id": profile["id"], "role": profile["role"], "email": profile["email"]}


async def actor(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> dict:
    return await resolve_actor(request, response, db)


def _operator(current: dict) -> None:
    if current.get("role") != "operator":
        raise HTTPException(status_code=403, detail="operator access required")


@router.get("/farms/{farm_id}/photo-checks")
async def farm_photo_checks(
    farm_id: int,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(actor),
) -> dict:
    try:
        return await list_for_farm(session, farm_id, current)
    except PhotoCheckError as exc:
        raise _http(exc) from exc


@router.post("/farms/{farm_id}/photo-checks", status_code=201)
async def create_photo_check(
    farm_id: int,
    file: UploadFile = File(...),
    milestone_id: int | None = None,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(actor),
) -> dict:
    image = await file.read(MAX_IMAGE_BYTES + 1)
    await file.close()
    try:
        return await submit_photo(
            session,
            farm_id=farm_id,
            actor=current,
            image=image,
            milestone_id=milestone_id,
        )
    except PhotoCheckError as exc:
        raise _http(exc) from exc


@router.get("/console/photo-checks")
async def operator_queue(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(actor),
) -> dict:
    _operator(current)
    return await queue_for_operator(session)


@router.get("/photo-checks/{check_id}")
async def one_photo_check(
    check_id: int,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(actor),
) -> dict:
    try:
        return await get_check(session, check_id, current)
    except PhotoCheckError as exc:
        raise _http(exc) from exc


@router.get("/photo-checks/{check_id}/photo")
async def photo_file(
    check_id: int,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(actor),
) -> Response:
    try:
        data, media_type = await read_photo(session, check_id, current)
    except PhotoCheckError as exc:
        raise _http(exc) from exc
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(
        media_type, "img"
    )
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="evidence-{check_id}.{extension}"',
        },
    )


@router.post("/photo-checks/{check_id}/review")
async def review_photo_check(
    check_id: int,
    body: ReviewBody,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(actor),
) -> dict:
    try:
        return await review_check(session, check_id, current, body.action, body.rationale)
    except PhotoCheckError as exc:
        raise _http(exc) from exc
