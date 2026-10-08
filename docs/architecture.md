# Yieldra architecture

Yieldra serves a React and TypeScript frontend from FastAPI. Afribase Auth handles sign-in, and Postgres stores users, farms, sponsorships, milestones, and payout obligations. Public visitors can browse farms; sponsor and operator pages use authenticated API routes.

## Sponsorship flow

1. The sponsor selects a farm and an amount in US dollars.
2. The backend creates four milestones and a PayPal order for the first tranche.
3. PayPal approval is captured by the return route or verified webhook. The capture activates the sponsorship.
4. The backend records a `pending_manual` farmer payout obligation in naira. It does not send a bank transfer.
5. When a farmer submits a photo, the milestone agent checks the image. Clear passes trigger the next PayPal charge; uncertain results wait for operator review. Each captured stage records another farmer obligation.
6. The operator console shows every milestone and payout status. The sponsor's page shows captured amounts and pending farmer payouts.

`PAYPAL_ENV=live` blocks new sponsorships and subsequent charges until a farmer settlement route is configured. This avoids collecting real money while the app cannot pay farmers. Sandbox and mock mode are for testing.

## Payment history

The database keeps older naira sponsorship and Tuago payout columns so historical rows can still be read. New Tuago checkout, webhook, payout and bank account routes have been removed. Investment creation through the former naira payment rail is unavailable.

## AI configuration

`app/agents/base.py` supports Anthropic and OpenAI-compatible APIs. AgentRouter's Anthropic-compatible endpoint is configured with `LLM_PROVIDER=anthropic`, `LLM_BASE_URL=https://co.agentrouter.org`, and a local `LLM_API_KEY`. Model names can be overridden with `MODEL_*` variables. Without a valid key, photo verification cannot run.

## Safety of state changes

PayPal events are verified with PayPal. Captured amounts are checked against milestone amounts before marking a stage paid. Later PayPal charges use stable idempotency references. The capture is committed before model-generated messages or notifications run. Payout obligations are recorded separately from sponsor payment status.
