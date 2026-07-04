# CLAUDE.md — Yieldra Autonomous Build Specification

> **Project:** Yieldra  
> **Tagline:** Invest in a farm. Let AI run it.  
> **Hackathon:** Global AI Hackathon Series with Qwen Cloud (Track 4 — Autopilot Agent)  
> **Deadline:** July 9, 2026  
> **Prize:** $7,000 cash + $3,000 cloud credits  

---

## What is Yieldra?

Yieldra is an AI-powered fractional farm investment and autonomous supply chain platform for Africa. Urban investors buy shares in real farms. Yieldra's AI agents then run the entire operation — harvest coordination, cold storage booking, buyer matching, payment distribution — all triggered via WhatsApp. No manual coordination required.

**The gap Yieldra fills:** Enterprise supply chain AI exists (SAP, Walmart) but serves Fortune 500 companies. WhatsApp farming chatbots exist (Darli AI, Ulangizi) but only give advice — they take no action. Fractional farm investment exists (Afrik Farm, AcreTrader) but has no autonomous operations layer. Yieldra combines all three into one autonomous agent platform.

---

## Tech Stack

```
Backend:        Python 3.11+ (FastAPI)
AI Agents:      Qwen models via Alibaba Cloud Model Studio (OpenAI-compatible)
Database:       PostgreSQL 15
Cache/Queue:    Redis 7
WhatsApp:       WhatsApp Business API (via webhook)
Payments:       Paystack API
Deployment:     Alibaba Cloud ECS (required for hackathon submission)
Containers:     Docker + Docker Compose
Task Queue:     Celery + Redis
```

### Qwen model assignment per agent

| Agent | Model | Reason |
|---|---|---|
| Investment Agent | `qwen-max` | Complex financial reasoning |
| Harvest Logistics Agent | `qwen-max` | Multi-step tool use + decisions |
| Offtake Matching Agent | `qwen-max` | Negotiation + contract generation |
| Farmer Advisory Agent | `qwen-plus` | Multilingual (Yoruba/Pidgin/Hausa), fast |
| Portfolio Report Agent | `qwen-turbo` | Fast summaries, cheap |
| Crop Vision Agent | `qwen-vl-plus` | Multimodal — processes farm photos |

### API base URL (international — Singapore region)
```
https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

---

## Project Structure

```
yieldra/
├── CLAUDE.md                          # This file
├── README.md                          # Public-facing docs
├── docker-compose.yml                 # Full stack orchestration
├── .env.example                       # Environment variables template
│
├── app/
│   ├── main.py                        # FastAPI entrypoint
│   ├── config.py                      # Settings via pydantic-settings
│   ├── database.py                    # PostgreSQL async session
│   ├── redis_client.py                # Redis connection
│   │
│   ├── agents/                        # All AI agents live here
│   │   ├── base.py                    # BaseAgent class (shared Qwen client)
│   │   ├── investment_agent.py        # Agent 1: Farm investment + share tokenization
│   │   ├── logistics_agent.py         # Agent 2: Harvest + cold storage + truck booking
│   │   ├── offtake_agent.py           # Agent 3: Buyer matching + contract generation
│   │   ├── advisory_agent.py          # Agent 4: Multilingual farmer advisory
│   │   ├── report_agent.py            # Agent 5: Investor portfolio reports
│   │   └── vision_agent.py            # Agent 6: Crop photo diagnosis (multimodal)
│   │
│   ├── tools/                         # Agent tools (function calling)
│   │   ├── paystack.py                # Paystack payment + payout tools
│   │   ├── whatsapp.py                # Send WhatsApp messages
│   │   ├── cold_storage.py            # Mock cold room booking tool
│   │   ├── contracts.py               # PDF contract generation tool
│   │   └── weather.py                 # Weather/crop data lookup tool
│   │
│   ├── routers/                       # FastAPI route handlers
│   │   ├── webhooks.py                # WhatsApp webhook receiver
│   │   ├── farms.py                   # Farm registration + management
│   │   ├── investments.py             # Investor endpoints
│   │   ├── logistics.py               # Supply chain endpoints
│   │   └── health.py                  # /health endpoint for deployment proof
│   │
│   ├── models/                        # SQLAlchemy ORM models
│   │   ├── farm.py                    # Farm, FarmPlot
│   │   ├── investment.py              # Investment, InvestorShare
│   │   ├── harvest.py                 # Harvest, ColdStorageBooking
│   │   ├── offtake.py                 # OfftakeContract, Buyer
│   │   └── user.py                    # User (farmer / investor / logistics)
│   │
│   ├── schemas/                       # Pydantic request/response schemas
│   │   ├── farm.py
│   │   ├── investment.py
│   │   ├── harvest.py
│   │   └── user.py
│   │
│   ├── tasks/                         # Celery background tasks
│   │   ├── harvest_monitor.py         # Polls harvest readiness, triggers logistics
│   │   ├── payout_distributor.py      # Distributes returns to investors post-harvest
│   │   └── report_scheduler.py        # Weekly investor report generation
│   │
│   └── utils/
│       ├── logger.py                  # Structured logging
│       └── language.py                # Language detection helper (Yoruba/Pidgin/Hausa/EN)
│
├── migrations/                        # Alembic migrations
│   └── versions/
│
├── tests/
│   ├── test_agents/
│   ├── test_tools/
│   └── test_routers/
│
└── scripts/
    ├── seed_db.py                     # Seed demo data for hackathon demo
    └── test_whatsapp.py               # Local WhatsApp webhook simulator
