# Yieldra architecture

Yieldra serves a React and TypeScript frontend from FastAPI. Afribase Auth handles sign-in, and Postgres stores users, farms, sponsorships, milestones, and payout obligations. Public visitors can browse farms; sponsor and operator pages use authenticated API routes.

## Sponsorship flow

1. The sponsor selects a farm and an amount in US dollars.
2. The backend creates four milestones and a PayPal order for the first tranche.
3. PayPal approval is captured by the return route or verified webhook. The capture activates the sponsorship.
4. The backend records a `pending_manual` farmer payout obligation in naira. It does not send a bank transfer.
5. A farmer can upload a milestone photo on the dashboard. Yieldra stores it, sends it to Cencori's vision route, and keeps the structured result for a person to review. That result never charges PayPal and never creates a payout.
6. The older path is still present. A Telegram photo captioned PROOF, or the legacy console photo form, can still collect the next PayPal tranche when that older check is confident. That automatic charge is a known risk; the new review queue does not use it.
7. The operator console shows every milestone, the separate AI photo review queue, and payout status. The sponsor's page shows captured amounts and pending farmer payouts.

`PAYPAL_ENV=live` blocks new sponsorships and subsequent charges until a farmer settlement route is configured. This avoids collecting real money while the app cannot pay farmers. Sandbox and mock mode are for testing.

## Payment history

The database keeps older naira sponsorship and Tuago payout columns so historical rows can still be read. New Tuago checkout, webhook, payout and bank account routes have been removed. Investment creation through the former naira payment rail is unavailable.

## AI configuration

`app/agents/base.py` supports Anthropic and OpenAI-compatible APIs. The default is OpenAI Chat Completions with `LLM_PROVIDER=openai`, `LLM_BASE_URL=https://api.openai.com/v1`, and `OPENAI_API_KEY`. General agents use `gpt-5.4-mini`; the legacy photo diagnosis and milestone verification agents use `gpt-5.4`. Model names can be overridden with `MODEL_*` variables. Without a valid key, that legacy verification cannot run.

Website milestone photos use a separate server-side client, `app/services/cencori_vision.py`. It calls Cencori `POST /v1/vision` with `CENCORI_API_KEY`. It does not send the image through the OpenAI chat client. The documented default vision model is `gpt-4o-mini` (`CENCORI_VISION_MODEL`). `CENCORI_VISION_MODE=mock` returns a labeled demo result and does not call Cencori. With mode `live` and no key, the check is stored as blocked and waits for a person. See the README for the official documentation links.

## Safety of state changes

PayPal events are verified with PayPal. Captured amounts are checked against milestone amounts before marking a stage paid. Later PayPal charges use stable idempotency references. The capture is committed before model-generated messages or notifications run. Payout obligations are recorded separately from sponsor payment status.

## Farm assignment

`Farm.farmer_id` is the only link between a farm and its farmer. Operators change it
from the **Farm assignments** panel on `/dashboard` (also on `/console`), backed by:

- `GET /console/farm-assignments`: operator only. Farms with their current owner,
  photo readiness and any reassignment blockers, plus the farmers that may be chosen
  (role `farmer`, signed in to Yieldra at least once, not an operator email). Emails
  are masked.
- `POST /console/farms/{farm_id}/assignment` with `farmer_id`, `expected_farmer_id`
  (the owner the operator saw), `reason` (10 to 500 characters) and `confirm: true`.
  Same-origin only. Repeating the current assignment returns `unchanged` and writes
  nothing. The write is a compare-and-set on `farmer_id`, so a stale or concurrent
  change returns 409 instead of overwriting.

A change is refused while the farm has a photo check pending or awaiting review, or a
tranche under review or awaiting payment. Each change writes a
`farm_assignment_events` row with the farm, previous and new farmer, operator, time and
reason. Sponsorships, PayPal records, farmer payout rows and photo evidence keep their
original owners.

Deploy the table with `alembic upgrade head` (revision `e2a7c4f19b38`). Preview the SQL
first with `alembic upgrade c4e8b1a07d21:e2a7c4f19b38 --sql`.
