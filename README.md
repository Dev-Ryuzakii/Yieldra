# Yieldra

> **Sponsor a farm. Pay as it grows.**

Yieldra lets someone sponsor a smallholder farm in Nigeria and pay in stages. The
first part pays for seed and land. Each part after that is collected only when the
farmer sends a photo and an AI agent verifies that the farm has really reached the
next stage: planted, established, harvested.

- **From abroad, in dollars, with PayPal.** The sponsor approves once; later parts are
  charged to their saved PayPal account when a stage is verified.
- **From Nigeria, in naira, with Tuago.** The sponsor pays each part by bank transfer
  in WhatsApp when a stage is verified.
- **The farmer is paid in naira to their own bank account**, verified by the bank.

No model can release a payment: the AI judges the photo, fixed rules in code decide,
and the payment provider moves the money.

## How a sponsorship runs

1. **Sponsor picks a farm** on the web page, chooses PayPal or bank transfer, and
   enters a total. Yieldra splits it into four tranches (20 / 30 / 30 / 20 %).
2. **First tranche is paid.** PayPal: the sponsor approves, the tranche is captured
   and their account is saved (PayPal Vault). Tuago: the sponsor pays a checkout.
3. **Farmer sends proof.** A photo to the Telegram bot with the caption `PROOF`.
4. **Milestone Verification Agent judges the photo** against the stage's evidence
   requirement and returns a structured verdict with a confidence score.
5. **Policy decides** (`app/agents/milestone_agent.py::decide`):
   - confident pass → PayPal: the tranche is charged. Tuago: the sponsor gets a link to pay
   - unsure → held for a human reviewer in the operator console
   - fail → nothing is collected; the farmer is told what the photo must show
6. **The farmer is paid** (see below) and the sponsor's page shows the photo, what the
   check saw, and an update written from it.
7. After the last tranche the saved PayPal account is deleted. The sponsor can also
   stop at any time from their page.

### How the farmer gets the money

Tuago collects money but has no API to send it, so farmers are paid by Tuago's *split
settlement*: the farmer's bank account is registered as a Tuago subaccount (Tuago
verifies the account name), and collections routed through it settle to that bank.

| Sponsor paid with | Farmer is paid by |
|---|---|
| Tuago (naira) | The sponsor's own payment, routed through the farmer's subaccount |
| PayPal (dollars) | A *disbursement*: Yieldra transfers the naira value into a Tuago checkout routed to the farmer's subaccount. Tuago confirms the transfer and settles it |

The disbursement step is a bank transfer Yieldra makes; the console lists what to pay
and where. The rate is `USD_NGN_RATE`. Farmers add their account by chat:
`BANKS` lists codes, `BANK <bank code> <account number>` saves it.

### Safeguards

| Risk | Guard |
|---|---|
| Model talked into approving | The model only fills a verdict; collecting is plain code behind fixed thresholds |
| Same photo reused, or resubmitted until it passes | Every judged photo is fingerprinted (SHA-256) and refused a second time |
| Double charge on retry or crash | Every PayPal call carries an idempotency key (`PayPal-Request-Id`) |
| Forged or replayed payment notice | Webhooks are signature-checked, then the amount and status are re-read from the provider |
| Payment succeeds, then something else fails | The payment is committed before any message or model call |
| Model, PayPal or Tuago outage | Nothing is released; the tranche stays open and can be retried |
| Unsure verdict | Held for a person |
| Paying the wrong person | Bank account names are verified by the bank; account numbers are never echoed back |

## The pages

| Address | For | What it does |
|---|---|---|
| `/` | Sponsors | Farms, how the money is split, and the form to start |
| `/s/<reference>` | One sponsor | Their private page: stages, verified photos, updates, pay and stop buttons |
| `/console` | Operators | AG Grid ledger of every tranche and farmer payout, review and retry actions, photo upload |
| `/docs` | Developers | The JSON API |

The pages are static files in `app/web/` served by the API, with no build step.
AG Grid Community and the two typefaces are bundled, so nothing loads from a CDN.

## PayPal integration

`app/tools/paypal.py` calls the PayPal REST APIs directly:

| API | Used for |
|---|---|
| OAuth 2 client credentials | Access token, cached |
| Orders v2 — create, capture | First tranche, approved by the sponsor |
| Vault with purchase (`store_in_vault: ON_SUCCESS`) | Saving the sponsor's PayPal account |
| Orders v2 with `vault_id` | Charging later tranches with no sponsor present |
| Payment Method Tokens v3 — delete | Removing the saved account on completion or cancel |
| Webhooks + verify-webhook-signature | Completing an approval the sponsor never returned from; late payment tokens; refunds |

The PayPal app needs **Save payment methods (Vault)** switched on.

## Tuago integration

`app/tools/tuago.py`, written against the public docs and the `@tuago/node` SDK:

