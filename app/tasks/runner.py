"""Bridge for running async agent code inside synchronous Celery tasks.

Each task gets a fresh event loop via ``asyncio.run`` and a fresh DB session.
The async engine is disposed afterwards so its connection pool never leaks
across event loops (asyncpg pools are bound to the loop that created them).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, engine

T = TypeVar("T")


def run_task(coro_factory: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run ``coro_factory(session)`` to completion, committing on success.

    Usage:
        result = run_task(lambda s: agent.send_weekly_reports(s))
    """

    async def _runner() -> T:
        async with async_session_factory() as session:
            try:
                result = await coro_factory(session)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
            finally:
                await engine.dispose()

    return asyncio.run(_runner())
