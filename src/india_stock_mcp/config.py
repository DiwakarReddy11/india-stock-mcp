"""Application settings, loaded from environment variables (and an optional .env file)."""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field can be overridden by an environment variable with the same name, e.g. PORT=10000."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- Cache lifetimes (seconds) ---
    quote_cache_ttl: int = Field(default=60, ge=0)
    history_cache_ttl: int = Field(default=15 * 60, ge=0)
    info_cache_ttl: int = Field(default=6 * 60 * 60, ge=0)

    # --- Abuse protection (used in the next step) ---
    rate_limit_per_minute: int = Field(default=60, ge=1)

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_log_level(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value


settings = Settings()
