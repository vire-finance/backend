from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    # Web OAuth client ID used to verify the audience of Google ID tokens.
    FIREBASE_SERVICE_ACCOUNT: str = ""
    FIREBASE_PROJECT_ID: str = ""
    GOOGLE_CLIENT_ID: str = ""
    SECRET_KEY: str

    TESSERACT_CMD: str = ""
    TESSDATA_PREFIX: str = ""

    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_BYTES: int = Field(default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)  # 5MB default, max 50MB
    OCR_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=120)
    OCR_MAX_IMAGE_PIXELS: int = Field(default=25_000_000, ge=1, le=100_000_000)
    # None preserves legacy LLM_ENDPOINT opt-in; "disabled" explicitly stops calls.
    LLM_PROVIDER: str | None = None
    LLM_BASE_URL: str = ""
    LLM_TIMEOUT: float = Field(default=30, ge=1, le=120)
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
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