```

---

## Environment Variables

Create `.env` from `.env.example`:

```env
# Qwen / Alibaba Cloud
DASHSCOPE_API_KEY=sk-xxx
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1

# Database
DATABASE_URL=postgresql+asyncpg://yieldra:password@localhost:5432/yieldra_db

# Redis
REDIS_URL=redis://localhost:6379/0

# Paystack
PAYSTACK_SECRET_KEY=sk_live_xxx
PAYSTACK_PUBLIC_KEY=pk_live_xxx

# WhatsApp Business API
WHATSAPP_TOKEN=xxx
WHATSAPP_PHONE_NUMBER_ID=xxx
WHATSAPP_VERIFY_TOKEN=yieldra_webhook_verify

# App
APP_ENV=development
SECRET_KEY=change-this-in-production
ALLOWED_ORIGINS=http://localhost:3000
```

---

## Phase-by-Phase Build Plan

Build phases in strict order. Complete and test each phase before moving to the next.

---

### Phase 1 — Project scaffold + database (Day 1)

**Goal:** Running FastAPI server with PostgreSQL, Redis, and all models migrated.

**Tasks:**
1. Create `pyproject.toml` with all dependencies (FastAPI, SQLAlchemy async, asyncpg, alembic, celery, redis, openai, pydantic-settings, httpx, python-dotenv, reportlab)
2. Create `docker-compose.yml` with services: `api`, `db` (postgres:15), `redis` (redis:7), `celery_worker`
3. Implement `app/config.py` using `pydantic-settings` — loads all env vars, validates on startup
4. Implement `app/database.py` — async SQLAlchemy engine + session factory
5. Implement all SQLAlchemy models in `app/models/` with proper relationships:
   - `User`: id, name, phone, role (farmer/investor/buyer/logistics), language_preference, created_at
   - `Farm`: id, name, farmer_id (FK User), location, crop_type, total_plots, available_plots, status (listed/funded/growing/harvesting/completed), created_at
   - `FarmPlot`: id, farm_id (FK), investor_id (FK User), share_percentage, amount_invested, status, created_at
   - `Harvest`: id, farm_id (FK), expected_date, actual_date, yield_kg, status (pending/ready/in_transit/stored/sold), created_at
   - `ColdStorageBooking`: id, harvest_id (FK), facility_name, booked_at, confirmed, temperature_celsius
   - `OfftakeContract`: id, harvest_id (FK), buyer_id (FK User), quantity_kg, price_per_kg, total_value, status (draft/signed/paid), pdf_url
   - `Investment`: id, farm_id (FK), investor_id (FK User), amount_ngn, shares, expected_return_ngn, actual_return_ngn, status
6. Run `alembic init migrations` and create initial migration
7. Implement `app/main.py` — FastAPI app, include all routers, CORS, lifespan startup
8. Implement `app/routers/health.py` — `GET /health` returns `{"status": "ok", "version": "1.0.0"}`
9. Test: `docker compose up` → `curl http://localhost:8000/health` returns 200

---

### Phase 2 — BaseAgent + Qwen client (Day 1–2)

**Goal:** Working Qwen API connection with tool-calling support.

**Tasks:**
1. Implement `app/agents/base.py`:
   ```python
   class BaseAgent:
       def __init__(self, model: str, system_prompt: str, tools: list = None)
       async def run(self, messages: list, context: dict = None) -> str
       async def run_with_tools(self, messages: list, available_tools: list) -> dict
   ```
   - Uses `openai` Python SDK with `base_url=QWEN_BASE_URL` and `api_key=DASHSCOPE_API_KEY`
   - Implements tool-calling loop: call model → if tool_use → execute tool → feed result back → repeat until final answer
   - Implements retry logic with exponential backoff (max 3 retries)
   - Logs all agent calls with model name, token usage, latency
