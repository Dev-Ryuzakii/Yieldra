# Yieldra

Yieldra is a farm sponsorship app with a React and TypeScript frontend and a FastAPI backend. Sponsors select a farm and approve staged US dollar payments through PayPal. Farmers submit photos for each milestone; the AI checks the evidence and an operator can review uncertain results. Afribase manages sign-in and stores application data.

## Current payment setup

- **Sponsor checkout:** PayPal only, in US dollars. The first stage uses a PayPal order; later approved stages use a saved PayPal payment method.
- **Farmer payout ledger:** Each captured PayPal stage records a naira amount owed, using `USD_NGN_RATE`. The payout stays `pending_manual` until a settlement method is added. The rate is a ledger estimate, not an FX quote.
- **Live money safeguard:** `PAYPAL_ENV=live` pauses new sponsorships and later stage charges until farmer settlement is configured. Sandbox and mock mode remain available for the hackathon demo.
- **Legacy records:** Older naira sponsorship and Tuago payout fields are retained in the database for history. New Tuago checkout, webhook, bank account and payout actions are disabled. Naira investment creation is unavailable.

Do not promise farmers that a bank transfer has completed based on a PayPal capture. The operator console shows pending farmer obligations.

## Local setup

1. Copy `.env.example` to `.env` and fill in the Afribase, PayPal sandbox and other keys you intend to use. Never commit `.env` or put a service role key in browser code.
2. Start Postgres and Redis with `docker compose up -d` if you are running the local stack. Set `DATABASE_URL` to your local database or Afribase Postgres URL.
3. Install Python dependencies with `pip install -e '.[dev]'` and frontend dependencies with `cd app/web && npm install`.
4. Run `alembic upgrade head`, then `cd app/web && npm run build`.
5. Start the app with `uvicorn app.main:app --reload --port 8000`. Open `http://127.0.0.1:8000/`.

The operator account is selected by a verified email in `OPERATOR_EMAILS`. Other users can register through Afribase Auth and view their role dashboard. Set `AFRIBASE_URL` to the project root URL and `AFRIBASE_ANON_KEY` to the public anon key. Keep `AFRIBASE_SERVICE_ROLE_KEY` server-side for the publishing script only.

## AgentRouter

The AI client already supports Anthropic-compatible endpoints. `.env.example` uses AgentRouter's Anthropic-compatible base URL:

```env
LLM_PROVIDER=anthropic
LLM_BASE_URL=https://co.agentrouter.org
LLM_API_KEY=xxx
```

Replace `LLM_API_KEY` locally when the AgentRouter key is available. Set `MODEL_MILESTONE`, `MODEL_VISION`, `MODEL_REPORT` and other `MODEL_*` variables to model IDs enabled for that key. Run `python scripts/check_llm.py` to verify the connection. Until a working key and model are configured, evidence verification stays unavailable. AgentRouter also documents an OpenAI-compatible base at `https://co.agentrouter.org/v1`; the app supports that with `LLM_PROVIDER=openai`.

## Deployment

`render.yaml` describes an API and Postgres deployment. Set the public URL, Afribase Auth values, PayPal sandbox credentials, `LLM_API_KEY`, and a production `SECRET_KEY` in the host environment. Register `<PUBLIC_BASE_URL>/webhook/paypal` in PayPal. Do not enable PayPal live payments until farmer settlement has been implemented and verified.

## Checks

Run `pytest` for backend tests and `cd app/web && npm run build` for the frontend.
