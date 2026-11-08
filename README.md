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

The sign-in page reads `GET /yieldra/meta`. Afribase reserves `/meta` for its own database API, so that path does not tell the page whether Yieldra auth is configured.

Set these on the API process. The browser does not receive the key. Docker Compose passes them through `env_file: .env` and does not override them. It does override `DATABASE_URL` to the Compose Postgres service, so an Afribase database URL in `.env` is not used by `docker compose up` unless you remove that override.

```env
AFRIBASE_URL=https://your-project.afribase.dev
AFRIBASE_ANON_KEY=your-public-anon-key
PUBLIC_BASE_URL=https://your-yieldra-host
SECRET_KEY=a-long-unique-secret
OPERATOR_EMAILS=operator@example.com
```

`AFRIBASE_SERVICE_ROLE_KEY` stays server-side for `scripts/publish_farm_covers.py` only. It is not used for sign-in.

In the Afribase project, allow `{PUBLIC_BASE_URL}/signin` as an auth redirect. Signup asks Afribase to send the confirmation link there. This checkout cannot see whether the live host has these variables set.

## Farmer signup

1. Open `/signin`.
2. Choose **Create account**.
3. Choose **I farm**. **I sponsor** creates a sponsor instead.
4. Enter your name, email and a password of at least 8 characters, then **Create account**.
5. If Afribase requires confirmation, open the email link and return to `/signin`. Then choose **Sign in**.
6. The dashboard role is `farmer`. A farm is listed only when a farm row already uses that user. This form does not create a farm and cannot grant operator, investor, buyer or logistics access. Operator access is still only the `OPERATOR_EMAILS` allowlist, checked on the server.

On each signed-in request the server reads `user_metadata.yieldra_role`. If it is `farmer` and the linked row is still a sponsor with no sponsorships, the row becomes `farmer`. A sponsor who already owns a sponsorship stays a sponsor. Operator, investor, buyer and logistics roles are not taken from that metadata. The dashboard asks `/account/dashboard` for the role; it is not stored in the browser session.

The six licensed demo farm covers are also kept in `app/web/public/assets/farm-covers/`. The app serves these copies for seeded farms, so cards remain visible if Afribase Storage has an outage. To repair the public Storage objects, set `AFRIBASE_SERVICE_ROLE_KEY` locally and run `.venv/bin/python scripts/publish_farm_covers.py`; it upserts each copy and verifies the public download.

## OpenAI

The chat and legacy milestone agents use OpenAI Chat Completions. Set these values on the app deployment:

```env
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-key
```

The default models are `gpt-5.4-mini` for general agents and `gpt-5.4` for photo diagnosis and milestone verification. Set `MODEL_*` variables to override individual agents. Run `python scripts/check_llm.py` to verify the key and model access. Until a working key is configured, evidence verification stays unavailable. The application server needs these variables; Afribase Auth and Storage alone do not run the AI agents.

## Cencori photo checks

Farmers submit a milestone photo from the dashboard. Operators review it in the console. The server calls Cencori's vision API and stores the result for a person. The model recommendation does not charge PayPal and does not pay the farmer.

Official references used for this integration:

- Vision API: https://cencori.com/docs/api/vision
- Integration contract: https://cencori.com/llm.txt
- Vision changelog: https://cencori.com/changelog/vision

The route is `POST https://api.cencori.com/v1/vision` with the server-side header `CENCORI_API_KEY`. The body sends `image_base64`, `mime_type`, `prompt`, `model`, and `response_format=json`. Cencori's contract says the embedded Python SDK is not published, so this app uses that HTTP route directly and does not send photos through an OpenAI-compatible chat client. The documented default vision model is `gpt-4o-mini`. The vision docs also list `gpt-4o`, `gpt-4-turbo`, `claude-sonnet-4-5`, `claude-3-5-sonnet-latest`, `claude-3-5-haiku-latest`, `gemini-2.5-pro`, `gemini-2.5-flash`, and `gemini-2.5-flash-lite`. Set `CENCORI_VISION_MODEL` only to a vision-capable model the Cencori project can route.

```env
CENCORI_API_KEY=your-project-key
CENCORI_BASE_URL=https://api.cencori.com
CENCORI_VISION_MODEL=gpt-4o-mini
CENCORI_VISION_MODE=live
CENCORI_VISION_TIMEOUT_SECONDS=25
```

`CENCORI_VISION_MODE=mock` skips the network call and stores a labeled demo result. It is not a crop judgment. With `CENCORI_VISION_MODE=live` and no key, the check is marked blocked and stays in human review. A missing key is not reported as a successful analysis.

The check compares the photo with the crop and the evidence text stored on that sponsorship milestone. It does not use a universal checklist. A photo cannot prove ownership, location, date, acreage, or that the whole milestone is complete. Any confidence number is labeled as model-reported, not as calibrated accuracy. Text in the photo is stored as untrusted data.

Operator approve, reject, and request-another-photo actions require a written rationale and do not change PayPal execution.

### Payment risk that remains

Telegram photos captioned `PROOF`, and the legacy "Check photo" form in the console, still use the older milestone agent. A confident result on that path can collect the next PayPal tranche. The new Cencori queue does not. Do not treat the two as the same control.

## Deployment

`render.yaml` describes an API and Postgres deployment. Set the public URL, Afribase Auth values, PayPal sandbox credentials, `OPENAI_API_KEY`, and a production `SECRET_KEY` in the host environment. Register `<PUBLIC_BASE_URL>/webhook/paypal` in PayPal. Do not enable PayPal live payments until farmer settlement has been implemented and verified.

## Checks

Run `pytest` for backend tests and `cd app/web && npm run build` for the frontend.
