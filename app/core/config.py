from pydantic import Field, SecretStr, field_validator, model_validator
from urllib.parse import urlsplit
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from pydantic import AliasChoices

class Settings(BaseSettings):
    APP_ENV: Literal["development", "production"] = "development"

    DEV_DATABASE_URL: str = ""
    PROD_DATABASE_URL: str = ""

    DEV_LLM_PROVIDER: str = "ollama"
    PROD_LLM_PROVIDER: str = "groq"

    DEV_LLM_MODEL: str = "qwen3:1.7b"
    PROD_LLM_MODEL: str = "qwen/qwen3.8-27b"

    DEV_LLM_BASE_URL: str = "http://localhost:11434"
    PROD_LLM_BASE_URL: str = ""

    PROD_LLM_API_KEY: str = ""
    DATABASE_URL: str = ""
    # Web OAuth client ID used to verify the audience of Google ID tokens.
    FIREBASE_SERVICE_ACCOUNT: str = ""
    FIREBASE_PROJECT_ID: str = ""
    GOOGLE_CLIENT_ID: str = ""
    SECRET_KEY: str

    TESSERACT_CMD: str = ""
    TESSDATA_PREFIX: str = ""

    UPLOAD_DIR: str = "uploads"
    # Server-only Storage credentials. Development never requires or uses them.
    SUPABASE_URL: str = ""
    SUPABASE_SECRET_KEY: SecretStr = Field(default=SecretStr(""), repr=False)
    STORAGE_TIMEOUT_SECONDS: float = Field(default=15, ge=1, le=30)
    MAX_UPLOAD_SIZE_BYTES: int = Field(default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)  # 5MB default, max 50MB
    OCR_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=120)
    OCR_MAX_IMAGE_PIXELS: int = Field(default=25_000_000, ge=1, le=100_000_000)
    OCR_MAX_CONCURRENT_JOBS: int = Field(default=2, ge=1, le=8)
    OCR_JOB_TIMEOUT_SECONDS: int = Field(default=90, ge=1, le=300)
    OCR_STALE_JOB_SECONDS: int = Field(default=600, ge=60, le=3600)
    # Process-local protection for the single-instance local backend. Shared
    # enforcement belongs to the future deployment configuration.
    HTTP_MAX_JSON_BODY_BYTES: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024)
    HTTP_MULTIPART_OVERHEAD_BYTES: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    HTTP_BODY_TIMEOUT_SECONDS: float = Field(default=30, ge=5, le=120)
    HTTP_MAX_CONCURRENT_UPLOADS: int = Field(default=2, ge=1, le=8)
    RATE_LOGIN_REQUESTS_PER_MINUTE: int = Field(default=20, ge=1, le=120)
    RATE_UPLOAD_REQUESTS_PER_MINUTE: int = Field(default=20, ge=1, le=120)
    RATE_AI_REQUESTS_PER_MINUTE: int = Field(default=6, ge=1, le=60)
    LLM_MAX_CONCURRENT_REQUESTS: int = Field(default=1, ge=1, le=4)
    # None preserves legacy LLM_ENDPOINT opt-in; "disabled" explicitly stops calls.
    LLM_PROVIDER: str | None = None
    LLM_BASE_URL: str = ""
    # False disables Qwen3 reasoning; null omits the option for other runtimes.
    LLM_THINKING: bool | None = False
    LLM_TIMEOUT: float = Field(default=30, ge=1, le=120)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=300, ge=128, le=600)
    LLM_NUM_THREADS: int = Field(default=4, ge=1, le=16)
    LLM_KEEP_ALIVE: str = Field(default="5m", pattern=r"^(?:0|[1-9][0-9]*(?:s|m|h))$")
    AI_INSIGHT_CACHE_TTL_SECONDS: int = Field(default=300, ge=1, le=3600)
    AI_INSIGHT_FAILURE_TTL_SECONDS: int = Field(default=30, ge=1, le=300)
    AI_SPENDING_INCREASE_PERCENT: float = Field(default=25, ge=1, le=1000)
    AI_HIGH_UTILIZATION_PERCENT: float = Field(default=80, ge=1, le=100)
    AI_CONCENTRATION_PERCENT: float = Field(default=60, ge=1, le=100)
    AI_LARGE_TRANSACTION_MULTIPLIER: float = Field(default=2, ge=1.1, le=100)
    AI_MIN_BASELINE_TRANSACTIONS: int = Field(default=3, ge=1, le=100)
    LLM_ENDPOINT: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5070",

    ]

    @field_validator("LLM_THINKING", mode="before")
    @classmethod
    def optional_thinking(cls, value):
        return None if isinstance(value, str) and value.strip().lower() == "null" else value

    @model_validator(mode="after")
    def valid_ocr_recovery_deadline(self):
        if self.OCR_STALE_JOB_SECONDS <= self.OCR_JOB_TIMEOUT_SECONDS:
            raise ValueError("OCR_STALE_JOB_SECONDS must exceed OCR_JOB_TIMEOUT_SECONDS.")
        if (self.APP_ENV == "production" and self.OCR_STALE_JOB_SECONDS
                <= self.OCR_JOB_TIMEOUT_SECONDS + self.STORAGE_TIMEOUT_SECONDS):
            raise ValueError("OCR_STALE_JOB_SECONDS must exceed OCR and Storage timeouts in production.")
        return self

    @model_validator(mode="after")
    def select_environment(self):
        if self.APP_ENV == "production":
            if not self.PROD_DATABASE_URL:
                raise ValueError("PROD_DATABASE_URL is required in production")
            if not self.PROD_LLM_API_KEY:
                raise ValueError("PROD_LLM_API_KEY is required in production")
            endpoint = urlsplit(self.SUPABASE_URL)
            if (endpoint.scheme != "https" or not endpoint.hostname
                    or endpoint.username or endpoint.password
                    or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
                raise ValueError("SUPABASE_URL must be an HTTPS project root URL in production")
            if not self.SUPABASE_SECRET_KEY.get_secret_value():
                raise ValueError("SUPABASE_SECRET_KEY is required in production (backend only)")

            self.DATABASE_URL = self.PROD_DATABASE_URL
            self.LLM_PROVIDER = self.PROD_LLM_PROVIDER
            self.LLM_MODEL = self.PROD_LLM_MODEL
            self.LLM_BASE_URL = self.PROD_LLM_BASE_URL
            self.LLM_API_KEY = self.PROD_LLM_API_KEY

        else:
            if not self.DEV_DATABASE_URL:
                raise ValueError("DEV_DATABASE_URL is required in development")

            self.DATABASE_URL = self.DEV_DATABASE_URL
            self.LLM_PROVIDER = self.DEV_LLM_PROVIDER
            self.LLM_MODEL = self.DEV_LLM_MODEL
            self.LLM_BASE_URL = self.DEV_LLM_BASE_URL
            self.LLM_API_KEY = ""

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

settings = Settings()


# GitHub Pages frontend origin must be allowed in production, including when Render supplies CORS_ORIGINS.
if settings.APP_ENV == "production" and "https://vire-finance.github.io" not in settings.CORS_ORIGINS:
    settings.CORS_ORIGINS.append("https://vire-finance.github.io")
