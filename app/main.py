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
    console,
    auth,
    farms,
    health,
    investments,
    logistics,
    media,
    offtake,
    pages,
    payouts,
    reference,
    reports,
    sponsorships,
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
    description="Sponsor a farm and pay as it grows: PayPal payments linked to verified farm milestones.",
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
app.include_router(media.router)
app.include_router(offtake.router)
app.include_router(reports.router)
app.include_router(sponsorships.router)
app.include_router(payouts.router)
app.include_router(console.router)
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(webhooks.router)

# Serve generated files: contract PDFs at /static/contracts/, farm photos at /static/evidence/.
os.makedirs(os.path.join("generated", "contracts"), exist_ok=True)
os.makedirs(os.path.join("generated", "evidence"), exist_ok=True)
app.mount("/static", StaticFiles(directory="generated"), name="static")
# Vite's compiled scripts, styles and bundled fonts.
app.mount("/assets", StaticFiles(directory=os.path.join(pages.DIST_DIR, "assets"), check_dir=False), name="assets")
