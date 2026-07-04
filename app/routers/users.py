"""User CRUD — create + list farmers, investors, buyers, logistics operators."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=201)
async def create_user(
    payload: UserCreate, session: AsyncSession = Depends(get_session)
) -> User:
    exists = await session.execute(select(User).where(User.phone == payload.phone))
    if exists.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="phone already registered")
    user = User(**payload.model_dump())
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


@router.get("", response_model=list[UserRead])
async def list_users(
    role: UserRole | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    stmt = select(User).order_by(User.id)
    if role is not None:
        stmt = stmt.where(User.role == role)
    result = await session.execute(stmt)
    return list(result.scalars())


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: int, session: AsyncSession = Depends(get_session)) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user