| API | Used for |
|---|---|
| `GET /v1/banks`, `POST /v1/subaccounts` | Registering and verifying a farmer's bank account |
| `POST /v1/whatsapp/checkout/sessions` (with `subaccount`) | Naira tranches and farmer disbursements |
| `.../pay/bank-transfer`, `GET .../sessions/{id}` | The account to pay into; confirming a payment |
| `.../simulate` | Test-mode payments |
| `POST /v1/charges/{reference}/verify` | Naira investment payments |
| Webhooks, `X-Ollie-Signature` (HMAC-SHA512) | `charge.*` events |

## AI

Agents run on Claude through the Anthropic API by default (`LLM_PROVIDER=anthropic`).
`LLM_BASE_URL` can point at an Anthropic-compatible gateway, and
`LLM_PROVIDER=openai` runs the same agents on any OpenAI-compatible endpoint (Qwen).

| Agent | Role |
|---|---|
| Milestone Verification | Judges farm photos against a stage's evidence requirement (vision, structured output) |
| Sponsorship | Runs the flow above and writes sponsor updates |
| Farmer Advisory | Crop advice in Yoruba, Pidgin, Hausa or English, with weather tools |
| Crop Vision | Diagnoses crop disease from a photo |
| Harvest Logistics | Cold storage and truck coordination |
| Offtake Matching | Buyer matching and contract PDFs |
| Portfolio Report | Weekly summaries |
| Investment | Naira investment records, verified through Tuago |

System messages to farmers are translated into their language by the model.

## Stack

- **Backend:** Python 3.11+ / FastAPI (async), SQLAlchemy 2.0, PostgreSQL 15
- **Background work:** Celery + Redis
- **Payments:** PayPal (USD) and Tuago (NGN)
- **Channels:** web pages and the Telegram Bot API
- **Console grid:** AG Grid Community

## Quick start

```bash
cp .env.example .env          # runs in mock mode until you add keys
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed_db.py
uvicorn app.main:app --reload
```

Open http://localhost:8000. Or run everything with `docker compose up`.

### Walk through it in mock mode

1. On `/`, choose a farm and sponsor it with PayPal. Mock mode skips PayPal's page
   and lands on your sponsorship page with the first part paid.
2. On `/console`, save a bank account for the farmer, then add a farm photo.
   Checking a photo needs a real `LLM_API_KEY`; everything else works without keys.
3. Reload your sponsorship page to see the photo, the update and the next stage.
4. Sponsor the same farm by bank transfer to see the naira flow.

### Going from mock to real

| To switch on | Set in `.env` |
|---|---|
| Claude | `LLM_API_KEY` (then run `python scripts/check_llm.py`) |
| PayPal sandbox | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` |
| Tuago test mode | `TUAGO_SECRET_KEY` (`sk_test_...`), `TUAGO_WEBHOOK_SECRET` |
| Provider redirects and webhooks | `PUBLIC_BASE_URL` — where this app is reachable from the internet |
| Farmer payout rate | `USD_NGN_RATE` |
| Telegram | `TELEGRAM_BOT_TOKEN` |
| Operator console | `SECRET_KEY` — entered in the console, sent as `X-Admin-Key` |

Register `<PUBLIC_BASE_URL>/webhook/paypal` and `<PUBLIC_BASE_URL>/webhook/tuago`
with the two providers. Without webhooks the flow still works whenever the sponsor
returns to their page, which re-checks open payments.

### Deploy

`render.yaml` is a Render blueprint for the app and a PostgreSQL database.

## Tests

```bash
pytest
```

The tests cover the Claude tool loop, the PayPal and Tuago requests, both webhooks,
the release policy, both sponsorship rails, farmer payouts and the HTTP endpoints.
They use SQLite and never call an outside service. To run them on PostgreSQL, set
`TEST_DATABASE_URL` to a throwaway database.

## Limits to know about

- Funding a farmer disbursement is a bank transfer someone at Yieldra makes; Tuago
  has no payout API to automate it.
- Naira investor returns (the older investment flow) are calculated and queued for a
  manual transfer for the same reason.
- Web sponsors get their updates on their page, not by email.
- Farm photos are stored on local disk under `generated/evidence/`.
- The Render blueprint leaves out the Celery worker and Redis used by the scheduled
  harvest and report tasks.

## History

Yieldra began as an entry for the Global AI Hackathon Series with Qwen Cloud
(fractional farm investment with an autonomous supply chain). The sponsorship flow on
PayPal and Tuago, the Claude agent layer, milestone verification, farmer payouts, the
web pages and the test suite were added for *Build What's Next with PayPal and AI*.

## License

MIT — see [LICENSE](LICENSE). Bundled third-party files keep their own licenses:
AG Grid Community (MIT, `app/web/assets/vendor/`) and the Bricolage Grotesque and
Instrument Sans typefaces (SIL OFL 1.1, `app/web/assets/fonts/`).
