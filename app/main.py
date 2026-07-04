"""FastAPI application entrypoint."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.redis_client import close_redis
from app.telegram_poller import poll_forever
from app.routers import (
    farms,
    health,
    investments,
    logistics,
    offtake,
    reference,
    reports,
    users,
    webhooks,
)
from app.utils.logger import get_logger

log = get_logger("yieldra.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Yieldra starting (env=%s, version=%s)", settings.app_env, __version__)

    poller_task: asyncio.Task | None = None
    if settings.telegram_use_polling and settings.telegram_live:
        poller_task = asyncio.create_task(poll_forever())
        log.info("Telegram long-polling enabled")

    yield

    if poller_task is not None:
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    log.info("Yieldra shutdown complete")


app = FastAPI(
    title="Yieldra",
    description="AI-powered fractional farm investment and autonomous supply chain platform.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(users.router)
app.include_router(reference.router)
app.include_router(farms.router)
app.include_router(investments.router)
app.include_router(logistics.router)
app.include_router(offtake.router)
app.include_router(reports.router)
app.include_router(webhooks.router)

# Serve generated contract PDFs at /static/contracts/<file>.
os.makedirs(os.path.join("generated", "contracts"), exist_ok=True)
app.mount("/static", StaticFiles(directory="generated"), name="static")


@app.get("/")
async def root() -> dict:
    return {
        "name": "Yieldra",
        "tagline": "Invest in a farm. Let AI run it.",
        "version": __version__,
        "docs": "/docs",
    }
