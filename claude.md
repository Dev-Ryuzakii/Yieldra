# CLAUDE.md — Yieldra

> **Sponsor a farm. Pay as it grows.**
> Entry for *Build What's Next with PayPal and AI*. Began as a Qwen Cloud hackathon
> project (fractional farm investment); that code is still here and still works.

Read `README.md` for what the product does and `docs/architecture.md` for how the
pieces fit. This file is the working guide for changing the code.

## What matters most

A sponsor pays for a farm in four tranches. Tranches after the first are collected
only when a farmer's photo passes verification. Two rails: PayPal (USD, saved
account charged automatically) and Tuago (NGN, sponsor pays a checkout per stage).
Farmers are paid in naira through Tuago split settlement.

## Rules that must not be broken

- **No model moves money.** Models judge photos and write messages. Collecting is
  plain code behind `app/agents/milestone_agent.py::decide`. Payment tools exposed to
  models are read-only. Never let model output choose an amount, a recipient or a URL.
- **Commit a payment before anything else can fail.** After a successful charge or a
  confirmed payment, `await session.commit()` before messages or model calls.
- **Every PayPal write carries an idempotency key** (`PayPal-Request-Id`), built by
  `SponsorshipAgent._payment_reference`. The attempt number advances only when PayPal
  definitively refuses; a timeout reuses the key.
- **Check a provider's word with the provider.** Webhooks are signature-verified, and
  then the status and amount are re-read before a tranche or payout is marked paid.
- **Notifications never raise.** Use `app/services/notify.py::notify`.
- **Money is integers in minor units** (US cents, kobo). Format only at the edges
  (`app/utils/money.py`).
- **Never return secrets or full account numbers**: no PayPal vault id, no farmer
  account number, in any response or log.
- **Every external tool has a mock mode** used while its key is a placeholder
  (`settings.paypal_live`, `tuago_live`, `telegram_live`). Model calls are not mocked.
- **Never put real keys in `.env.example`**; it is committed.
- Chat messages are short (about three sentences); farmers read on basic phones. Agent
  system prompts say "Respond in the user's language".

## Layout

```
app/
  main.py                 FastAPI app, routers, static mounts
  config.py               Settings (pydantic-settings); provider and model defaults
  agents/
    base.py               BaseAgent: Anthropic + OpenAI-compatible, tool loop, run_structured
    milestone_agent.py    Photo verdict + decide() release policy
    sponsorship_agent.py  The sponsorship flow on both rails
    advisory / vision / logistics / offtake / report / investment agents
  tools/
    paypal.py             Orders v2, Vault, payment tokens, webhook verification
    tuago.py              Banks, subaccounts, checkout sessions, charge verify, signatures
    telegram.py, weather.py, cold_storage.py, contracts.py
  services/
    milestones.py         Tranche plan and evidence wording per crop; amount limits
    payouts.py            Farmer payout accounts (Tuago subaccounts)
    disbursements.py      Naira owed to farmers for PayPal tranches
    naira_payments.py     Routes a Tuago payment notice to a tranche or a disbursement
    evidence_store.py     Stored copies of judged photos
    notify.py             Chat messages with translation; never raises
  routers/
    sponsorships.py       Start, sponsor's own page API, provider returns, operator actions
    payouts.py            Banks, payout accounts, disbursements
    console.py            Ledger for the operator console
    pages.py              /, /s/{reference}, /console, /meta, mock Tuago checkout
    webhooks.py           Telegram, Tuago, PayPal
    deps.py               require_operator (X-Admin-Key)
  models/                 SQLAlchemy models; sponsorship.py and payout.py are the new ones
  web/                    Static pages (no build step): index, track, console + assets/
migrations/versions/      Alembic; add a revision for every model change
tests/                    pytest; SQLite by default, PostgreSQL with TEST_DATABASE_URL
scripts/                  seed_db.py, check_llm.py
render.yaml               Render blueprint
```

## Commands

```bash
cp .env.example .env
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed_db.py
uvicorn app.main:app --reload        # http://localhost:8000

pytest                               # must stay green
python scripts/check_llm.py          # checks LLM_API_KEY / LLM_BASE_URL
alembic revision --autogenerate -m "..."   # then review it by hand
```

## Working conventions

- All database work is async (SQLAlchemy 2.0). Do not rely on lazy-loaded
  relationships; query explicitly.
- Adding a value to a PostgreSQL enum needs `ALTER TYPE ... ADD VALUE` in the
  migration; autogenerate will not write it.
- New behaviour gets a test. Payment tests assert the exact request sent to the
  provider and the state afterwards; see `tests/test_paypal.py`, `tests/test_tuago.py`.
- The web pages insert API data as text, never as HTML (`h()` in
  `app/web/assets/common.js`).
- Tuago has no API for sending money. Do not add code that assumes one.

## Known gaps

- Funding a farmer disbursement is a manual bank transfer (shown in the console).
- Naira investor returns are queued for manual transfer.
- No email to web sponsors; their updates are on their page.
- Photos are stored on local disk.
- Nothing has been run against live PayPal, Tuago or Claude from this repository's
  tests; they use fakes shaped like the real APIs.
