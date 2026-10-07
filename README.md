# Yieldra

> **Sponsor a farm. Pay as it grows.**

Yieldra lets someone abroad sponsor a smallholder farm in Nigeria through PayPal,
with the money released in stages. The sponsor approves once. After that, each
tranche is charged only when the farmer sends a photo and an AI agent verifies that
the farm has really reached the next stage: planted, established, harvested.

No money sits in escrow, and no model can be talked into releasing a payment: the
AI judges the photo, fixed rules in code decide, and PayPal moves the money.

## How a sponsorship runs

1. **Sponsor starts.** `POST /sponsorships` with a farm and a total (USD). Yieldra
   splits it into four tranches (20 / 30 / 30 / 20 %) and returns a PayPal approval link.
2. **Sponsor approves on PayPal.** The first tranche is captured and the sponsor's
   PayPal account is saved (PayPal Vault) for the later ones.
3. **Farmer sends proof.** The farmer sends a photo to the Telegram bot with the
   caption `PROOF`.
4. **Milestone Verification Agent judges the photo** against the stage's evidence
   requirement and returns a structured verdict with a confidence score.
5. **Policy decides** (`app/agents/milestone_agent.py::decide`):
   - confident pass → the next tranche is charged to the saved PayPal account
   - unsure → held for a human reviewer
   - fail → nothing is charged; the farmer is told what the photo must show
6. **Both sides are told.** The sponsor gets a short update written from what the
   verified photo showed; the farmer gets a message in their language.
7. After the last tranche the saved PayPal account is deleted.

### Safeguards

| Risk | Guard |
|---|---|
| Model talked into approving | The model only fills a verdict; charging is plain code behind fixed thresholds |
| Same photo reused, or resubmitted until it passes | Every judged photo is fingerprinted (SHA-256) and refused a second time |
| Double charge on retry or crash | Every PayPal call carries an idempotency key (`PayPal-Request-Id`) |
| Charge succeeds, then something else fails | The charge is committed before any message or model call |
| Model or PayPal outage | Nothing is released; the tranche stays open and can be retried |
| Unsure verdict | Held for a person (`POST /sponsorships/milestones/{id}/review`) |

## PayPal integration

`app/tools/paypal.py` calls the PayPal REST APIs directly:

| API | Used for |
|---|---|
| OAuth 2 client credentials | Access token, cached |
| Orders v2 — create, capture | First tranche, approved by the sponsor |
| Vault with purchase (`store_in_vault: ON_SUCCESS`) | Saving the sponsor's PayPal account |
| Orders v2 with `vault_id` | Charging later tranches with no sponsor present |
| Payment Method Tokens v3 — delete | Removing the saved account on completion or cancel |

Your PayPal app needs **Save payment methods (Vault)** switched on.

## AI

Agents run on Claude through the Anthropic API by default (`LLM_PROVIDER=anthropic`).
`LLM_BASE_URL` can point at an Anthropic-compatible gateway, and
`LLM_PROVIDER=openai` runs the same agents on any OpenAI-compatible endpoint (Qwen).

| Agent | Role |
|---|---|
| Milestone Verification | Judges farm photos against a stage's evidence requirement (vision, structured output) |
| Sponsorship | Runs the flow above; writes sponsor updates and translates farmer messages |
| Farmer Advisory | Crop advice in Yoruba, Pidgin, Hausa or English, with weather tools |
| Crop Vision | Diagnoses crop disease from a photo |
| Harvest Logistics | Cold storage and truck coordination |
| Offtake Matching | Buyer matching and contract PDFs |
| Portfolio Report | Weekly summaries |
| Investment | Naira investment records and payouts (Paystack) |

## Stack

- **Backend:** Python 3.11+ / FastAPI (async), SQLAlchemy 2.0, PostgreSQL 15
- **Background work:** Celery + Redis
- **Payments:** PayPal (USD, sponsors) and Paystack (naira leg)
- **Channel:** Telegram Bot API

## Quick start

```bash
cp .env.example .env          # runs in mock mode until you add keys
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed_db.py
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs. Or run everything with `docker compose up`.

### Try a sponsorship

```bash
# 1. start: returns approve_url
curl -s -X POST localhost:8000/sponsorships -H 'content-type: application/json' \
  -d '{"sponsor_id": 9, "farm_id": 3, "total_usd": 250}'

# 2. open approve_url in a browser and approve
#    (mock mode skips PayPal's page and lands on the confirmation directly)

# 3. submit the farmer's photo (or send it to the Telegram bot captioned PROOF)
curl -s -X POST localhost:8000/sponsorships/farms/3/evidence \
  -H 'content-type: application/json' -H 'X-Admin-Key: <SECRET_KEY>' \
  -d '{"photo_url": "https://example.com/farm.jpg"}'

# 4. see the result
curl -s localhost:8000/sponsorships/1
```

Use `GET /users?role=sponsor` and `GET /farms` for real ids after seeding.

### Going from mock to real

| To switch on | Set in `.env` |
|---|---|
| Claude | `LLM_API_KEY` (then run `python scripts/check_llm.py`) |
| PayPal sandbox | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` from developer.paypal.com |
| PayPal redirect | `PUBLIC_BASE_URL` — where this API is reachable from the sponsor's browser |
| Telegram | `TELEGRAM_BOT_TOKEN` |
| Operator endpoints | `SECRET_KEY` — sent as `X-Admin-Key` to review, retry, cancel and submit evidence |

Photo verification always needs a working model key; PayPal, Paystack and Telegram
fall back to mocks while their keys are placeholders.

## Tests

```bash
pytest
```

The tests cover the Claude tool loop, the PayPal requests, the release policy and the
whole sponsorship flow. They use SQLite and never call an outside service. To run
them on PostgreSQL, set `TEST_DATABASE_URL` to a throwaway database.

## Not built yet

- Paying the farmer the naira value of each released tranche (Paystack leg)
- A sponsor-facing web page: today the flow is the API plus a confirmation page
- PayPal webhooks (captures are confirmed on the sponsor's return instead)
- Sponsor self-service cancel (cancel is an operator endpoint)

## History

Yieldra began as an entry for the Global AI Hackathon Series with Qwen Cloud
(fractional farm investment with an autonomous supply chain). The PayPal sponsorship
flow, the Claude agent layer, milestone verification and the test suite were added
for *Build What's Next with PayPal and AI*.

## License

MIT — see [LICENSE](LICENSE).
