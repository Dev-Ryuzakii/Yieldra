FROM node:22-slim AS web-build
WORKDIR /web
COPY app/web/package*.json ./
RUN npm ci
COPY app/web ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY --from=web-build /web/dist ./app/web/dist
RUN pip install --upgrade pip && pip install -e .

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=3)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