2. Test: instantiate BaseAgent with `qwen-plus`, call with a simple message, verify response

---

### Phase 3 — Investment Agent (Day 2)

**Goal:** Agent that handles farm funding and investor share tracking.

**System prompt context:**
- You are Yieldra's Investment Agent. You manage farm investment transactions on behalf of investors.
- You can create investment records, verify Paystack payments, tokenize farm plot shares, and send WhatsApp confirmation to investors.
- Always confirm investment amounts before creating records. Investments above ₦500,000 require explicit investor re-confirmation.
- Respond in the investor's preferred language.

**Tools to implement in `app/tools/paystack.py`:**
```python
async def verify_payment(reference: str) -> dict  # Verify Paystack transaction
async def initiate_payout(recipient_code: str, amount: int, reason: str) -> dict
async def create_transfer_recipient(name: str, account_number: str, bank_code: str) -> dict
```

**Agent capabilities (`app/agents/investment_agent.py`):**
- `async def process_investment(investor_id, farm_id, amount_ngn, payment_reference)` — verify payment → create Investment + FarmPlot records → send WhatsApp confirmation
- `async def calculate_expected_returns(farm_id, investment_amount)` — based on farm crop type + historical yield data
- `async def distribute_returns(harvest_id)` — calculate each investor's share → initiate Paystack payouts → update Investment records

**Human-in-the-loop rule:** Any payout above ₦100,000 must pause and send WhatsApp message to investor asking for approval before executing.

---

### Phase 4 — Harvest Logistics Agent (Day 2–3)

**Goal:** Agent that autonomously coordinates harvest pickup, cold storage, and transport.

**System prompt context:**
- You are Yieldra's Harvest Logistics Agent. You coordinate the movement of harvested crops from farm to cold storage to market.
- You monitor harvest readiness reports from farmers, book cold storage slots, coordinate truck pickups, and confirm delivery with photo proof.
- Always confirm logistics plans with the farmer before booking. Never book a truck without farmer confirmation.
- Handle delays gracefully — if a truck is late, proactively notify all parties and rebook.

**Tools to implement in `app/tools/cold_storage.py`:**
```python
async def check_available_slots(location: str, date: str, quantity_kg: float) -> list
async def book_cold_storage(facility_id: str, harvest_id: str, date: str) -> dict
async def confirm_delivery(booking_id: str, proof_photo_url: str) -> dict
```

**Tools to implement in `app/tools/whatsapp.py`:**
```python
async def send_text_message(phone: str, message: str) -> dict
async def send_template_message(phone: str, template_name: str, params: list) -> dict
async def send_image_message(phone: str, image_url: str, caption: str) -> dict
```

**Agent capabilities (`app/agents/logistics_agent.py`):**
- `async def initiate_harvest_coordination(harvest_id)` — message farmer → get readiness confirmation → book cold storage → book truck → send logistics plan to all parties
- `async def handle_harvest_update(harvest_id, update_message, photo_url=None)` — process farmer WhatsApp update → update Harvest record → decide next action
- `async def monitor_active_harvests()` — called by Celery task every 6 hours — check all harvests in status `growing` → message farmers for readiness update

---

### Phase 5 — WhatsApp Webhook (Day 3)

**Goal:** Receive and route all inbound WhatsApp messages to the correct agent.

**Tasks:**
1. Implement `app/routers/webhooks.py`:
   - `GET /webhook/whatsapp` — verify webhook with `WHATSAPP_VERIFY_TOKEN`
   - `POST /webhook/whatsapp` — receive inbound messages, route to correct agent based on sender's role and message content
2. Implement `app/utils/language.py`:
   - `detect_language(text: str) -> str` — returns "yoruba", "pidgin", "hausa", or "english"
   - Use simple keyword detection first, fall back to asking Qwen to classify
3. Message routing logic:
   ```
   if sender.role == "farmer":
       → logistics_agent.handle_harvest_update() or advisory_agent.respond()
   elif sender.role == "investor":
       → investment_agent or report_agent
   elif sender.role == "buyer":
       → offtake_agent
   else:
       → onboarding flow (ask role, register user)
   ```
4. Implement `scripts/test_whatsapp.py` — local simulator that POSTs mock webhook payloads so you can test without a real WhatsApp connection

---

### Phase 6 — Farmer Advisory Agent (Day 3–4)

