"""Operator-only endpoints to see and change which registered farmer owns a farm."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.routers import photo_checks as _actors
from app.routers.auth import _same_origin
from app.services import farm_assignment
from app.services.farm_assignment import AssignmentError

router = APIRouter(prefix="/console", tags=["farm-assignments"])


class AssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    farmer_id: int = Field(gt=0)
    # The owner the operator saw when they chose. A mismatch means the screen is stale.
    expected_farmer_id: int = Field(gt=0)
    reason: str = Field(default="", max_length=2000)
    confirm: bool = False


async def operator(
    request: Request, response: Response, db: AsyncSession = Depends(get_session)
) -> dict:
    """Signed-in operator, operator key, or the local development placeholder."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        _same_origin(request)
    current = await _actors.resolve_actor(request, response, db)
    if current.get("role") != "operator":
        raise HTTPException(status_code=403, detail="operator access required")
    if current.get("id") is not None:
        via = "session"
    elif _actors._key_ok(request.headers.get("x-admin-key")):
        via = "operator_key"
    elif settings.is_development:
        via = "development"
    else:
        via = "operator_key"
    return {**current, "via": via}


@router.get("/farm-assignments")
async def farm_assignments(
    session: AsyncSession = Depends(get_session), _: dict = Depends(operator)
) -> dict:
    return await farm_assignment.overview(session)


@router.post("/farms/{farm_id}/assignment")
async def assign_farm(
    farm_id: int,
    body: AssignmentBody,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(operator),
) -> dict:
    if farm_id <= 0:
        raise HTTPException(status_code=404, detail="Farm not found.")
    try:
        return await farm_assignment.assign(
            session,
            farm_id=farm_id,
            farmer_id=body.farmer_id,
            expected_farmer_id=body.expected_farmer_id,
            reason=body.reason,
            confirm=body.confirm,
            actor=current,
        )
    except AssignmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
