from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str

    TESSERACT_CMD: str = ""
    TESSDATA_PREFIX: str = ""

    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_BYTES: int = Field(default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)  # 5MB default, max 50MB

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()