"""Application settings loaded and validated from environment variables."""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default model per agent, by LLM provider. Any of them can be overridden with the
# matching MODEL_* environment variable.
_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "model_investment": "claude-sonnet-5-5",
        "model_logistics": "claude-sonnet-5-5",
        "model_offtake": "claude-sonnet-5-5",
        "model_advisory": "claude-haiku-4-5-20251001",
        "model_report": "claude-haiku-4-5-20251001",
        "model_vision": "claude-sonnet-5-5",
        "model_milestone": "claude-sonnet-5-5",
    },
    # OpenAI-compatible endpoints (Qwen on Alibaba Cloud Model Studio by default).
    "openai": {
        "model_investment": "qwen3.7-max",
        "model_logistics": "qwen3.7-max",
        "model_offtake": "qwen3.7-max",
        "model_advisory": "qwen3.6-flash",
        "model_report": "qwen3.6-flash",
        "model_vision": "qwen3.7-plus",
        "model_milestone": "qwen3.7-plus",
    },
}


class Settings(BaseSettings):
    """Central configuration. All values come from the environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=("settings_",),
    )

    # LLM provider.
    #   anthropic — Claude through the Anthropic Messages API. Set LLM_BASE_URL to use
    #               an Anthropic-compatible gateway instead of api.anthropic.com.
    #   openai    — any OpenAI-compatible chat-completions endpoint (Qwen, etc.).
    llm_provider: str = "anthropic"
    llm_api_key: str = "xxx"
    llm_base_url: str = ""
    llm_max_tokens: int = 1024

    # Legacy Qwen / Alibaba Cloud settings. Used only when LLM_PROVIDER=openai and the
    # generic LLM_API_KEY / LLM_BASE_URL are not set.
    dashscope_api_key: str = "sk-xxx"
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    # Database
    database_url: str = "postgresql+asyncpg://yieldra:password@localhost:5432/yieldra_db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Tuago (naira leg: naira sponsors pay in, farmers are paid out by split settlement).
    # The key decides the environment: sk_test_... is Tuago's sandbox, sk_live_... is real.
    tuago_secret_key: str = "xxx"
    tuago_webhook_secret: str = "xxx"
    tuago_base_url: str = "https://api.gettuago.com"
    # Yieldra's cut of each naira collection, in basis points (0 = farmer gets it all,
    # less Tuago's own fee).
    tuago_platform_fee_bps: int = 0

    # PayPal (international leg: sponsor payments). PAYPAL_ENV is "sandbox" or "live".
    paypal_client_id: str = "xxx"
    paypal_client_secret: str = "xxx"
    paypal_env: str = "sandbox"
    # Id of the webhook registered on the PayPal app; needed to verify PayPal webhooks.
    paypal_webhook_id: str = "xxx"

    # Naira paid to the farmer per US dollar of a PayPal tranche. Set this yourself:
    # the default is only a placeholder so development runs.
    usd_ngn_rate: float = 1500.0

    # Telegram Bot API
    telegram_bot_token: str = "xxx"
    telegram_use_polling: bool = True
    telegram_webhook_secret: str = "yieldra_webhook_verify"

    # App
    app_env: str = "development"
    secret_key: str = "change-this-in-production"
    allowed_origins: str = "http://localhost:3000"
    # Public address of this API. PayPal sends the sponsor back here after approval.
    public_base_url: str = "http://localhost:8000"
    # Contact email Tuago shows on the checkouts Yieldra itself pays (farmer payouts).
    operator_email: str = "operator@example.com"
    afribase_url: str = ""
    afribase_anon_key: str = ""
    # Comma-separated verified emails allowed into the operator console.
    operator_emails: str = ""

    @property
    def afribase_ready(self) -> bool:
        return bool(self.afribase_url.strip() and self.afribase_anon_key.strip())

    # Milestone verification thresholds (model confidence, 0-1).
    #   >= release  -> the tranche is charged automatically
    #   >= review   -> held for a human reviewer
    #   below       -> rejected; the farmer is asked for a clearer photo
    milestone_release_confidence: float = 0.75
    milestone_review_confidence: float = 0.50

    # Model assignment per agent. Blank means "use the provider default" above.
    model_investment: str = ""
    model_logistics: str = ""
    model_offtake: str = ""
    model_advisory: str = ""
    model_report: str = ""
    model_vision: str = ""
    model_milestone: str = ""

    @field_validator("database_url", mode="before")
    @classmethod
    def _async_database_url(cls, value: str) -> str:
        """Hosts such as Render hand out postgres:// URLs; the app needs the asyncpg driver."""
        value = str(value or "")
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix):]
        return value

    @model_validator(mode="after")
    def _fill_model_defaults(self) -> "Settings":
        self.llm_provider = (self.llm_provider or "anthropic").strip().lower()
        if self.llm_provider not in _DEFAULT_MODELS:
            raise ValueError(
                f"LLM_PROVIDER must be one of {sorted(_DEFAULT_MODELS)}, got {self.llm_provider!r}"
            )
        for field, default in _DEFAULT_MODELS[self.llm_provider].items():
            if not (getattr(self, field) or "").strip():
                setattr(self, field, default)
        return self

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        """A key is a placeholder if empty or one of the .env.example stubs."""
        v = (value or "").strip().lower()
        return v in {"", "xxx", "sk-xxx", "sk_live_xxx", "pk_live_xxx", "change-this-in-production"}

    # -- LLM -----------------------------------------------------------------
    @property
    def resolved_llm_api_key(self) -> str:
        """The key the agents send. Falls back to the legacy Qwen key for provider=openai."""
        if not self._is_placeholder(self.llm_api_key):
            return self.llm_api_key
        if self.llm_provider == "openai":
            return self.dashscope_api_key
        return self.llm_api_key

    @property
    def resolved_llm_base_url(self) -> str | None:
        """Custom endpoint, or None to use the provider SDK's default."""
        if self.llm_base_url.strip():
            return self.llm_base_url.strip()
        if self.llm_provider == "openai":
            return self.qwen_base_url
        return None

    @property
    def llm_live(self) -> bool:
        """True once a real LLM key is configured (not a placeholder)."""
        return not self._is_placeholder(self.resolved_llm_api_key)

    # -- Payments / channels -------------------------------------------------
    @property
    def tuago_live(self) -> bool:
        """True once a real Tuago secret key is configured (test or live)."""
        return self.tuago_secret_key.startswith(("sk_test_", "sk_live_"))

    @property
    def tuago_test_mode(self) -> bool:
        """True in mock mode or with a Tuago sandbox key: payments can be simulated."""
        return not self.tuago_secret_key.startswith("sk_live_")

    @property
    def paypal_live(self) -> bool:
        """True once real PayPal REST credentials are configured (sandbox or live)."""
        return not (
            self._is_placeholder(self.paypal_client_id)
            or self._is_placeholder(self.paypal_client_secret)
        )

    @property
    def paypal_api_base(self) -> str:
        if self.paypal_env.strip().lower() == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

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
