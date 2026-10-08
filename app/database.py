"""Async SQLAlchemy engine, session factory, and declarative base."""

from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_postgres = settings.database_url.startswith("postgresql+asyncpg://")
_pooled_afribase = _postgres and ":6543/" in settings.database_url
_database_url = settings.database_url
_engine_options: dict = {"echo": False, "pool_pre_ping": True}
if _postgres:
    # Hosted Postgres may put auth before public in search_path. Every Yieldra
    # domain table, including users, is in public.
    _engine_options["connect_args"] = {"server_settings": {"search_path": "public"}}
if _pooled_afribase:
    # Afribase's transaction pooler can hand successive statements to different
    # backends. Unique prepared-statement names avoid collisions between clients.
    _database_url += ("&" if "?" in _database_url else "?") + "prepared_statement_cache_size=0"
    _engine_options["poolclass"] = NullPool
    _engine_options["connect_args"].update({
        "prepared_statement_name_func": lambda: f"__yieldra_{uuid4().hex}__",
        "statement_cache_size": 0,
    })
engine = create_async_engine(_database_url, **_engine_options)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session and closes it after use."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
