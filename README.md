# Yieldra

> **Invest in a farm. Let AI run it.**

Yieldra is an AI-powered fractional farm investment and autonomous supply chain
platform for Africa. Urban investors buy shares in real farms. Yieldra's AI
agents then run the entire operation — harvest coordination, cold storage
booking, buyer matching, payment distribution — all triggered via WhatsApp.

Built for the Global AI Hackathon Series with Qwen Cloud (Track 4 — Autopilot Agent).

## Architecture

- **Backend:** Python 3.11+ / FastAPI (async)
- **AI agents:** Qwen models via Alibaba Cloud Model Studio (OpenAI-compatible)
- **Database:** PostgreSQL 15 (async, SQLAlchemy 2.0)
- **Cache/queue:** Redis 7 + Celery
- **Channels:** WhatsApp Business API
- **Payments:** Paystack

### Agents

| Agent | Model | Role |
|---|---|---|
| Investment | `qwen-max` | Farm funding + share tokenization |
| Harvest Logistics | `qwen-max` | Cold storage + truck coordination |
| Offtake Matching | `qwen-max` | Buyer matching + contracts |
| Farmer Advisory | `qwen-plus` | Multilingual crop advice |
| Portfolio Report | `qwen-turbo` | Investor reports |
| Crop Vision | `qwen-vl-plus` | Photo disease diagnosis |

## Quick start

```bash
cp .env.example .env          # fill in keys (dev works with mocks)
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed_db.py
uvicorn app.main:app --reload
curl http://localhost:8000/health
```

Or run the full stack:

```bash
docker compose up
```

## Development mode

Every external tool (Paystack, WhatsApp, cold storage, weather) has a mock
fallback when `APP_ENV=development`, so the platform runs end-to-end with no
real credentials.

## License

MIT — see [LICENSE](LICENSE).
