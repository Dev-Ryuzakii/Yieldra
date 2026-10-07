"""Shared test setup.

Environment variables are set before any ``app`` module is imported so the tests
never touch a real database, PayPal, Telegram or model provider, whatever the
developer's own .env contains.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

_DB_FILE = os.path.join(tempfile.mkdtemp(prefix="yieldra-test-"), "test.sqlite3")
# SQLite by default. To run against PostgreSQL, point TEST_DATABASE_URL at a throwaway
# database: every test DROPS AND RECREATES all tables in it.
_DB_URL = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_DB_FILE}"
os.environ.update(
    {
        "APP_ENV": "development",
        "DATABASE_URL": _DB_URL,
        "LLM_PROVIDER": "anthropic",
        "LLM_API_KEY": "xxx",
        "LLM_BASE_URL": "",
        "PAYPAL_CLIENT_ID": "xxx",
        "PAYPAL_CLIENT_SECRET": "xxx",
        "PAYPAL_ENV": "sandbox",
        "PAYPAL_WEBHOOK_ID": "xxx",
        "TUAGO_SECRET_KEY": "xxx",
        "TUAGO_WEBHOOK_SECRET": "xxx",
        "TUAGO_PLATFORM_FEE_BPS": "0",
        "USD_NGN_RATE": "1500",
        "TELEGRAM_BOT_TOKEN": "xxx",
        "SECRET_KEY": "change-this-in-production",
        "PUBLIC_BASE_URL": "http://testserver",
    }
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.agents.base import BaseAgent  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, async_session_factory, engine  # noqa: E402
import app.models  # noqa: E402,F401  (registers every table)
from app.models.farm import Farm, FarmStatus  # noqa: E402
from app.models.user import Language, User, UserRole  # noqa: E402
from app.services import evidence_store  # noqa: E402
from app.tools import paypal, tuago  # noqa: E402

# A 1x1 PNG, used wherever a test needs "a photo".
PNG_1PX = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def photo(tag: str) -> str:
    """A distinct data-URL "photo" per tag (same tag -> same bytes -> same fingerprint)."""
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(f"photo-{tag}".encode()).decode()


@pytest_asyncio.fixture(autouse=True)
async def _clean_state(monkeypatch, tmp_path):
    """Fresh tables and no cached clients, tokens or mock payments for every test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    paypal.reset_state()
    tuago.reset_state()
    BaseAgent.reset_clients()
    # Farm photos go to a throwaway folder, never into the repository.
    monkeypatch.setattr(evidence_store, "_ROOT", str(tmp_path / "generated"))
    yield
    BaseAgent.reset_clients()


@pytest_asyncio.fixture
async def session():
    async with async_session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def world(session):
    """A Yoruba-speaking cassava farmer, her farm, and a sponsor abroad."""
    farmer = User(name="Adunni Okafor", phone="1001", role=UserRole.farmer,
                  language_preference=Language.yoruba)
    sponsor = User(name="Kemi Diaspora", phone="2001", role=UserRole.sponsor,
                   language_preference=Language.english)
    session.add_all([farmer, sponsor])
    await session.flush()
    farm = Farm(name="Adunni Cassava Plot", farmer_id=farmer.id, location="Ogun",
                crop_type="cassava", total_plots=10, available_plots=10,
                status=FarmStatus.listed)
    session.add(farm)
    await session.commit()
    return SimpleNamespace(farmer=farmer, sponsor=sponsor, farm=farm)


@pytest.fixture
def sent(monkeypatch):
    """Capture chat messages instead of sending them. Returns the list of (chat, text)."""
    messages: list[tuple[str, str]] = []

    async def fake_send(phone: str, message: str) -> dict:
        messages.append((phone, message))
        return {"ok": True}

    monkeypatch.setattr("app.services.notify.send_text_message", fake_send)
    monkeypatch.setattr("app.routers.webhooks.send_text_message", fake_send)
    return messages


@pytest_asyncio.fixture
async def banked(session, world):
    """Give the farmer a verified payout account (needed for naira sponsorships)."""
    from app.services import payouts

    result = await payouts.register_account(session, world.farmer, "058", "0123456789")
    await session.commit()
    assert result["status"] == "saved"
    return result


@pytest.fixture
def live_setting(monkeypatch):
    """Temporarily change settings, e.g. ``live_setting(paypal_client_id="real")``."""

    def _set(**values):
        for key, value in values.items():
            monkeypatch.setattr(settings, key, value)

    return _set


def make_verdict(confidence: float = 0.92, met: bool = True, crop: bool = True, scene: bool = True):
    from app.agents.milestone_agent import MilestoneVerdict

    return MilestoneVerdict(
        shows_farm_scene=scene,
        crop_matches=crop,
        milestone_met=met,
        confidence=confidence,
        observations="Rows of young cassava cuttings across a cleared plot.",
        concerns="" if met else "Only bare soil is visible.",
    )


@pytest.fixture
def verifier(monkeypatch):
    """Replace the model's photo judgement. Set ``.next`` / ``.error`` to steer it."""
    from app.agents.milestone_agent import MilestoneAgent

    state = SimpleNamespace(next=make_verdict(), error=None, calls=[])

    async def fake_verify(self, **kwargs):
        state.calls.append(kwargs)
        if state.error is not None:
            raise state.error
        return state.next

    monkeypatch.setattr(MilestoneAgent, "verify", fake_verify)
    return state