**Goal:** Multilingual agent that gives farmers crop advice in their language.

**System prompt context:**
- You are Yieldra's Farmer Advisory Agent. You support smallholder farmers across Nigeria.
- You must respond in the farmer's preferred language: Yoruba, Nigerian Pidgin, Hausa, or English.
- You can analyze crop photos to detect disease, give planting advice, answer questions about fertilizer and pests, and send weather alerts.
- Keep responses short and practical — most farmers are reading on basic smartphones.
- Never give advice that contradicts safe agricultural practices.

**Tools:**
```python
# app/tools/weather.py
async def get_weather_forecast(location: str, days: int = 7) -> dict
async def get_crop_calendar(crop_type: str, location: str) -> dict
```

**Agent capabilities (`app/agents/advisory_agent.py`):**
- `async def respond_to_farmer(farmer_id, message, photo_url=None)` — detect language → build context (farmer's crop type, location, farm status) → generate response → send via WhatsApp
- `async def diagnose_crop_photo(photo_url, crop_type, farmer_language)` — use `qwen-vl-plus` multimodal to analyze farm photo → return diagnosis + treatment recommendation in farmer's language
- `async def send_weather_alert(farm_id)` — check 7-day forecast → if rain/drought alert → proactively message farmer in their language

**Language examples to handle:**
- Yoruba: "Kini mo le se fun arun iresi mi?" (What can I do for my rice disease?)
- Pidgin: "My farm dey give me wahala, the leaves don turn yellow"
- Hausa: "Gonar nawa tana da matsala"

---

### Phase 7 — Offtake Matching Agent (Day 4)

**Goal:** Agent that matches ready harvests to verified buyers and generates contracts.

**System prompt context:**
- You are Yieldra's Offtake Matching Agent. You connect farm harvests with verified buyers.
- You match based on: crop type, quantity, quality grade, location proximity, and buyer's payment history.
- You generate draft contracts and send for both parties to confirm via WhatsApp.
- Never finalize a contract without explicit confirmation from both farmer and buyer.
- Contracts above ₦500,000 total value require a 48-hour review period before signing.

**Tools to implement in `app/tools/contracts.py`:**
```python
async def generate_contract_pdf(harvest_id, buyer_id, terms: dict) -> str  # returns PDF URL
async def send_contract_for_signing(contract_id, farmer_phone, buyer_phone) -> dict
```

**Agent capabilities (`app/agents/offtake_agent.py`):**
- `async def find_buyers(harvest_id)` — query available buyers by crop type + location → rank by payment history + price offered
- `async def initiate_deal(harvest_id, buyer_id)` — generate draft contract → send to farmer and buyer via WhatsApp → wait for confirmation
- `async def handle_negotiation(contract_id, party, message)` — process counter-offer or acceptance → update contract → notify other party

---

### Phase 8 — Portfolio Report Agent (Day 4–5)

**Goal:** Agent that generates and delivers weekly investment performance reports.

**System prompt context:**
- You are Yieldra's Portfolio Report Agent. You keep investors informed about their farm investments.
- Write reports in clear, friendly language. Include specific numbers — yields, timelines, returns.
- If a farm is underperforming or facing issues, be honest but constructive.
- Always end with the estimated payout date.

**Agent capabilities (`app/agents/report_agent.py`):**
- `async def generate_investor_report(investor_id) -> str` — pull all active investments → aggregate farm status, harvest progress, logistics updates → generate plain-language summary
- `async def send_weekly_reports()` — called by Celery task every Monday 8am WAT — generate and send report to all active investors via WhatsApp

---

### Phase 9 — Celery background tasks (Day 5)

**Goal:** Autonomous task scheduling so agents run without human triggers.

**Tasks to implement in `app/tasks/`:**

```python
# harvest_monitor.py
@celery.task
def monitor_harvests():
    """Runs every 6 hours. Checks all growing farms, messages farmers for status."""

# payout_distributor.py  
@celery.task
def distribute_completed_payouts():
    """Runs daily at 9am WAT. Finds completed harvests with unpaid returns, distributes via Paystack."""

# report_scheduler.py
@celery.task
def send_weekly_investor_reports():
    """Runs every Monday at 8am WAT. Sends portfolio reports to all active investors."""
```

Configure Celery beat schedule in `app/config.py`.

---

### Phase 10 — Demo data + deployment prep (Day 5–6)

**Goal:** Seed realistic demo data and deploy to Alibaba Cloud ECS.

**Tasks:**
1. Implement `scripts/seed_db.py` with demo data:
   - 3 farmers (one Yoruba, one Hausa, one English-speaking)
   - 3 farms (cassava in Ogun, maize in Benue, tomatoes in Kano)
   - 5 investors with active investments
   - 2 buyers (a Lagos restaurant and an export company)
   - 1 harvest in progress (logistics agent can demo live)
   - 1 completed harvest with pending payout distribution
2. Create `Dockerfile` for the FastAPI app
3. Create production `docker-compose.prod.yml` for Alibaba ECS
4. Deploy to Alibaba Cloud ECS:
   - Use Singapore region (required for hackathon international endpoint)
   - Expose port 8000
   - Set all env vars on the instance
5. Record 60-second proof-of-deployment video showing:
   - `docker ps` on the ECS instance showing all containers running
   - `curl https://your-domain/health` returning 200
   - One live agent call via the API

---

### Phase 11 — Demo script + submission (Day 6–7)

**Goal:** A clean, judge-ready 3-minute demo video and complete Devpost submission.

**Demo video flow (3 minutes exactly):**
1. (0:00–0:30) Show the problem: "40% post-harvest loss in Africa. $14B wasted annually."
2. (0:30–1:00) Investor flow: POST to `/investments` → watch Investment Agent verify payment → see WhatsApp confirmation sent
3. (1:00–1:45) Farmer flow: Send WhatsApp message in Yoruba → watch Advisory Agent respond in Yoruba → send a crop photo → watch Vision Agent diagnose
4. (1:45–2:30) Logistics flow: Trigger harvest ready → watch Logistics Agent book cold storage → generate truck booking → notify all parties
5. (2:30–3:00) Show architecture diagram + Alibaba Cloud ECS dashboard proving deployment

**Devpost submission checklist:**
- [ ] Public GitHub repo with MIT license in root
- [ ] Architecture diagram (PNG) in `/docs/architecture.png`
- [ ] Proof of Alibaba Cloud deployment (60s video link)
- [ ] 3-minute demo video on YouTube (unlisted is fine)
- [ ] Track 4 selected
- [ ] Blog post published (optional but earns $500 extra)

---

## Agent Interaction Flow (how it all connects)

```
Investor pays via Paystack
        ↓
Investment Agent verifies → creates FarmPlot → sends WhatsApp confirmation
        ↓
Farm reaches harvest readiness
        ↓
Celery task triggers Harvest Monitor every 6h
        ↓
Logistics Agent messages farmer in their language
        ↓
Farmer confirms via WhatsApp (in Yoruba/Pidgin/Hausa/English)
        ↓
Logistics Agent books cold storage + truck → notifies all parties
        ↓
Offtake Agent matches harvest to buyer → generates contract PDF
        ↓
Both parties confirm via WhatsApp
        ↓
Buyer pays → Investment Agent distributes returns to all investors via Paystack
        ↓
Report Agent sends payout confirmation + portfolio update to each investor
```

---

## Judging Criteria Alignment

| Criterion | Weight | How Yieldra scores |
|---|---|---|
| Technical depth & engineering | 30% | 6 specialized Qwen agents, tool-calling loops, async Python, Celery scheduling, multimodal vision |
| Innovation & AI creativity | 30% | First platform combining fractional farm investment + autonomous supply chain + WhatsApp in African languages |
| Problem value & impact | 25% | $14B post-harvest loss problem, 38M Nigerian smallholder farmers, real Paystack integration |
| Presentation & documentation | 15% | Architecture diagram, seeded demo data, clean README, blog post |

---

## Key Rules for Claude Code

- Never hardcode API keys — always use environment variables
- All database operations must be async (use `asyncpg` + SQLAlchemy async)
- Every agent must log: model used, tokens consumed, latency, tool calls made
- WhatsApp messages must be short (max 3 sentences) — farmers are on basic phones
- All monetary amounts stored in kobo (smallest Naira unit) in the database, converted to Naira only in responses
- Human-in-the-loop checkpoints are non-negotiable — never skip approval gates
- Every tool must have a mock/fallback mode for local development (when `APP_ENV=development`)
- All agent system prompts must explicitly mention "Respond in the user's language" for multilingual support

---

## Commands

```bash
# Setup
cp .env.example .env
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed_db.py

# Development
docker compose up              # Full stack
uvicorn app.main:app --reload  # API only (hot reload)
celery -A app.tasks worker --loglevel=info  # Worker only

# Test
pytest tests/ -v
python scripts/test_whatsapp.py  # Simulate WhatsApp messages

# Deploy
docker compose -f docker-compose.prod.yml up -d
```