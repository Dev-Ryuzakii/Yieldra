# Yieldra project notes

Read `README.md` and `docs/architecture.md` before changing payment flows.

The active sponsor rail is PayPal in US dollars. Each captured tranche records a pending farmer payout obligation; there is no active farmer settlement provider. Keep `PAYPAL_ENV=live` blocked until farmer settlement is implemented and verified. Historical Tuago columns and model values remain for old records, but no new Tuago checkout, webhook, bank or payout flow should be exposed.

Afribase Auth manages sign-in. Keep service role and JWT secrets server-side. The React and TypeScript frontend is built with Vite and served by FastAPI.

AI runs through `app/agents/base.py`. For AgentRouter, set `LLM_PROVIDER=anthropic`, `LLM_BASE_URL=https://co.agentrouter.org`, and a local `LLM_API_KEY`; select model IDs available to that key with `MODEL_*`. Do not commit credentials.

Run `.venv/bin/pytest -q` and `cd app/web && npm run build` after payment or frontend changes.
