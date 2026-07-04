"""Application settings loaded and validated from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration. All values come from the environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Qwen / Alibaba Cloud
    dashscope_api_key: str = "sk-xxx"
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    # Database
    database_url: str = "postgresql+asyncpg://yieldra:password@localhost:5432/yieldra_db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Paystack
    paystack_secret_key: str = "sk_live_xxx"
    paystack_public_key: str = "pk_live_xxx"

    # Telegram Bot API
    telegram_bot_token: str = "xxx"
    telegram_use_polling: bool = True
    telegram_webhook_secret: str = "yieldra_webhook_verify"

    # App
    app_env: str = "development"
    secret_key: str = "change-this-in-production"
    allowed_origins: str = "http://localhost:3000"

    # Qwen model assignment per agent — Token Plan (Team Edition) available models.
    model_investment: str = "qwen3.7-max"
    model_logistics: str = "qwen3.7-max"
    model_offtake: str = "qwen3.7-max"
    model_advisory: str = "qwen3.6-flash"
    model_report: str = "qwen3.6-flash"
    model_vision: str = "qwen3.7-plus"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        """A key is a placeholder if empty or one of the .env.example stubs."""
        v = (value or "").strip().lower()
        return v in {"", "xxx", "sk-xxx", "sk_live_xxx", "pk_live_xxx", "change-this-in-production"}

    @property
    def paystack_live(self) -> bool:
        """True once a real Paystack secret key is configured (not a placeholder)."""
        return not self._is_placeholder(self.paystack_secret_key)

    @property
    def telegram_live(self) -> bool:
        """True once a real Telegram bot token is configured (not a placeholder)."""
        return not self._is_placeholder(self.telegram_bot_token)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url


@lru_cache
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    return Settings()


settings = get_settings()
