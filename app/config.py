from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str
    DATABASE_URL_SYNC: str  # psycopg2 — Alembic only
    TEST_DATABASE_URL: str = ""

    # ── Auth ──────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    REFRESH_TOKEN_HASH_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    # Confirmed by Arpit 2026-06-19: 480 min (8 hours)
    SESSION_INACTIVITY_MINUTES: int = 480
    SESSION_ABSOLUTE_EXPIRE_HOURS: int = 24
    REFRESH_REUSE_GRACE_SECONDS: int = 30

    # ── Password ──────────────────────────────────────────────────────────────
    BCRYPT_ROUNDS: int = 12

    # ── Brute-force protection ────────────────────────────────────────────────
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 5
    LOGIN_MAX_FAILURES: int = 10
    LOGIN_LOCKOUT_MINUTES: int = 15

    # ── Session housekeeping ──────────────────────────────────────────────────
    SESSION_RETENTION_DAYS: int = 30

    # ── App ───────────────────────────────────────────────────────────────────
    APP_ENV: Literal["development", "production", "test"] = "development"
    APP_TIMEZONE: str = "Asia/Kolkata"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("JWT_SECRET_KEY", "REFRESH_TOKEN_HASH_KEY", mode="after")
    @classmethod
    def validate_secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("Secret keys must be at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
