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

The six licensed demo farm covers are also kept in `app/web/public/assets/farm-covers/`. The app serves these copies for seeded farms, so cards remain visible if Afribase Storage has an outage. To repair the public Storage objects, set `AFRIBASE_SERVICE_ROLE_KEY` locally and run `.venv/bin/python scripts/publish_farm_covers.py`; it upserts each copy and verifies the public download.

## OpenAI

The AI agents use OpenAI Chat Completions. Set these values on the app deployment:

```env
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-key
```

The default models are `gpt-5.4-mini` for general agents and `gpt-5.4` for photo diagnosis and milestone verification. Set `MODEL_*` variables to override individual agents. Run `python scripts/check_llm.py` to verify the key and model access. Until a working key is configured, evidence verification stays unavailable. The application server needs these variables; Afribase Auth and Storage alone do not run the AI agents.

## Deployment

`render.yaml` describes an API and Postgres deployment. Set the public URL, Afribase Auth values, PayPal sandbox credentials, `OPENAI_API_KEY`, and a production `SECRET_KEY` in the host environment. Register `<PUBLIC_BASE_URL>/webhook/paypal` in PayPal. Do not enable PayPal live payments until farmer settlement has been implemented and verified.

## Checks

Run `pytest` for backend tests and `cd app/web && npm run build` for the frontend.
